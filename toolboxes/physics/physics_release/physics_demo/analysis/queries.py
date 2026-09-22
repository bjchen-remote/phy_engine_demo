"""Predeclared measurements, bounded planning, and evidence-based query answers.

Observers own collection; this module owns the public definitions and reductions.
No query reads video pixels or the decimated presentation trajectory.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from physics_demo.core.math3d import finite_number, finite_vec

MAX_QUERIES = 16
MAX_OBSERVATION_SCALARS = 250_000
MAX_OBSERVATION_WORK = 50_000_000
MAX_MEASUREMENT_FILE_BYTES = 32_000_000
METRIC_FIELDS = {
    "spread_radius": {"type", "entity", "plane", "origin"},
    "centroid": {"type", "entity", "axis"},
    "speed": {"type", "entity"},
    "center_distance": {"type", "entities"},
    "surface_gap": {"type", "entities"},
    "volume_ratio": {"type", "entity"},
    "max_displacement": {"type", "entity"},
    "max_edge_strain": {"type", "entity"},
    **{kind: {"type", "entity"} for kind in ("angular_speed", "rotational_energy", "axis_tilt")},
    "angular_momentum": {"type", "entity", "axis"},
    **{kind: {"type", "connection"} for kind in ("connection_length", "connection_extension", "spring_force", "spring_energy")},
}
CAPABILITIES = {
    "declaration": "scene.queries before prepare/simulate; physics_query retrieves declared IDs only",
    "query_types": ["threshold", "series", "nbody_stability"],
    "metrics": sorted(METRIC_FIELDS),
    "operators": ["gte", "lte"],
    "max_queries": MAX_QUERIES,
    "max_observation_scalars": MAX_OBSERVATION_SCALARS,
    "max_observation_work": MAX_OBSERVATION_WORK,
    "sampling": "t=0 and every completed solver macro step, full state, float64; independent of video FPS/decimation",
    "spread_radius": "maximum projected particle-center distance from an explicit fixed origin; includes airborne particles",
    "speed": "norm of entity centroid velocity, not mean individual particle speed",
    "surface_gap": "sphere: center distance minus radii; axis-aligned boxes: maximum signed axis gap; slider: x edge gap",
    "stability": "finite-window sampled radius/separation criteria with whole-window energy/momentum checks; no long-term stability proof",
    "mesh_metrics": "volume_ratio is signed current/rest volume; max_edge_strain is max abs(length/rest-1); max_displacement is maximum vertex travel from the initial world position, including translation. Mesh centroid/speed are unweighted vertex means.",
    "connection_metrics": "Connection length/extension are metres; spring_force is signed k*(length-rest)+c*axial_relative_speed in N (positive tension), spring_energy is 0.5*k*(length-rest)^2 in J. Declare a connection ID; these are ideal constitutive values, not rod/rope impulses or calibrated stresses.",
}


def digest(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def validate_queries(scene: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize only present declarations, preserving old no-query scenes."""
    if "queries" not in scene:
        return []
    errors: list[dict[str, Any]] = []

    def issue(code: str, path: str, message: str) -> None:
        errors.append({"code": code, "path": path, "message": message})

    queries = scene["queries"]
    if not isinstance(queries, list) or len(queries) > MAX_QUERIES:
        issue("query_limit", "queries", f"queries must be an array of at most {MAX_QUERIES} declarations.")
        return errors
    entities = {entity["id"]: entity for entity in scene["entities"]}
    seen: set[str] = set()
    for index, query in enumerate(queries):
        path = f"queries[{index}]"
        if not isinstance(query, dict):
            issue("query_type", path, "Every query must be an object.")
            continue
        ident = query.get("id")
        if not isinstance(ident, str) or not ident.strip() or len(ident) > 128:
            issue("query_id", path + ".id", "id must be a non-empty string of at most 128 characters.")
        elif ident in seen:
            issue("duplicate_query_id", path + ".id", "Query IDs must be unique.")
        else:
            seen.add(ident)
        kind = query.get("type")
        if not isinstance(kind, str) or kind not in {"threshold", "series", "nbody_stability"}:
            issue("query_kind", path + ".type", "Use threshold, series, or nbody_stability.")
            continue
        allowed = {"id", "type", "metric"}
        if kind == "threshold":
            allowed |= {"operator", "value", "hold_for"}
        elif kind == "nbody_stability":
            allowed = {"id", "type", "max_radius", "min_separation"}
        for key in set(query) - allowed:
            issue("unknown_field", path + "." + key, f"Unknown query field {key!r}.")
        if kind == "nbody_stability":
            bodies = [e for e in entities.values() if e["type"] == "point_mass"]
            closed = (len(bodies) >= 2 and scene["interactions"]["mutual_gravity"]
                      and scene["interactions"]["gravity_G"] > 0
                      and not any(e["fixed"] for e in bodies)
                      and not any(e["id"] in f["targets"] for e in bodies for f in scene["force_fields"]))
            if not closed:
                issue("stability_requires_closed_nbody", path, "Stability requires at least two free, mutually gravitating point masses with positive G and no targeted external fields.")
            for key in ("max_radius", "min_separation"):
                if not finite_number(query.get(key)) or not 0 < query[key] <= 1e6:
                    issue("query_range", path + "." + key, key + " must be explicitly set in (0, 1e6] metres.")
                else:
                    query[key] = float(query[key])
            continue
        metric = query.get("metric")
        if not isinstance(metric, dict) or not isinstance(metric.get("type"), str) or metric["type"] not in METRIC_FIELDS:
            issue("query_metric", path + ".metric", "Choose a supported metric object from capabilities.quantitative_queries.")
            continue
        metric_kind = metric["type"]
        required = METRIC_FIELDS[metric_kind]
        for key in set(metric) - required:
            issue("unknown_field", path + ".metric." + key, f"Unknown metric field {key!r}.")
        for key in required - set(metric):
            issue("query_required", path + ".metric." + key, "This metric field must be explicit.")
        selected: list[dict[str, Any]] = []
        if "connection" in required:
            connection = metric.get("connection")
            links = {link["id"]: link for link in scene.get("connections", [])}
            if not isinstance(connection, str) or connection not in links:
                issue("query_connection", path + ".metric.connection", "Target must name an existing connection.")
            elif metric_kind in {"spring_force", "spring_energy"} and links[connection]["type"] != "spring":
                issue("query_metric_target", path + ".metric.connection", "Spring force and energy require a spring connection.")
        elif "entity" in required:
            entity_id = metric.get("entity")
            if not isinstance(entity_id, str) or entity_id not in entities:
                issue("query_target", path + ".metric.entity", "Target must name an existing entity.")
            else:
                selected = [entities[entity_id]]
        else:
            ids = metric.get("entities")
            if (not isinstance(ids, list) or len(ids) != 2
                    or any(not isinstance(x, str) or x not in entities for x in ids)
                    or ids[0] == ids[1]):
                issue("query_target", path + ".metric.entities", "Use exactly two distinct existing entity IDs.")
            else:
                selected = [entities[x] for x in ids]
        if metric_kind == "spread_radius":
            if selected and selected[0]["type"] not in {"fluid", "granular", "mesh"}:
                issue("query_metric_target", path + ".metric.entity", "spread_radius requires fluid/granular particles or mesh vertices.")
            if not isinstance(metric.get("plane"), str) or metric["plane"] not in {"xy", "xz", "yz"}:
                issue("query_plane", path + ".metric.plane", "plane must be xy, xz, or yz.")
            if not finite_vec(metric.get("origin")) or any(abs(x) > 1e6 for x in metric["origin"]):
                issue("query_origin", path + ".metric.origin", "origin must contain three finite coordinates within ±1e6 m.")
            else:
                metric["origin"] = [float(x) for x in metric["origin"]]
        if metric_kind in {"centroid", "angular_momentum"} and (not isinstance(metric.get("axis"), str) or metric["axis"] not in {"x", "y", "z"}):
            issue("query_axis", path + ".metric.axis", "axis must be x, y, or z.")
        if metric_kind in {"angular_speed", "angular_momentum", "rotational_energy", "axis_tilt"} and selected and selected[0]["type"] != "rigid_body":
            issue("query_metric_target", path + ".metric.entity", "Rotation metrics require a rigid_body entity.")
        if metric_kind in {"volume_ratio", "max_displacement", "max_edge_strain"} and selected:
            if selected[0]["type"] != "mesh":
                issue("query_metric_target", path + ".metric.entity", "This metric requires a mesh entity.")
            elif metric_kind == "volume_ratio" and not selected[0]["mesh"]["metadata"].get("closed"):
                issue("query_metric_target", path + ".metric.entity", "volume_ratio requires a closed mesh with positive rest volume.")
        if metric_kind == "surface_gap" and len(selected) == 2:
            shapes = [e.get("shape", {}).get("type") if e["type"] == "rigid" else e["type"] for e in selected]
            if shapes not in (["sphere", "sphere"], ["box", "box"], ["slider", "slider"]):
                issue("query_metric_target", path + ".metric.entities", "surface_gap supports two rigid spheres, two static axis-aligned boxes, or two rail sliders.")
        if kind == "threshold":
            if not isinstance(query.get("operator"), str) or query["operator"] not in {"gte", "lte"}:
                issue("query_operator", path + ".operator", "operator must be gte or lte.")
            if not finite_number(query.get("value")) or abs(query["value"]) > 1e6:
                issue("query_value", path + ".value", "value must be a finite number within ±1e6 in the metric's SI unit.")
            else:
                query["value"] = float(query["value"])
            query.setdefault("hold_for", 0.0)
            if not finite_number(query["hold_for"]) or not 0 <= query["hold_for"] <= scene["world"]["duration"]:
                issue("query_hold", path + ".hold_for", "hold_for must lie between zero and world.duration seconds.")
            else:
                query["hold_for"] = float(query["hold_for"])
    return errors


def observation_plan(scene: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    queries = scene.get("queries", [])
    samples = int(plan["steps"]) + 1
    scalars = 1 + sum(6 if q["type"] == "nbody_stability" else 1 for q in queries)
    bodies = sum(e["type"] == "point_mass" for e in scene["entities"])
    particles = int(plan["planned_particles"])
    entity_map = {e["id"]: e for e in scene["entities"]}
    work = 0
    for q in queries:
        if q["type"] == "nbody_stability":
            work += max(bodies * bodies, 1)
        else:
            metric = q["metric"]
            if "connection" in metric:
                work += 1
                continue
            targets = metric.get("entities", [metric.get("entity")])
            work += sum((len(entity_map[x]["mesh"]["vertices"]) + len(entity_map[x]["mesh"]["triangles"])) if entity_map[x]["type"] == "mesh" else particles if entity_map[x]["type"] in {"fluid", "granular"} else 1 for x in targets)
    return {
        "query_count": len(queries), "sample_capacity": samples,
        "scalar_capacity": scalars * samples, "work_units": samples * work,
        "sample_interval_s": scene["world"]["dt"], "precision": "float64",
        "source": "solver_macro_steps", "full_particle_population": True,
        "fits_limits": scalars * samples <= MAX_OBSERVATION_SCALARS and samples * work <= MAX_OBSERVATION_WORK,
        "estimated_memory_mb": round(scalars * samples * 128 / 1e6, 3),
    }


def _number(value: Any, path: str) -> float:
    if not finite_number(value):
        raise ValueError(path + " must be a finite number, not a boolean")
    return float(value)


def validate_observations(scene: dict[str, Any], plan: dict[str, Any], trajectory: dict[str, Any]) -> dict[str, Any]:
    obs = trajectory.get("observations")
    if not isinstance(obs, dict) or type(obs.get("version")) is not int or obs.get("version") != 1 or obs.get("source") != "solver_macro_steps":
        raise ValueError("Missing or incompatible solver observations for declared queries")
    times = obs.get("times")
    if not isinstance(times, list) or not times or len(times) > plan["measurement_plan"]["sample_capacity"]:
        raise ValueError("Observation timestamps exceed the declared sample capacity or are missing")
    parsed = [_number(x, "observations.times") for x in times]
    dt = scene["world"]["dt"]
    duration = scene["world"]["duration"]
    if parsed[0] != 0 or any(b <= a for a, b in zip(parsed, parsed[1:])):
        raise ValueError("Observation timestamps must start at zero and strictly increase")
    if any(abs(t - min(i * dt, duration)) > max(1e-8, duration * 1e-9) for i, t in enumerate(parsed)):
        raise ValueError("Observation timestamps do not match solver macro-step sampling")
    diagnostics = trajectory["diagnostics"]
    if parsed[-1] > diagnostics["simulated_time_s"] + 1e-8:
        raise ValueError("Observations extend beyond the integrated physical time")
    terminal_tolerance = max(1e-8, dt * 1e-5) if str(diagnostics.get("backend", "")).startswith("native-") else 1e-8
    if diagnostics["completed"] and (len(times) != diagnostics["steps"] + 1 or abs(parsed[-1] - duration) > terminal_tolerance):
        raise ValueError("Completed observations omit a macro step or the requested final time")
    columns, nbody = obs.get("columns"), obs.get("nbody")
    if not isinstance(columns, dict) or not isinstance(nbody, dict):
        raise ValueError("Observation columns and nbody must be objects")
    scalar_ids = {q["id"] for q in scene["queries"] if q["type"] != "nbody_stability"}
    nbody_ids = {q["id"] for q in scene["queries"] if q["type"] == "nbody_stability"}
    if set(columns) != scalar_ids or set(nbody) != nbody_ids:
        raise ValueError("Observation IDs do not match the predeclared queries")

    def series(values: Any, path: str, nonnegative: bool = False) -> None:
        if not isinstance(values, list) or len(values) != len(times):
            raise ValueError(path + " length does not match timestamps")
        for value in values:
            number = _number(value, path)
            if nonnegative and number < 0:
                raise ValueError(path + " cannot be negative")

    for q in scene["queries"]:
        if q["type"] != "nbody_stability":
            series(columns[q["id"]], q["id"], q["metric"]["type"] in {"spread_radius", "speed", "center_distance", "angular_speed", "rotational_energy", "axis_tilt"})
        else:
            data = nbody[q["id"]]
            keys = {"max_com_radius", "min_pair_distance", "energy", "momentum"}
            if not isinstance(data, dict) or set(data) != keys:
                raise ValueError("N-body observer fields do not match the measurement contract")
            for key in keys - {"momentum"}:
                series(data[key], q["id"] + "." + key, key != "energy")
            momenta = data["momentum"]
            if not isinstance(momenta, list) or len(momenta) != len(times) or not all(finite_vec(p) for p in momenta):
                raise ValueError("N-body momentum samples must be finite vectors matching timestamps")
    return obs


def _crossing(query: dict[str, Any], times: list[float], values: list[float]) -> dict[str, Any]:
    sign = 1 if query["operator"] == "gte" else -1
    threshold, hold = query["value"], query["hold_for"]
    start: float | None = None
    bracket: list[float] | None = None
    observed: float | None = None
    for i, (t, value) in enumerate(zip(times, values)):
        satisfied = sign * (value - threshold) >= 0
        if not satisfied:
            start = bracket = observed = None
            continue
        if start is None:
            observed = t
            if i == 0:
                start, bracket = 0.0, [0.0, 0.0]
            else:
                fraction = (threshold - values[i - 1]) / (value - values[i - 1])
                start = times[i - 1] + max(0.0, min(1.0, fraction)) * (t - times[i - 1])
                bracket = [times[i - 1], t]
        # Dwell is verified on samples beginning with the first qualifying
        # sample, so interpolation cannot manufacture a sustained event.
        if t - observed + 1e-12 >= hold:
            return {"status": "initially_satisfied" if start == 0 else "reached",
                    "time_s": start, "time_bracket_s": bracket,
                    "first_observed_at_s": observed, "confirmed_at_s": t,
                    "hold_for_s": hold}
    return {"status": "not_observed", "time_s": None, "time_bracket_s": None,
            "first_observed_at_s": None, "confirmed_at_s": None, "hold_for_s": hold}


def evaluate(scene: dict[str, Any], plan: dict[str, Any], trajectory: dict[str, Any],
             run_id: str, scene_hash: str) -> dict[str, Any]:
    obs = validate_observations(scene, plan, trajectory)
    times = obs["times"]
    complete = trajectory["diagnostics"]["completed"] and trajectory["diagnostics"]["finite"]
    answers = []
    for q in scene["queries"]:
        answer: dict[str, Any] = {"id": q["id"], "type": q["type"],
                                  "definition": q, "window_s": [times[0], times[-1]],
                                  "sampling_interval_s": scene["world"]["dt"]}
        if q["type"] == "nbody_stability":
            data = obs["nbody"][q["id"]]
            energy0, p0 = data["energy"][0], data["momentum"][0]
            energy_drift = max(abs(e - energy0) / max(abs(energy0), 1e-12) for e in data["energy"])
            momentum_drift = max(math.dist(p, p0) for p in data["momentum"])
            tolerance = trajectory["diagnostics"].get("nbody_momentum_tolerance")
            reliable = (complete and trajectory["diagnostics"].get("nbody_invariants_applicable") is True
                        and trajectory["diagnostics"].get("nbody_invariants_conserved") is True
                        and finite_number(tolerance) and energy_drift <= 0.02 and momentum_drift <= tolerance)
            violations = [i for i, (r, d) in enumerate(zip(data["max_com_radius"], data["min_pair_distance"]))
                          if r > q["max_radius"] or d < q["min_separation"]]
            answer.update({
                "status": ("criteria_violated" if violations else "criteria_satisfied") if reliable else "inconclusive",
                "max_observed_com_radius_m": max(data["max_com_radius"]),
                "min_observed_pair_distance_m": min(data["min_pair_distance"]),
                "max_relative_energy_drift": energy_drift,
                "max_momentum_drift": momentum_drift,
                "momentum_tolerance": tolerance,
                "numerical_integrity_passed": reliable,
                "first_violation_observed_s": times[violations[0]] if violations else None,
                "long_term_stability_proven": False,
                "interpretation": "Only the declared radius/separation criteria at sampled times in this finite window are tested; conservation is a numerical check, not a stability proof.",
            })
        else:
            values = obs["columns"][q["id"]]
            low, high = min(range(len(values)), key=values.__getitem__), max(range(len(values)), key=values.__getitem__)
            metric_type = q["metric"]["type"]
            unit = {"speed": "m/s", "volume_ratio": "1", "max_edge_strain": "1", "spring_force": "N", "spring_energy": "J", "angular_speed": "rad/s", "angular_momentum": "kg*m^2/s", "rotational_energy": "J", "axis_tilt": "rad"}.get(metric_type, "m")
            answer.update({"unit": unit,
                           "initial": values[0], "final": values[-1],
                           "minimum": {"value": values[low], "time_s": times[low]},
                           "maximum": {"value": values[high], "time_s": times[high]},
                           "model_scope": "visual_particle_model" if any(e["type"] in {"fluid", "granular"} and e["id"] in q["metric"].get("entities", [q["metric"].get("entity")]) for e in scene["entities"]) else "configured_mechanical_model"})
            if any(e["type"] == "mesh" for e in scene["entities"]):
                answer["model_scope"] = "visual_elastic_surface_model"
            if scene.get("connections"):
                answer["model_scope"] = "ideal_massless_connection_model"
            from physics_demo.io.coupled import enabled
            if enabled(scene):
                answer["model_scope"] = "partitioned_two_way_contact_model"
            if q["type"] == "threshold":
                answer.update(_crossing(q, times, values))
                answer["interpretation"] = "Linear-interpolated first qualifying sampled interval; the bracket is temporal sampling resolution, not a certified error bound. A missed event between samples is possible. not_observed does not mean never."
            else:
                answer.update({"status": "measured", "sample_count": len(times)})
            if not complete:
                answer["status"] = "inconclusive"
                if q["type"] == "threshold":
                    answer["time_s"] = None
                    answer["time_bracket_s"] = None
        answers.append(answer)
    return {"version": 1, "run_id": run_id, "scene_hash": scene_hash,
            "query_contract_sha256": digest(scene["queries"]),
            "observations_sha256": digest(obs),
            "sampling": {**plan["measurement_plan"], "sample_count": len(times), "window_s": [times[0], times[-1]]},
            "answers": answers, "observations": obs}


def compact(measurements: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in measurements.items() if key != "observations"}
