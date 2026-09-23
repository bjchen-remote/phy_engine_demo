"""Particle counting, bounded resource planning, and wall-time estimates."""

from __future__ import annotations

import math
from typing import Any

from physics_demo.core.force_fields import maximum_acceleration
from physics_demo.analysis.queries import observation_plan
from physics_demo.limits import (
    EXACT_SPHERE_COUNT_CELLS,
    MAX_FRAME_PARTICLE_SAMPLES,
    NBODY_MAX_INITIAL_STEP_RATIO,
    QUALITY_LIMITS,
)


_COUNT_OVERFLOW = 1_000_000_000_000_000_000

# The native video backends both render 960 x 540 pixels. The liquid shader
# performs four coverage and six depth smoothing sweeps per distinct material,
# plus full-frame buffer setup. Costs include their typical bounded regions.
# M4 calibration, 1,920 samples / four materials: 0.153-0.156 s per warm frame.
# The fixed term below is 0.145 s/frame; the existing sample term adds 0.0077 s.
# Keep this cost independent of render downsampling: fewer stored samples do
# not eliminate pixel buffers, material passes, or fixed output frame counts.
_VIDEO_FRAME_PIXELS = 960 * 540
_LIQUID_SMOOTHING_PASSES = 4 + 6
_LIQUID_SETUP_SECONDS_PER_PIXEL = 2.0e-8
_LIQUID_PASS_SECONDS_PER_PIXEL = 6.5e-9


def _liquid_video_cost(scene: dict[str, Any], frames: int, include_video: bool) -> dict[str, Any]:
    materials = {
        entity.get("preset", "water")
        for entity in scene["entities"] if entity["type"] == "fluid"
    } if include_video else set()
    material_count = len(materials)
    p50 = frames * _VIDEO_FRAME_PIXELS * (
        _LIQUID_SETUP_SECONDS_PER_PIXEL
        + material_count * _LIQUID_SMOOTHING_PASSES * _LIQUID_PASS_SECONDS_PER_PIXEL
    ) if material_count else 0.0
    return {
        "liquid_materials_per_frame": material_count,
        "liquid_pixels_per_frame": _VIDEO_FRAME_PIXELS if material_count else 0,
        "liquid_smoothing_passes_per_material": _LIQUID_SMOOTHING_PASSES if material_count else 0,
        "liquid_surface_p50_s": p50,
        "liquid_surface_p90_s": 1.6 * p50,
    }


def _account_for_delegated_liquid_video(
    scene: dict[str, Any], plan: dict[str, Any], include_video: bool,
) -> dict[str, Any]:
    """Specialized solver plans still use the shared continuous liquid shader."""
    cost = _liquid_video_cost(scene, int(plan["output_frames"]), include_video)
    if not cost["liquid_materials_per_frame"]:
        return plan
    timing = plan["timing_estimate"]
    for quantile in ("p50", "p90"):
        extra = cost[f"liquid_surface_{quantile}_s"]
        for component in ("video", "total"):
            key = f"{component}_{quantile}_s"
            timing[key] = round(timing[key] + extra, 3)
    timing.update({key: round(value, 3) if isinstance(value, float) else value
                   for key, value in cost.items()})
    timing["fits_budget"] = timing["total_p90_s"] <= timing["hard_limit_s"]
    if timing["video_p90_s"] > timing["solver_p90_s"]:
        timing["limiting_factor"] = "continuous liquid surfaces and video encoding"
    return plan


def _axis_values(center: float, extent: float, spacing: float) -> list[float]:
    count = max(1, int(math.floor(extent / spacing)))
    start = center - 0.5 * spacing * (count - 1)
    return [start + index * spacing for index in range(count)]


def _safe_axis_count(extent: float, spacing: float) -> int:
    ratio = extent / spacing if spacing > 0.0 else math.inf
    if not math.isfinite(ratio) or ratio >= 1.0e9:
        return 1_000_000_000
    return max(1, int(math.floor(ratio)))


def _shape_particle_count(shape: dict[str, Any], spacing: float) -> int:
    """Count a lattice without expanding hostile or very large volumes."""
    if not math.isfinite(spacing) or spacing <= 0.0:
        return _COUNT_OVERFLOW
    if shape.get("type") == "box":
        size = [float(value) for value in shape.get("size", [0.0, 0.0, 0.0])]
        return math.prod(_safe_axis_count(extent, spacing) for extent in size)
    if shape.get("type") == "sphere":
        center = [float(value) for value in shape.get("center", [0.0, 0.0, 0.0])]
        radius = max(0.0, float(shape.get("radius", 0.0)))
        axis_count = _safe_axis_count(2.0 * radius, spacing)
        if axis_count**3 > EXACT_SPHERE_COUNT_CELLS:
            ratio = max(radius - 0.18 * spacing, 0.0) / spacing
            if not math.isfinite(ratio) or ratio >= 1.0e6:
                return _COUNT_OVERFLOW
            return max(1, int((4.0 / 3.0) * math.pi * ratio**3))
        values = [_axis_values(center[axis], 2.0 * radius, spacing) for axis in range(3)]
        limit = max(0.0, radius - 0.18 * spacing) ** 2
        return max(1, sum(
            1
            for x in values[0]
            for y in values[1]
            for z in values[2]
            if (x - center[0]) ** 2 + (y - center[1]) ** 2 + (z - center[2]) ** 2 <= limit
        ))
    return 0


def estimate_particle_count(scene: dict[str, Any], spacing_override: float | None = None) -> int:
    total = 0
    entities = scene.get("entities", [])
    if not isinstance(entities, list):
        return 0
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_type = entity.get("type")
        if not isinstance(entity_type, str) or entity_type not in {"fluid", "granular"}:
            continue
        shape = entity.get("shape")
        if not isinstance(shape, dict):
            continue
        shape_type = shape.get("type")
        if not isinstance(shape_type, str) or shape_type not in {"box", "sphere"}:
            continue
        try:
            spacing = float(entity.get("spacing", 0.12)) if spacing_override is None else spacing_override
            total += _shape_particle_count(shape, spacing)
        except (OverflowError, TypeError, ValueError):
            continue
    return total


def _vector_magnitude(value: list[float]) -> float:
    return math.sqrt(sum(component * component for component in value))


def _nbody_initial_step_ratio(scene: dict[str, Any]) -> float:
    """Estimate dt / shortest initial softened two-body dynamical time."""
    interactions = scene["interactions"]
    if not interactions["mutual_gravity"]:
        return 0.0
    bodies = [entity for entity in scene["entities"] if entity["type"] == "point_mass"]
    if len(bodies) < 2:
        return 0.0
    gravitational_constant = float(interactions["gravity_G"])
    if gravitational_constant <= 0.0:
        return 0.0
    softening_sq = float(interactions["softening"]) ** 2
    dt = float(scene["world"]["dt"])
    maximum_ratio = 0.0
    for first_index, first in enumerate(bodies):
        for second in bodies[first_index + 1:]:
            separation_sq = sum(
                (float(second["position"][axis]) - float(first["position"][axis])) ** 2
                for axis in range(3)
            ) + softening_sq
            pair_mass = float(first["mass"]) + float(second["mass"])
            if pair_mass <= 0.0:
                continue
            dynamical_time = separation_sq ** 0.75 / math.sqrt(gravitational_constant * pair_mass)
            maximum_ratio = max(maximum_ratio, dt / max(dynamical_time, 1.0e-30))
    return maximum_ratio


def timing_estimate(
    scene: dict[str, Any],
    plan: dict[str, Any],
    include_video: bool,
    *,
    legacy_video_estimate: bool = False,
) -> dict[str, Any]:
    particles = int(plan["planned_particles"])
    bodies = sum(entity["type"] == "point_mass" for entity in scene["entities"])
    rigid_count = sum(entity["type"] in {"rigid", "slider"} for entity in scene["entities"])
    steps = int(plan["steps"])
    iterations = int(plan["density_iterations"]) + int(plan["divergence_iterations"])
    spacing = float(plan["effective_spacing"])
    duration = float(scene["world"]["duration"])
    dt = float(scene["world"]["dt"])
    maximum_initial_speed = max(
        (_vector_magnitude(entity.get("velocity", [0.0, 0.0, 0.0])) for entity in scene["entities"]),
        default=0.0,
    )
    acceleration = _vector_magnitude(scene["world"]["gravity"]) + maximum_acceleration(scene.get("force_fields", []))
    representative_speed = maximum_initial_speed + 0.45 * acceleration * duration
    interactions = scene["interactions"]
    self_gravity = particles > 0 and interactions["mutual_gravity"] and interactions["gravity_G"] > 0
    if self_gravity:
        # Collective collapse creates internal speeds even when world gravity
        # and all initial velocities are zero. Account for the binding scale,
        # not just the cheap initial, well-separated force evaluation.
        volume = particles * spacing**3
        radius = max(spacing, (3 * volume / (4 * math.pi))**(1/3))
        representative_speed += math.sqrt(interactions["gravity_G"] *
            interactions["particle_gravity_density"] * volume / radius)
    transport_speed = representative_speed + math.sqrt(max(acceleration * spacing, 0.0))
    expected_substeps = max(1, min(
        128 if self_gravity else int(plan["max_substeps"]),
        math.ceil(dt * transport_speed / max(0.4 * spacing, 1.0e-9)),
    ))
    baseline_substeps = {"preview": 2.5, "balanced": 3.5, "high": 6.5}[plan["quality"]]
    cfl_factor = min(2.5, max(0.65, expected_substeps / baseline_substeps))
    fields_factor = 1.0 + 0.035 * len(scene.get("force_fields", []))
    collider_factor = 1.0 + 0.01 * len(scene.get("colliders", []))
    if particles:
        complexity = steps * particles * iterations
        solver_p50 = 0.12 + 6.5e-7 * complexity * cfl_factor * fields_factor * collider_factor
        if plan["backend"] == "python":
            solver_p50 *= 75.0
    else:
        pair_work = steps * max(bodies * max(bodies - 1, 1), 1)
        solver_p50 = 0.001 + 2.5e-8 * pair_work * fields_factor
    if plan["backend"] == "slider":
        solver_p50 = 0.01 + steps * max(rigid_count, 1) ** 2 * 2.5e-6
    gravity_cost = 0.0
    if self_gravity:
        gravity_steps = max(expected_substeps, math.ceil(dt * math.sqrt(
            interactions["gravity_G"] * interactions["particle_gravity_density"]) / 0.2))
        work = (particles * particles if interactions["gravity_theta"] == 0 else
                particles * max(1.0, math.log2(max(particles, 2))) * 30 * (0.5 / max(interactions["gravity_theta"], 0.05))**2)
        gravity_cost = 8e-9 * steps * gravity_steps * work
        solver_p50 += gravity_cost
    measurement_p50 = 0.0
    if scene.get("queries"):
        measurements = observation_plan(scene, plan)
        work_cost = 2e-8 if plan["backend"] == "native" else 1e-6
        measurement_p50 = measurements["work_units"] * work_cost + measurements["scalar_capacity"] * 3e-6
        solver_p50 += measurement_p50
    solver_p90 = 0.15 + 1.7 * solver_p50
    frame_samples = int(plan["frame_particle_samples"])
    frame_count = int(plan["output_frames"])
    liquid_cost = _liquid_video_cost(
        scene, frame_count, include_video and not legacy_video_estimate,
    )
    if include_video:
        video_p50 = (0.20 + 4.0e-6 * frame_samples
                     + 1.0e-5 * frame_count * (bodies + rigid_count)
                     + liquid_cost["liquid_surface_p50_s"])
        video_p90 = 0.15 + 1.6 * video_p50
    else:
        video_p50 = 8.0e-7 * frame_samples
        video_p90 = 1.6 * video_p50
    cold_compile_p90 = 2.0 if plan["backend"] == "native" else 0.0
    total_p50 = solver_p50 + video_p50
    total_p90 = solver_p90 + video_p90 + cold_compile_p90
    hard_limit = float(scene["budget"]["wall_time_s"])
    components = {
        "native solver and adaptive substeps": solver_p90,
        ("frame conversion and H.264 encoding" if legacy_video_estimate
         else "continuous surfaces, frame conversion and video encoding"): video_p90,
        "cold native compilation": cold_compile_p90,
    }
    estimate = {
        "hardware_profile": "Apple M4 10-core calibration; other machines should treat this as a relative estimate",
        "physical_duration_s": duration,
        "expected_cfl_substeps_per_step": expected_substeps,
        "solver_p50_s": round(solver_p50, 3),
        "particle_gravity_p50_s": round(gravity_cost, 3),
        "solver_p90_s": round(solver_p90, 3),
        "video_p50_s": round(video_p50, 3),
        "video_p90_s": round(video_p90, 3),
        "cold_compile_p90_s": round(cold_compile_p90, 3),
        "total_p50_s": round(total_p50, 3),
        "total_p90_s": round(total_p90, 3),
        "hard_limit_s": hard_limit,
        "fits_budget": total_p90 <= hard_limit,
        "confidence": (
            "calibrated range; contact density, CFL events, compiler cache, and thermal state can move actual time"
            if legacy_video_estimate else
            "calibrated range; contact density, liquid screen coverage, CFL events, compiler cache, and thermal state can move actual time"
        ),
        "limiting_factor": max(components, key=components.get),
    }
    if not legacy_video_estimate:
        estimate.update({key: round(value, 3) if isinstance(value, float) else value
                         for key, value in liquid_cost.items()})
    if scene.get("queries"):
        estimate["measurement_p50_s"] = round(measurement_p50, 3)
    return estimate


def make_plan(
    scene: dict[str, Any],
    quality_override: str | None = None,
    include_video: bool = True,
    particle_cap_override: int | None = None,
    *,
    legacy_video_estimate: bool = False,
) -> dict[str, Any]:
    from physics_demo.io.coupled import enabled as coupled_enabled, make_plan as coupled_plan
    if coupled_enabled(scene):
        delegated = coupled_plan(
            scene,
            include_video,
            legacy_video_estimate=legacy_video_estimate,
        )
        return (delegated if legacy_video_estimate else
                _account_for_delegated_liquid_video(scene, delegated, include_video))
    if scene.get("connections"):
        from physics_demo.io.connections import make_plan as connection_plan
        return connection_plan(scene, include_video)
    if any(entity["type"] == "mesh" for entity in scene["entities"]):
        from physics_demo.io.mesh_scene import make_mesh_plan
        return make_mesh_plan(scene, include_video)
    quality = quality_override or scene["budget"]["quality"]
    config = QUALITY_LIMITS[quality]
    particle_limit = config["particles"]
    if particle_cap_override is not None:
        particle_limit = max(1, min(particle_limit, int(particle_cap_override)))
    requested = estimate_particle_count(scene)
    requested_spacing = min(
        [float(entity.get("spacing", 0.12)) for entity in scene["entities"] if entity.get("type") in {"fluid", "granular"}],
        default=0.12,
    )
    uniform_requested = estimate_particle_count(scene, requested_spacing)
    scale = (uniform_requested / particle_limit) ** (1.0 / 3.0) if uniform_requested > particle_limit else 1.0
    spacing = requested_spacing * max(1.0, scale)
    actual = estimate_particle_count(scene, spacing)
    planning_iterations = 0
    while actual > particle_limit and planning_iterations < 96:
        spacing *= 1.02
        actual = estimate_particle_count(scene, spacing)
        planning_iterations += 1
    if actual > particle_limit:
        actual = particle_limit
    duration = float(scene["world"]["duration"])
    requested_dt = float(scene["world"]["dt"])
    output_fps = float(scene["world"]["output_fps"])
    # Frame sampling interpolates across physics steps and must not change the
    # requested timestep count.  Reserve one initial frame, every interior FPS
    # timestamp, and one terminal frame (which may coincide with an FPS tick).
    steps = math.ceil(duration / requested_dt)
    output_frames = max(1, math.ceil(duration * output_fps - 1.0e-10)) + 1
    sample_limited_render = max(1, MAX_FRAME_PARTICLE_SAMPLES // max(output_frames, 1))
    render_limit = min(config["render_particles"], sample_limited_render, max(actual, 1))
    adjustments: list[str] = []
    if uniform_requested != requested:
        adjustments.append(
            "The native single-resolution SPH/PBD solve unified particle entities at "
            f"the finest requested spacing ({requested_spacing:.6g} m): "
            f"{requested} independently requested samples became {uniform_requested} before budget coarsening."
        )
    if spacing > requested_spacing * (1.0 + 1.0e-9):
        adjustments.append(f"Increased particle spacing from {requested_spacing:.6g} m to {spacing:.6g} m to respect the {particle_limit} particle cap.")
    dynamic_rigid = any(entity["type"] == "rigid" and entity.get("mass", 0.0) > 0.0 for entity in scene["entities"])
    selected_backend = "python" if scene["budget"].get("backend") == "python" or dynamic_rigid else "native"
    if any(entity["type"] == "slider" for entity in scene["entities"]):
        selected_backend = "slider"
    plan: dict[str, Any] = {
        "quality": quality,
        "requested_backend": scene["budget"].get("backend", "auto"),
        "backend": selected_backend,
        "requested_particles": requested,
        "uniform_spacing_particles_before_cap": uniform_requested,
        "resolution_policy": "single-resolution; use the finest entity spacing, then coarsen all particle entities together when required",
        "particle_limit": particle_limit,
        "planned_particles": actual,
        "effective_spacing": spacing,
        "solver_iterations": config["iterations"],
        "density_iterations": config["iterations"],
        "divergence_iterations": config["divergence_iterations"],
        "render_particle_limit": render_limit,
        "threads": config["threads"],
        "max_substeps": config["max_substeps"],
        "steps": steps,
        "output_frames": output_frames,
        "complexity_units": steps * max(actual, 1) * (config["iterations"] + config["divergence_iterations"]),
        "wall_time_limit_s": scene["budget"]["wall_time_s"],
        "frame_particle_samples": output_frames * min(actual, render_limit),
        "estimated_peak_memory_mb": 0.0,
        "adjustments": adjustments,
        "note": "The p50/p90 estimate is calibrated on Apple M4; the cooperative hard deadline remains authoritative.",
    }
    nbody_step_ratio = _nbody_initial_step_ratio(scene)
    plan["nbody_initial_step_ratio"] = nbody_step_ratio
    plan["nbody_timestep_feasible"] = nbody_step_ratio <= NBODY_MAX_INITIAL_STEP_RATIO
    plan["timing_estimate"] = timing_estimate(
        scene, plan, include_video, legacy_video_estimate=legacy_video_estimate,
    )

    minimum_particles = max(
        64 if requested else 0,
        sum(entity.get("type") in {"fluid", "granular"} for entity in scene["entities"]),
    )
    budget_iterations = 0
    # Coarsening cannot fix a video whose mandatory material/pixel passes alone
    # exceed the deadline. Preserve the requested resolution for an honest
    # rejection instead of degrading it while the irreducible cost stays fixed.
    while (not plan["timing_estimate"]["fits_budget"]
           and (legacy_video_estimate
                or plan["timing_estimate"]["liquid_surface_p90_s"] < scene["budget"]["wall_time_s"])
           and actual > minimum_particles and budget_iterations < 64):
        old_actual = actual
        spacing *= 1.10
        actual = estimate_particle_count(scene, spacing)
        budget_iterations += 1
        if actual >= old_actual and actual <= minimum_particles:
            break
        plan["effective_spacing"] = spacing
        plan["planned_particles"] = actual
        render_limit = min(config["render_particles"], sample_limited_render, max(actual, 1))
        plan["render_particle_limit"] = render_limit
        plan["frame_particle_samples"] = output_frames * min(actual, render_limit)
        plan["complexity_units"] = steps * max(actual, 1) * (config["iterations"] + config["divergence_iterations"])
        plan["timing_estimate"] = timing_estimate(
            scene, plan, include_video, legacy_video_estimate=legacy_video_estimate,
        )
    if budget_iterations:
        plan["adjustments"].append(
            f"Increased spacing to {spacing:.6g} m so the calibrated runtime approaches the {scene['budget']['wall_time_s']:.3g} s budget."
        )
    if render_limit < min(config["render_particles"], actual):
        plan["adjustments"].append(
            f"Reduced rendered particles to keep frame-particle samples below {MAX_FRAME_PARTICLE_SAMPLES}."
        )
    interactions = scene["interactions"]
    if actual and interactions["mutual_gravity"]:
        plan["particle_gravity"] = {
            "algorithm": "direct" if interactions["gravity_theta"] == 0 else "Barnes-Hut",
            "opening_angle": interactions["gravity_theta"],
            "softening_m": interactions["softening"],
            "reference_density_kg_m3": interactions["particle_gravity_density"],
            "particle_mass_kg": interactions["particle_gravity_density"] * spacing**3,
            "represented_mass_kg": actual * interactions["particle_gravity_density"] * spacing**3,
            "scope": "fluid/granular particles; shared reference density; no point-mass exchange",
        }
    bounds = scene["world"]["bounds"]
    bounds_spans = [float(bounds["max"][axis]) - float(bounds["min"][axis]) for axis in range(3)]
    particle_diameter = 0.92 * spacing if actual > 0 else 0.0
    dynamic_rigid_diameter = max(
        (
            2.0 * float(entity["shape"]["radius"])
            for entity in scene["entities"]
            if entity.get("type") == "rigid" and float(entity.get("mass", 0.0)) > 0.0
            and entity.get("shape", {}).get("type") == "sphere"
        ),
        default=0.0,
    )
    minimum_bounds_span = max(particle_diameter, dynamic_rigid_diameter)
    plan["minimum_particle_bounds_span_m"] = minimum_bounds_span
    plan["bounds_spans_m"] = bounds_spans
    plan["bounds_feasible"] = minimum_bounds_span == 0.0 or all(
        span + 1.0e-12 >= minimum_bounds_span for span in bounds_spans
    )
    bounds_minimum = [float(value) for value in bounds["min"]]
    bounds_maximum = [float(value) for value in bounds["max"]]
    initial_bounds_violations: list[str] = []
    particle_radius = 0.46 * spacing
    for entity in scene["entities"]:
        kind = entity.get("type")
        lower: list[float] | None = None
        upper: list[float] | None = None
        if kind in {"fluid", "granular"}:
            shape = entity["shape"]
            center = [float(value) for value in shape["center"]]
            if shape["type"] == "sphere":
                radius = float(shape["radius"])
                lattice_half = 0.5 * spacing * (_safe_axis_count(2.0 * radius, spacing) - 1)
                sampled_half = min(lattice_half, max(0.0, radius - 0.18 * spacing))
                half = [sampled_half + particle_radius] * 3
            else:
                # Particle volumes describe the requested source envelope, while
                # sample_shape places centres on a centred lattice inside it.
                # Check the support of those actual samples: expanding the raw
                # envelope by radius falsely rejects canonical sources whose box
                # face intentionally coincides with a world boundary.
                half = [
                    0.5 * spacing * (_safe_axis_count(float(value), spacing) - 1) + particle_radius
                    for value in shape["size"]
                ]
            lower = [center[axis] - half[axis] for axis in range(3)]
            upper = [center[axis] + half[axis] for axis in range(3)]
        elif (
            kind == "rigid"
            and float(entity.get("mass", 0.0)) > 0.0
            and entity.get("shape", {}).get("type") == "sphere"
        ):
            center = [float(value) for value in entity["position"]]
            half = [float(entity["shape"]["radius"])] * 3
            lower = [center[axis] - half[axis] for axis in range(3)]
            upper = [center[axis] + half[axis] for axis in range(3)]
        if lower is None or upper is None:
            continue
        outside_axes = [
            "xyz"[axis]
            for axis in range(3)
            if lower[axis] < bounds_minimum[axis] - 1.0e-12
            or upper[axis] > bounds_maximum[axis] + 1.0e-12
        ]
        if outside_axes:
            initial_bounds_violations.append(
                f"{entity['id']} ({'/'.join(outside_axes)} outside after radius margin)"
            )
    plan["initial_bounds_feasible"] = not initial_bounds_violations
    plan["initial_bounds_violations"] = initial_bounds_violations
    sample_bytes = plan["frame_particle_samples"] * 3 * 4
    state_bytes = max(actual, 1) * 720
    if scene["interactions"]["mutual_gravity"] and scene["interactions"]["gravity_G"] > 0:
        state_bytes += actual * 180  # Octree nodes, index arrays and accelerations.
    json_bytes = plan["frame_particle_samples"] * 72
    plan["estimated_peak_memory_mb"] = round((16_000_000 + sample_bytes + state_bytes + json_bytes) / 1_000_000, 1)
    if scene.get("queries"):
        plan["measurement_plan"] = observation_plan(scene, plan)
        plan["estimated_peak_memory_mb"] += plan["measurement_plan"]["estimated_memory_mb"]
    return plan


def plan_report(plan: dict[str, Any]) -> dict[str, Any]:
    """Flatten the fields an agent must disclose before starting a run."""
    timing = plan["timing_estimate"]
    report = {
        "decision": "run" if timing["fits_budget"] and plan.get("bounds_feasible", True) and plan.get("initial_bounds_feasible", True) and plan.get("nbody_timestep_feasible", True) and plan.get("measurement_plan", {}).get("fits_limits", True) else "revise_scene",
        "physical_duration_s": timing["physical_duration_s"],
        "estimated_wall_time_s": {
            "p50": timing["total_p50_s"],
            "p90": timing["total_p90_s"],
            "hard_limit": timing["hard_limit_s"],
        },
        "fits_budget": timing["fits_budget"],
        "bounds_feasible": plan.get("bounds_feasible", True),
        "initial_bounds_feasible": plan.get("initial_bounds_feasible", True),
        "initial_bounds_violations": plan.get("initial_bounds_violations", []),
        "minimum_particle_bounds_span_m": plan.get("minimum_particle_bounds_span_m", 0.0),
        "nbody_initial_step_ratio": plan.get("nbody_initial_step_ratio", 0.0),
        "nbody_timestep_feasible": plan.get("nbody_timestep_feasible", True),
        "backend": plan["backend"],
        "quality": plan["quality"],
        "requested_particles": plan["requested_particles"],
        "uniform_spacing_particles_before_cap": plan["uniform_spacing_particles_before_cap"],
        "resolution_policy": plan["resolution_policy"],
        "planned_particles": plan["planned_particles"],
        "rendered_particles_per_frame": min(plan["planned_particles"], plan["render_particle_limit"]),
        "effective_spacing_m": plan["effective_spacing"],
        "output_frames": plan["output_frames"],
        "frame_particle_samples": plan["frame_particle_samples"],
        "estimated_peak_memory_mb": plan["estimated_peak_memory_mb"],
        "adjustments": list(plan["adjustments"]),
        "required_disclosure": "Report this object before simulation; adjustments change discretization or display sampling.",
    }
    if "particle_gravity" in plan:
        report["particle_gravity"] = plan["particle_gravity"]
    if "measurement_plan" in plan:
        report["measurement_plan"] = plan["measurement_plan"]
    if plan["backend"] == "mesh":
        report.update({key: plan[key] for key in ("mesh_vertices", "mesh_soft_vertices", "mesh_triangles", "mesh_frame_vertex_samples", "mesh_substeps", "mesh_iterations", "mesh_fits_limits")})
        if not plan["mesh_fits_limits"]:
            report["decision"] = "revise_scene"
    if plan["backend"] == "connections":
        report.update({key: plan[key] for key in ("connection_nodes", "connection_count", "connection_substeps",
                      "connection_iterations", "connection_omega_bound_rad_s", "connection_damping_bound_s_inv", "connection_fits_limits")})
        if not plan["connection_fits_limits"]:
            report["decision"] = "revise_scene"
    if plan["backend"] == "coupled":
        report.update({key: plan[key] for key in ("coupling_points", "coupling_rigid_bodies", "connection_count", "coupling_substeps", "coupling_initial_cfl_substeps", "coupling_event_headroom", "coupling_iterations", "coupling_work_units", "coupling_fits_limits")})
        report.update({key: plan[key] for key in ("mesh_vertices", "mesh_triangles") if key in plan})
        if not plan["coupling_fits_limits"]:
            report["decision"] = "revise_scene"
    return report
