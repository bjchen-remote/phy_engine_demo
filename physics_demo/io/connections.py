"""Contract, bounded work plan and result checks for ideal connected point masses."""
from __future__ import annotations

import math
from typing import Any

from physics_demo.core.math3d import finite_number

MAX_CONNECTIONS = 256
MAX_SUBSTEPS = 64
MAX_STEPS = 250_000
MAX_WORK = 100_000_000
CAPABILITIES = {
    "types": ["spring", "rod", "rope"],
    "algorithm": "C11 Hooke velocity Verlet, split exact axial damping, SHAKE/RATTLE rod constraints and unilateral rope projection",
    "limits": {"max_nodes": 64, "max_connections": MAX_CONNECTIONS, "max_substeps": MAX_SUBSTEPS,
               "max_iterations": 64, "standalone_only": True},
    "gravity": "Point masses use explicit force_fields; world.gravity does not act on them.",
    "accuracy": "Ideal massless links: springs have axial stiffness/damping, rods fix distance, ropes limit maximum distance with inelastic take-up. No link collision, bending, fracture or material calibration.",
}


def initial_tolerance(length: float) -> float:
    return max(1e-9, length * 1e-7)


def validate_scene(scene: dict, errors: list[dict]) -> None:
    def issue(code: str, path: str, message: str) -> None:
        errors.append({"code": code, "path": path, "message": message})

    if "connections" not in scene:
        if "connection_settings" in scene:
            issue("connections_required", "connection_settings", "Solver settings require a nonempty connections scene.")
        return
    links = scene["connections"]
    if not isinstance(links, list) or not 1 <= len(links) <= MAX_CONNECTIONS:
        issue("connection_limit", "connections", f"Use 1–{MAX_CONNECTIONS} connections.")
        return
    nodes = scene["entities"]
    if any(e["type"] != "point_mass" for e in nodes) or scene["colliders"] or scene["interactions"]["mutual_gravity"]:
        issue("unsupported_connection_coupling", "connections", "Connections require standalone point masses, no colliders or mutual gravity; use force_fields for gravity and drives.")
        return
    if scene["budget"]["backend"] == "python":
        issue("connection_backend", "budget.backend", "Connections require auto or native; there is no Python fallback.")
    if scene["world"]["duration"] < 1e-4:
        issue("connection_duration", "world.duration", "Connected motion requires duration of at least 0.0001 s.")
    by_id = {e["id"]: e for e in nodes}
    for i, node in enumerate(nodes):
        if node["mass"] < 1e-9:
            issue("connection_mass", f"entities[{i}].mass", "Connected point masses must be at least 1e-9 kg.")
        if node["fixed"] and any(node["velocity"]):
            issue("fixed_velocity", f"entities[{i}].velocity", "A fixed anchor must have zero velocity.")
    seen = set()
    for i, link in enumerate(links):
        path = f"connections[{i}]"
        if not isinstance(link, dict):
            issue("connection_type", path, "Every connection must be an object.")
            continue
        ident, kind = link.get("id"), link.get("type")
        if not isinstance(ident, str) or not ident.strip() or len(ident) > 128:
            issue("connection_id", path + ".id", "Use a nonempty connection ID of at most 128 characters.")
        elif ident in seen:
            issue("duplicate_connection_id", path + ".id", "Connection IDs must be unique.")
        else:
            seen.add(ident)
        if not isinstance(kind, str) or kind not in {"spring", "rod", "rope"}:
            issue("connection_kind", path + ".type", "Use spring, rod or rope.")
            continue
        allowed = {"id", "type", "entities", "rest_length"} | ({"stiffness", "damping"} if kind == "spring" else set())
        for key in sorted(set(link) - allowed):
            issue("unknown_field", path + "." + key, "Unknown connection field.")
        params = {"rest_length": (1e-6, 1000.0)}
        if kind == "spring":
            link.setdefault("damping", 0.0)
            params.update(stiffness=(1e-9, 1e7), damping=(0.0, 1e5))
        valid_parameters = True
        for key, (low, high) in params.items():
            value = link.get(key)
            if not finite_number(value) or not low <= value <= high:
                issue("connection_parameter", path + "." + key, f"{key} must be a finite number in [{low:g}, {high:g}].")
                valid_parameters = False
            else:
                link[key] = float(value)
        ids = link.get("entities")
        if (not isinstance(ids, list) or len(ids) != 2 or any(not isinstance(x, str) or x not in by_id for x in ids)
                or ids[0] == ids[1]):
            issue("connection_target", path + ".entities", "Use two distinct existing point_mass IDs.")
            continue
        a, b = [by_id[x] for x in ids]
        if a["fixed"] and b["fixed"]:
            issue("fixed_connection", path, "At least one endpoint must be movable.")
        length = math.dist(a["position"], b["position"])
        if kind != "rope" and length <= 1e-9:
            issue("coincident_connection", path, "Spring and rod endpoints must start separated by more than 1e-9 m.")
        if kind == "rod" and length > 1e-9:
            axial_velocity = sum((b["velocity"][axis] - a["velocity"][axis]) *
                                 (b["position"][axis] - a["position"][axis]) / length for axis in range(3))
            tolerance = max(1e-9, 1e-7 * max(1, math.hypot(*a["velocity"]), math.hypot(*b["velocity"])))
            if abs(axial_velocity) > tolerance:
                issue("initial_connection_velocity", path, "A rod must start with zero relative axial velocity. Use tangential velocity; velocities are never silently projected.")
        if valid_parameters:
            error, tolerance = length - link["rest_length"], initial_tolerance(link["rest_length"])
            if (kind == "rod" and abs(error) > tolerance) or (kind == "rope" and error > tolerance):
                issue("initial_connection_length", path, "A rod must start at its stated length; a rope may start slack but not overlong. Initial positions are never silently projected.")
    settings = scene.setdefault("connection_settings", {})
    if not isinstance(settings, dict):
        issue("connection_settings", "connection_settings", "Expected a solver-settings object.")
        return
    for key in sorted(set(settings) - {"substeps", "iterations"}):
        issue("unknown_field", "connection_settings." + key, "Unknown connection solver setting.")
    defaults = {"preview": (2, 8), "balanced": (4, 16), "high": (8, 32)}[scene["budget"]["quality"]]
    for key, default in zip(("substeps", "iterations"), defaults):
        value = settings.setdefault(key, default)
        if type(value) is not int or not 1 <= value <= 64:
            issue("connection_settings", "connection_settings." + key, "Use an integer in [1, 64].")


def make_plan(scene: dict, include_video: bool) -> dict[str, Any]:
    from physics_demo.analysis.queries import observation_plan

    nodes, links, world = scene["entities"], scene["connections"], scene["world"]
    stiffness = {e["id"]: 0.0 for e in nodes}
    damping = dict(stiffness)
    for link in links:
        if link["type"] == "spring":
            for ident in link["entities"]:
                stiffness[ident] += link["stiffness"]
                damping[ident] += link["damping"]
    omega = math.sqrt(2 * max((stiffness[e["id"]] / e["mass"] for e in nodes if not e["fixed"]), default=0))
    gamma = 2 * max((damping[e["id"]] / e["mass"] for e in nodes if not e["fixed"]), default=0)
    hard = sum(link["type"] != "spring" for link in links)
    requested = scene["connection_settings"]["substeps"]
    substeps = max(requested, math.ceil(world["dt"] * omega / 0.05),
                   math.ceil(world["dt"] * gamma / 0.25), 8 if hard else 1)
    iterations = scene["connection_settings"]["iterations"]
    steps = math.ceil(world["duration"] / world["dt"])
    frames = max(1, math.ceil(world["duration"] * world["output_fps"] - 1e-10)) + 1
    work = steps * substeps * (4 * len(nodes) + 4 * len(links) + 2 * iterations * hard + 2 * len(nodes) * len(scene["force_fields"]))
    adjustments = ([f"Increased connection substeps from {requested} to {substeps} for stiffness/damping resolution or rod/rope constraints; topology, rest lengths, stiffness and damping are preserved."] if substeps != requested else [])
    plan = {
        "quality": scene["budget"]["quality"], "requested_backend": scene["budget"]["backend"], "backend": "connections",
        "requested_particles": 0, "uniform_spacing_particles_before_cap": 0, "particle_limit": 0, "planned_particles": 0,
        "effective_spacing": 0.0, "render_particle_limit": 0, "frame_particle_samples": 0,
        "resolution_policy": "Explicit point masses and connections; stiffness selects extra substeps, never weaker material or fewer links.",
        "solver_iterations": iterations, "density_iterations": 0, "divergence_iterations": 0, "threads": 1,
        "max_substeps": substeps, "steps": steps, "output_frames": frames, "complexity_units": work,
        "wall_time_limit_s": scene["budget"]["wall_time_s"], "bounds_feasible": True,
        "initial_bounds_feasible": True, "initial_bounds_violations": [], "nbody_timestep_feasible": True,
        "nbody_initial_step_ratio": 0.0, "minimum_particle_bounds_span_m": 0.0,
        "bounds_spans_m": [world["bounds"]["max"][a] - world["bounds"]["min"][a] for a in range(3)],
        "connection_nodes": len(nodes), "connection_count": len(links), "connection_substeps": substeps,
        "connection_iterations": iterations, "connection_omega_bound_rad_s": omega,
        "connection_damping_bound_s_inv": gamma,
        "connection_fits_limits": substeps <= MAX_SUBSTEPS and steps <= MAX_STEPS and work <= MAX_WORK,
        "adjustments": adjustments, "estimated_peak_memory_mb": round(16 + frames * len(nodes) * 200 / 1e6, 3),
        "note": "Provisional connection work estimate; bounds frame the picture and do not create walls. Native deadlines and final full-runtime gates remain authoritative.",
    }
    if scene.get("queries"):
        plan["measurement_plan"] = observation_plan(scene, plan)
        plan["estimated_peak_memory_mb"] += plan["measurement_plan"]["estimated_memory_mb"]
    solver = .03 + work * 1e-7 + plan.get("measurement_plan", {}).get("work_units", 0) * 2e-8
    video = .2 + frames * (len(nodes) + 8 * len(links)) * 8e-6 if include_video else 0.0
    p90 = 2 + 2 * (solver + video)
    plan["timing_estimate"] = {
        "hardware_profile": "provisional C11 connection work model; validate on target hardware",
        "physical_duration_s": world["duration"], "solver_p50_s": round(solver, 3), "solver_p90_s": round(2 * solver, 3),
        "video_p50_s": round(video, 3), "video_p90_s": round(2 * video, 3), "cold_compile_p90_s": 2.0,
        "total_p50_s": round(solver + video, 3), "total_p90_s": round(p90, 3),
        "hard_limit_s": scene["budget"]["wall_time_s"], "fits_budget": p90 <= scene["budget"]["wall_time_s"],
        "confidence": "provisional estimate; measured runtime is authoritative", "limiting_factor": "connection integration and video",
    }
    return plan


def result_checks(scene: dict, plan: dict, trajectory: dict) -> dict[str, bool]:
    nodes, links, diagnostics = scene["entities"], scene["connections"], trajectory["diagnostics"]
    if trajectory.get("gravity_body_ids") != [node["id"] for node in nodes]:
        raise ValueError("Connection node IDs do not match the scene order.")
    if trajectory.get("rigid_ids") != [] or trajectory.get("rigid_shapes") != []:
        raise ValueError("Connections cannot contain rigid-body metadata.")
    for index, frame in enumerate(trajectory["frames"]):
        for i, node in enumerate(nodes):
            if (index == 0 or node["fixed"]) and frame["g"][i] != node["position"]:
                raise ValueError("Initial or fixed connection node positions disagree with the scene.")
    limits = {kind: max((max(1e-6, link["rest_length"] * 1e-4) for link in links if link["type"] == kind), default=1e-6)
              for kind in ("rod", "rope")}
    values = {}
    for key in ("max_rod_error_m", "max_rope_extension_m", "max_constraint_error_ratio", "max_speed_m_s",
                "connection_peak_relative_energy_drift",
                "initial_kinetic_energy", "final_kinetic_energy", "initial_spring_energy", "final_spring_energy"):
        value = diagnostics.get(key)
        if not finite_number(value) or value < 0:
            raise ValueError("Connection diagnostic " + key + " must be finite and nonnegative.")
        values[key] = value
    # Measure each displayed link against its own tolerance. Linear display
    # interpolation can shorten a rotating rigid rod slightly between substeps.
    ids = {node["id"]: i for i, node in enumerate(nodes)}
    geometry_valid = True
    for frame in trajectory["frames"]:
        for link in links:
            if link["type"] == "spring":
                continue
            a, b = [frame["g"][ids[ident]] for ident in link["entities"]]
            error = math.dist(a, b) - link["rest_length"]
            tolerance = max(1e-6, link["rest_length"] * 1e-4)
            geometry_valid &= error <= tolerance if link["type"] == "rope" else abs(error) <= max(.001 * link["rest_length"], tolerance)
    applicable = not scene["force_fields"] and all(link["type"] == "spring" and link["damping"] == 0 for link in links)
    if diagnostics.get("connection_energy_conservation_applicable") is not applicable:
        raise ValueError("Connection energy applicability disagrees with the scene.")
    checks = {"connection_rod_length_residual": values["max_rod_error_m"] <= limits["rod"],
            "connection_rope_length_residual": values["max_rope_extension_m"] <= limits["rope"],
            "connection_each_constraint_residual": values["max_constraint_error_ratio"] <= 1,
            "connection_displayed_lengths": geometry_valid}
    if applicable:
        initial = values["initial_kinetic_energy"] + values["initial_spring_energy"]
        final = values["final_kinetic_energy"] + values["final_spring_energy"]
        checks["connection_endpoint_energy_drift_at_most_0_02"] = abs(final - initial) <= max(1e-12, .02 * initial)
        checks["connection_peak_energy_drift_at_most_0_02"] = values["connection_peak_relative_energy_drift"] <= .02
    return checks
