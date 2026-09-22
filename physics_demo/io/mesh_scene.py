"""Bounded mesh scene contract and cost planning, separate from particle physics."""
from __future__ import annotations

import math
import time
from typing import Any

from physics_demo.core.math3d import finite_number, finite_vec
from physics_demo.io.jsonio import canonical_bytes
from physics_demo.analysis.queries import observation_plan

MAX_MESH_OBJECTS = 16
MAX_SOFT_OBJECTS = 4
MAX_SCENE_VERTICES = 8192
MAX_SCENE_TRIANGLES = 16384
MAX_MESH_FRAME_SAMPLES = 600_000
MAX_MESH_SCENE_NODES = 160_000
MESH_FIELDS = {
    "id", "type", "mesh", "motion", "position", "velocity", "mass",
    "edge_compliance", "bending_compliance", "volume_compliance", "damping",
    "friction", "thickness", "pinned_vertices", "color",
}
MESH_CAPABILITIES = {
    "algorithm": "C11 XPBD triangle-surface stretch, bending-distance and closed global-volume constraints",
    "modeling": "A language/vision agent supplies a bounded recipe, image silhouette with inferred depth, or explicit imagined vertices/triangles to physics_mesh.",
    "motion": ["soft", "static", "kinematic"],
    "accuracy": "visual elastic surface proxy; no calibrated volumetric stress, fracture, cutting or force prediction",
    "contacts": "BVH vertex-triangle contacts and swept vertex-face detection; no general edge-edge CCD or self-collision guarantee",
    "limits": {"max_objects": MAX_MESH_OBJECTS, "max_soft_objects": MAX_SOFT_OBJECTS,
               "max_scene_vertices": MAX_SCENE_VERTICES, "max_scene_triangles": MAX_SCENE_TRIANGLES,
               "max_frame_vertex_samples": MAX_MESH_FRAME_SAMPLES, "standalone_only": True},
}


def _issue(errors: list[dict], code: str, path: str, message: str) -> None:
    errors.append({"code": code, "path": path, "message": message})


def normalize_entity(entity: dict, path: str, errors: list[dict]) -> None:
    from physics_demo.core.meshes import normalize_mesh

    try:
        entity["mesh"] = normalize_mesh(entity.get("mesh"))
    except (TypeError, ValueError, OverflowError) as error:
        _issue(errors, "invalid_mesh", path + ".mesh", str(error))
        return
    audit = entity["mesh"]["metadata"]
    if audit["closed"] and audit["signed_volume"] <= 1e-15:
        _issue(errors, "mesh_numeric_scale", path + ".mesh", "Closed mesh volume must exceed 1e-15 m³ for the native solver; use a larger explicit modeling scale.")
    entity.setdefault("motion", "soft")
    if entity["motion"] not in ("soft", "static", "kinematic"):
        _issue(errors, "mesh_motion", path + ".motion", "motion must be soft, static or kinematic.")
    for key, default in (("position", [0.0, 0.0, 0.0]), ("color", [0.22, 0.64, 0.86])):
        value = entity.setdefault(key, default)
        if not finite_vec(value):
            _issue(errors, "mesh_vector", path + "." + key, "Expected three finite numbers.")
        elif key == "color" and any(not 0 <= x <= 1 for x in value):
            _issue(errors, "mesh_color", path + ".color", "RGB components must be in [0, 1].")
        elif key == "position" and any(abs(x) > 10_000 for x in value):
            _issue(errors, "coordinate_range", path + ".position", "Position must stay within ±10000 m.")
        else:
            entity[key] = [float(x) for x in value]
    scalars = {
        "mass": (1.0, 1e-6, 1e6), "edge_compliance": (1e-6, 0.0, 1.0),
        "bending_compliance": (1e-4, 0.0, 1.0), "volume_compliance": (1e-7, 0.0, 1.0),
        "damping": (0.15, 0.0, 100.0), "friction": (0.35, 0.0, 5.0),
        "thickness": (0.015, 1e-5, 0.25),
    }
    for key, (default, low, high) in scalars.items():
        value = entity.setdefault(key, default)
        if not finite_number(value) or not low <= value <= high:
            _issue(errors, "mesh_parameter", path + "." + key, f"{key} must be a finite number in [{low:g}, {high:g}].")
        else:
            entity[key] = float(value)
    pins = entity.setdefault("pinned_vertices", [])
    count = len(entity["mesh"]["vertices"])
    if (not isinstance(pins, list) or len(pins) > count or
            any(type(i) is not int or not 0 <= i < count for i in pins) or len(set(pins)) != len(pins)):
        _issue(errors, "mesh_pins", path + ".pinned_vertices", "Use unique integer vertex indices within the mesh.")
    if entity["motion"] == "static" and any(entity["velocity"]):
        _issue(errors, "static_mesh_velocity", path + ".velocity", "A static mesh must have zero velocity; choose kinematic for prescribed translation.")
    if entity["motion"] != "soft" and pins:
        _issue(errors, "mesh_pins", path + ".pinned_vertices", "Only soft meshes use pinned vertices.")


def validate_scene(scene: dict, errors: list[dict], allow_coupling: bool = False) -> None:
    meshes = [e for e in scene["entities"] if isinstance(e, dict) and e.get("type") == "mesh"]
    if not meshes:
        if "mesh_settings" in scene:
            _issue(errors, "mesh_settings_without_mesh", "mesh_settings", "Mesh settings require a mesh scene.")
        return
    if not allow_coupling and (len(meshes) != len(scene["entities"]) or scene["force_fields"] or scene["interactions"]["mutual_gravity"]):
        _issue(errors, "unsupported_mesh_coupling", "entities", "Mesh scenes currently contain mesh entities and static analytic colliders only; fluid, sand, gravity-body and field coupling is unavailable.")
    if len(meshes) > MAX_MESH_OBJECTS or sum(e.get("motion") == "soft" for e in meshes) > MAX_SOFT_OBJECTS:
        _issue(errors, "mesh_object_limit", "entities", f"Use at most {MAX_MESH_OBJECTS} meshes, of which at most {MAX_SOFT_OBJECTS} are soft.")
    if scene["budget"]["backend"] == "python":
        _issue(errors, "mesh_backend", "budget.backend", "Mesh simulation requires the native C11 solver; use auto or native.")
    if scene["world"]["duration"] < 1e-4:
        _issue(errors, "mesh_duration", "world.duration", "Mesh physical duration must be at least 0.0001 s.")
    settings = scene.setdefault("mesh_settings", {})
    if not isinstance(settings, dict):
        _issue(errors, "mesh_settings", "mesh_settings", "Expected a settings object.")
        return
    for key in sorted(set(settings) - {"substeps", "iterations"}):
        _issue(errors, "unknown_field", "mesh_settings." + key, "Unknown mesh solver setting.")
    default_steps, default_iterations = {"preview": (4, 6), "balanced": (8, 8), "high": (12, 10)}.get(scene["budget"]["quality"], (4, 6))
    for key, default, high in (("substeps", default_steps, 32), ("iterations", default_iterations, 32)):
        value = settings.setdefault(key, default)
        if type(value) is not int or not 1 <= value <= high:
            _issue(errors, "mesh_settings", "mesh_settings." + key, f"{key} must be an integer in [1, {high}].")
    if errors:
        return
    vertices = sum(len(e["mesh"]["vertices"]) for e in meshes)
    triangles = sum(len(e["mesh"]["triangles"]) for e in meshes)
    if vertices > MAX_SCENE_VERTICES or triangles > MAX_SCENE_TRIANGLES:
        _issue(errors, "mesh_scene_limit", "entities", f"Whole scene limit: {MAX_SCENE_VERTICES} vertices and {MAX_SCENE_TRIANGLES} triangles. Reduce the requested topology explicitly.")
        return
    from physics_demo.core.meshes import meshes_intersect
    started = time.monotonic()
    for i, first in enumerate(meshes):
        for second in meshes[i + 1:]:
            if first["motion"] != "soft" and second["motion"] != "soft":
                continue
            try:
                if time.monotonic() - started > 2.0:
                    raise ValueError("Initial mesh contact audit exceeded its bounded time; simplify or separate geometry.")
                if meshes_intersect(first["mesh"], first["position"], second["mesh"], second["position"]):
                    _issue(errors, "initial_mesh_contact", "entities", f"{first['id']!r} and {second['id']!r} initially overlap, touch or contain each other. Start separated, then drive the intended contact.")
            except ValueError as error:
                _issue(errors, "mesh_contact_audit_budget", "entities", str(error))
                return


def make_mesh_plan(scene: dict, include_video: bool) -> dict[str, Any]:
    entities, world, settings = scene["entities"], scene["world"], scene["mesh_settings"]
    count = sum(len(e["mesh"]["vertices"]) for e in entities)
    faces = sum(len(e["mesh"]["triangles"]) for e in entities)
    soft_count = sum(len(e["mesh"]["vertices"]) for e in entities if e["motion"] == "soft")
    steps = math.ceil(world["duration"] / world["dt"])
    frames = max(1, math.ceil(world["duration"] * world["output_fps"] - 1e-10)) + 1
    minimum_edge = min(math.dist(e["mesh"]["vertices"][t[a]], e["mesh"]["vertices"][t[b]])
                       for e in entities for t in e["mesh"]["triangles"] for a, b in ((0, 1), (1, 2), (2, 0)))
    outside = []
    for entity in entities:
        margin = entity["thickness"] if entity["motion"] == "soft" else 0.0
        if any(any(not world["bounds"]["min"][a] + margin <= v[a] + entity["position"][a] <= world["bounds"]["max"][a] - margin
                   for a in range(3)) for v in entity["mesh"]["vertices"]):
            outside.append(entity["id"] + " (mesh outside initial world bounds)")
    work = steps * settings["substeps"] * settings["iterations"] * (count + faces)
    samples = count * frames
    plan = {
        "quality": scene["budget"]["quality"], "requested_backend": scene["budget"]["backend"], "backend": "mesh",
        "requested_particles": 0, "uniform_spacing_particles_before_cap": 0, "particle_limit": 0,
        "planned_particles": 0, "effective_spacing": minimum_edge, "render_particle_limit": 0,
        "resolution_policy": "explicit triangle topology; never silently simplify or decimate mesh vertices",
        "solver_iterations": settings["iterations"], "density_iterations": 0, "divergence_iterations": 0,
        "threads": 1, "max_substeps": settings["substeps"], "steps": steps, "output_frames": frames,
        "complexity_units": work, "wall_time_limit_s": scene["budget"]["wall_time_s"], "frame_particle_samples": 0,
        "mesh_vertices": count, "mesh_soft_vertices": soft_count, "mesh_triangles": faces,
        "mesh_frame_vertex_samples": samples, "mesh_substeps": settings["substeps"], "mesh_iterations": settings["iterations"],
        "mesh_fits_limits": samples <= MAX_MESH_FRAME_SAMPLES and work <= 2_000_000_000 and steps <= 250_000,
        "bounds_feasible": True, "initial_bounds_feasible": not outside, "initial_bounds_violations": outside,
        "bounds_spans_m": [world["bounds"]["max"][a] - world["bounds"]["min"][a] for a in range(3)],
        "minimum_particle_bounds_span_m": 0.0, "nbody_initial_step_ratio": 0.0, "nbody_timestep_feasible": True,
        "estimated_peak_memory_mb": round(24 + samples * 160 / 1e6 + (count + faces) * 400 / 1e6, 2),
        "adjustments": [], "note": "Mesh runtime estimate is provisional until benchmarked; full topology is preserved, and native execution checks the cooperative deadline.",
    }
    # Conservative initial work model. Benchmark coefficients separately from
    # particle DFSPH; collision density and resolution change the work substantially.
    solver_p50 = 0.05 + work * 4e-8
    video_p50 = 0.2 + frames * faces * 1.5e-6 if include_video else samples * 1e-6
    measurement_p50 = 0.0
    if scene.get("queries"):
        plan["measurement_plan"] = observation_plan(scene, plan)
        measurement_p50 = plan["measurement_plan"]["work_units"] * 2e-8
        plan["estimated_peak_memory_mb"] += plan["measurement_plan"]["estimated_memory_mb"]
    solver_p50 += measurement_p50
    total_p90 = 2.0 + 2.0 * solver_p50 + 2.0 * video_p50
    plan["timing_estimate"] = {
        "hardware_profile": "provisional C11 mesh work model; calibrate on target hardware",
        "physical_duration_s": world["duration"], "expected_cfl_substeps_per_step": settings["substeps"],
        "solver_p50_s": round(solver_p50, 3), "solver_p90_s": round(2 * solver_p50, 3),
        "video_p50_s": round(video_p50, 3), "video_p90_s": round(2 * video_p50, 3),
        "cold_compile_p90_s": 2.0, "measurement_p50_s": round(measurement_p50, 3),
        "total_p50_s": round(solver_p50 + video_p50, 3), "total_p90_s": round(total_p90, 3),
        "hard_limit_s": scene["budget"]["wall_time_s"], "fits_budget": total_p90 <= scene["budget"]["wall_time_s"],
        "limiting_factor": "mesh constraints and contact candidates" if solver_p50 > video_p50 else "triangle rasterization and video",
        "confidence": "provisional estimate; cooperative deadline and measured runtime remain authoritative",
    }
    return plan


def result_checks(scene: dict, plan: dict, trajectory: dict) -> dict[str, bool]:
    """Bind rendered topology and full vertex frames to their prepared assets."""
    diagnostics = trajectory["diagnostics"]
    objects = trajectory.get("mesh_objects")
    if not isinstance(objects, list) or len(objects) != len(scene["entities"]):
        raise ValueError("Mesh object metadata does not match the scene")
    offset, rest, pins, prescribed, edges, volumes, soft_vertices = 0, [], [], [], [], [], []
    closed_soft = 0
    for entity, item in zip(scene["entities"], objects):
        vertices = entity["mesh"]["vertices"]
        expected = {"id": entity["id"], "vertex_start": offset, "vertex_count": len(vertices),
                    "triangles": entity["mesh"]["triangles"], "color": entity["color"], "motion": entity["motion"],
                    "closed": entity["mesh"]["metadata"]["closed"]}
        if canonical_bytes(item) != canonical_bytes(expected):
            raise ValueError("Mesh topology, color or object offsets disagree with the prepared scene")
        rest.extend([[p[a] + entity["position"][a] for a in range(3)] for p in vertices])
        pins.extend(offset + i for i in entity["pinned_vertices"])
        if entity["motion"] != "soft":
            prescribed.extend((offset + i, entity["velocity"]) for i in range(len(vertices)))
        else:
            soft_vertices.extend(range(offset, offset + len(vertices)))
            unique_edges = {tuple(sorted((face[a], face[b]))) for face in entity["mesh"]["triangles"]
                            for a, b in ((0, 1), (1, 2), (2, 0))}
            edges.extend((a + offset, b + offset, math.dist(vertices[a], vertices[b])) for a, b in unique_edges)
            if entity["mesh"]["metadata"]["closed"]:
                volumes.append(([[offset + i for i in face] for face in entity["mesh"]["triangles"]], entity["mesh"]["metadata"]["signed_volume"]))
        closed_soft += entity["motion"] == "soft" and entity["mesh"]["metadata"]["closed"]
        offset += len(vertices)
    if offset != plan["mesh_vertices"] or type(diagnostics.get("mesh_vertex_count")) is not int or diagnostics["mesh_vertex_count"] != offset:
        raise ValueError("Mesh vertex count disagrees with the prepared plan")
    if type(diagnostics.get("mesh_triangle_count")) is not int or diagnostics["mesh_triangle_count"] != plan["mesh_triangles"]:
        raise ValueError("Mesh triangle count disagrees with the prepared plan")
    last_time, sampled_strain, sampled_min_volume, sampled_max_volume = -1.0, 0.0, 1.0, 1.0
    def close(point: list[float], expected: list[float]) -> bool:
        # Presentation frames are float32; queries retain float64 observations.
        tolerance = 4e-7 * max(1.0, *(abs(x) for x in expected))
        return math.dist(point, expected) <= tolerance

    for frame_index, frame in enumerate(trajectory["frames"]):
        points, t = frame.get("m"), frame.get("t")
        if (not isinstance(points, list) or len(points) != offset or any(not finite_vec(p) for p in points)
                or not finite_number(t) or t <= last_time or t < 0 or t > scene["world"]["duration"] + 1e-6):
            raise ValueError("Mesh frame vertices or timestamps are malformed")
        if any(frame.get(key) != [] for key in ("p", "g", "r")):
            raise ValueError("Mesh trajectories may not contain unplanned particle or rigid frames")
        expected_time = min(frame_index / scene["world"]["output_fps"], scene["world"]["duration"])
        if abs(t - expected_time) > 1e-7:
            raise ValueError("Mesh frame timestamp does not match the prepared output schedule")
        fixed = list(range(offset)) if frame_index == 0 else pins
        if (frame_index == 0 and t != 0) or any(not close(points[i], rest[i]) for i in fixed):
            raise ValueError("Initial or pinned mesh positions disagree with the scene")
        if any(not close(points[i], [rest[i][a] + velocity[a] * t for a in range(3)]) for i, velocity in prescribed):
            raise ValueError("Static/kinematic mesh frames violate their prescribed motion")
        bounds = scene["world"]["bounds"]
        if any(any(points[i][a] < bounds["min"][a] - 1e-3 or points[i][a] > bounds["max"][a] + 1e-3 for a in range(3)) for i in soft_vertices):
            raise ValueError("Soft mesh frame contains vertices outside the simulation bounds")
        sampled_strain = max(sampled_strain, max((abs(math.dist(points[a], points[b]) / length - 1) for a, b, length in edges), default=0))
        for faces, rest_volume in volumes:
            origin = points[faces[0][0]]
            total = 0.0
            for face in faces:
                a, b, c = [[points[i][k] - origin[k] for k in range(3)] for i in face]
                total += a[0] * (b[1]*c[2]-b[2]*c[1]) + a[1] * (b[2]*c[0]-b[0]*c[2]) + a[2] * (b[0]*c[1]-b[1]*c[0])
            ratio = total / (6 * rest_volume)
            sampled_min_volume, sampled_max_volume = min(sampled_min_volume, ratio), max(sampled_max_volume, ratio)
        last_time = float(t)
    for key in ("contact_count", "inverted_objects", "closed_objects"):
        if type(diagnostics.get(key)) is not int or diagnostics[key] < 0:
            raise ValueError("Mesh diagnostic " + key + " must be a non-negative integer")
    if diagnostics["closed_objects"] != closed_soft:
        raise ValueError("Mesh closed-object count disagrees with the prepared topology")
    values = {}
    for key in ("max_edge_strain", "min_volume_ratio", "max_volume_ratio", "residual_penetration_m",
                "max_contact_correction_m", "maximum_speed_m_s"):
        value = diagnostics.get(key)
        if not finite_number(value):
            raise ValueError("Mesh diagnostic " + key + " must be finite")
        values[key] = float(value)
    ranges = all(value >= 0 for value in values.values()) and values["min_volume_ratio"] <= values["max_volume_ratio"]
    tolerance = max(0.002, max(e["thickness"] for e in scene["entities"]) * 0.25)
    return {
        "mesh_diagnostics_in_physical_ranges": ranges,
        "mesh_no_global_volume_inversion": diagnostics["inverted_objects"] == 0,
        "mesh_volume_ratio_within_20_percent": not closed_soft or 0.8 <= values["min_volume_ratio"] <= values["max_volume_ratio"] <= 1.2,
        "mesh_max_edge_strain_at_most_1_5": ranges and values["max_edge_strain"] <= 1.5,
        "mesh_sampled_contact_residual_within_tolerance": ranges and values["residual_penetration_m"] <= tolerance,
        "mesh_frame_geometry_consistent_with_diagnostics": (sampled_strain <= values["max_edge_strain"] + 0.02
            and sampled_min_volume >= values["min_volume_ratio"] - 0.02
            and sampled_max_volume <= values["max_volume_ratio"] + 0.02),
    }
