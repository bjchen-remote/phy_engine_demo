"""Saved-result integrity, numerical quality gates, and compact summaries.

All recovery/query paths use these same checks; rendering and solver execution
remain in runner.py. No validation rule depends on the presentation frames for
quantitative measurements.
"""
from __future__ import annotations

import copy
import hashlib
import math
from pathlib import Path
from typing import Any

from physics_demo.io.jsonio import canonical_bytes as _json_bytes, loads as strict_json_loads
from physics_demo.limits import NBODY_MAX_RELATIVE_ENERGY_DRIFT
from physics_demo.core.math3d import finite_number, finite_vec
from physics_demo.io.coupled import enabled as coupled_enabled
from physics_demo.io.planning import make_plan, plan_report
from physics_demo.analysis.queries import MAX_MEASUREMENT_FILE_BYTES, compact as compact_measurements, digest as measurement_digest, evaluate as evaluate_queries
from physics_demo.io.schema import normalize_and_validate
from physics_demo.io.video import VideoEncodingError, probe_mp4


PHYSICS_CLAIMS = {
    "point_mass": "Quantitative only for softened Newtonian point masses with the configured fixed timestep.",
    "fluid": "Native C11 DFSPH with an Akinci surface force is the default. Liquid presets select stable model coefficients and continuous-surface appearance; they do not model calibrated density, temperature, phase change, chemistry, or non-Newtonian rheology.",
    "granular": "Visual particle/cohesion model; do not use it for soil strength or sediment transport prediction.",
    "water_sand": "Qualitative wetting-and-drag erosion proxy; displacement trends are meaningful only within this demo model.",
    "water_blob": "A free liquid volume, not a membrane-enclosed water balloon.",
    "force_fields": "Uniform, radial, and vortex fields prescribe acceleration; they do not resolve air, pumps, explosions, or electromagnetism.",
    "capsule_collider": "Capsules are analytic static boundaries with rounded ends.",
}

RESULT_VERSION = 1
_LEGACY_FLUID_CLAIM = (
    "Native C11 DFSPH with an Akinci surface force is the default; it constrains "
    "density and velocity divergence but remains an uncalibrated visual fluid model."
)


def physics_claims(scene: dict[str, Any] | None = None) -> dict[str, str]:
    claims = dict(PHYSICS_CLAIMS)
    if scene is None or (scene.get("interactions", {}).get("mutual_gravity", False) and any(e["type"] in {"fluid", "granular"} for e in scene["entities"])):
        claims["particle_gravity"] = "Plummer-softened gravity on equal-volume, equal-reference-density particles; Barnes-Hut far fields or direct pairs. Coupled to incompressible DFSPH, not compressible stellar hydrodynamics. Spatial resolution, softening and timestep convergence are required for quantitative conclusions."
    if scene is not None and coupled_enabled(scene):
        claims.update(
            point_mass="Finite masses accelerated by explicit fields, attachments and contact impulses; no mutual gravity in the coupled route.",
            mesh="XPBD elastic triangle surfaces, not calibrated solid FEM; cross-domain contact reacts on both sides. Discrete contact cannot guarantee no tunnelling or self-intersection.",
            connections="Hooke springs/dashpots and rod/rope constraints attach to point centers, mesh vertices or rigid local points. Optional solid geometry is a straight collision capsule; optional mass is lumped half to point endpoints. No resolved helical wire or self-contact.",
            rigid_body="Uniform sphere/box/cylinder inertia and body-to-world unit quaternions; free angular momentum or an ideal fixed principal-axis pivot. Contact impulses apply torque.",
            coupling="Partitioned two-way contact on one shared clock; no monolithic moving-boundary fluid pressure solve or calibrated buoyancy. Momentum conservation claims require closed, unforced configurations and convergence checks.")
        return claims
    if scene is None or any(e["type"] == "mesh" for e in scene["entities"]):
        claims["mesh"] = "C11 XPBD elastic triangle-surface proxy with global closed volume; not calibrated solid FEM. Vertex-face CCD is not a general edge-edge or self-collision guarantee. No cutting, fracture, liquid coupling or image-depth measurement."
    if scene is None or scene.get("connections"):
        claims["connections"] = "Ideal massless Hooke springs and axial dashpots between point masses; rods fix length and ropes cap length with inelastic take-up. Constraints can dissipate numerical energy. No collision, bending, fracture, calibrated stress or fluid/mesh coupling. Only explicit fields accelerate nodes."
        if scene is not None:
            claims["point_mass"] = "Translating point masses accelerated by declared connections and explicit fields; no collision, rotation or mutual gravity in this route."
    return claims

def verified_measurements(result: dict[str, Any], root: Path | None) -> dict[str, Any]:
    scene = result["scene"]
    expected = evaluate_queries(scene, result["plan"], result["trajectory"], result["run_id"], result["scene_hash"])
    raw_path = result.get("artifacts", {}).get("measurements")
    if not isinstance(raw_path, str):
        raise ValueError("Declared queries require a measurements.json artifact")
    path = Path(raw_path).expanduser()
    if not path.is_absolute() or path.is_symlink() or path.name != "measurements.json":
        raise ValueError("Measurements must use an absolute run-local regular measurements.json")
    try:
        path = path.resolve(strict=True)
        if root is not None and path.parent != root.resolve():
            raise ValueError("Measurement artifact is outside the run directory")
        if not path.is_file() or path.stat().st_size > MAX_MEASUREMENT_FILE_BYTES:
            raise ValueError("Measurement artifact is not a bounded regular file")
        stored = strict_json_loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError("Measurement artifact cannot be read: " + str(error)) from error
    expected_digest = measurement_digest(expected)
    if result.get("measurements_sha256") != expected_digest or measurement_digest(stored) != expected_digest:
        raise ValueError("Measurements disagree with their scene, raw solver observations, or declared result digest")
    return expected


def _verify_video_artifact(
    result: dict[str, Any],
    artifact_root: Path | None,
    verified_probe: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, dict):
        return False, "artifacts must be an object"
    video = artifacts.get("video")
    if not isinstance(video, dict):
        return False, "video metadata is absent"
    raw_path = video.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return False, "video.path is absent"
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        return False, "video.path must be absolute"
    if candidate.is_symlink():
        return False, "video.path may not be a symbolic link"
    try:
        resolved = candidate.resolve(strict=True)
        stat = resolved.stat()
    except (OSError, RuntimeError) as error:
        return False, f"video file is unavailable: {error}"
    if artifact_root is not None and resolved.parent != artifact_root.resolve():
        return False, "video file is outside the run directory"
    if resolved.name != "simulation.mp4" or not resolved.is_file():
        return False, "video artifact is not the run's regular simulation.mp4 file"
    expected_bytes = video.get("bytes")
    if not isinstance(expected_bytes, int) or isinstance(expected_bytes, bool) or expected_bytes != stat.st_size:
        return False, "video byte count does not match result metadata"
    if stat.st_size <= 1000:
        return False, "video artifact is unexpectedly small"
    try:
        with resolved.open("rb") as handle:
            header = handle.read(32)
    except OSError as error:
        return False, f"video header cannot be read: {error}"
    if b"ftyp" not in header:
        return False, "video artifact has no MP4 ftyp header"
    if verified_probe is None:
        try:
            probe = probe_mp4(resolved)
        except VideoEncodingError as error:
            return False, f"video track or one of its frames is invalid: {error}"
    elif isinstance(verified_probe, dict):
        probe = verified_probe
    else:
        return False, "trusted video probe metadata is invalid"
    expected_width = video.get("width")
    expected_height = video.get("height")
    if (
        not isinstance(expected_width, int)
        or isinstance(expected_width, bool)
        or not isinstance(expected_height, int)
        or isinstance(expected_height, bool)
        or probe["first_frame_width"] != expected_width
        or probe["first_frame_height"] != expected_height
    ):
        return False, "video dimensions do not match result metadata"
    expected_codec_tag = video.get("codec_tag")
    if expected_codec_tag is None:
        expected_codec_tag = {"H.264": "avc1", "Motion JPEG": "jpeg"}.get(video.get("codec"))
    if not isinstance(expected_codec_tag, str) or probe["codec_tag"] != expected_codec_tag:
        return False, "video codec does not match result metadata"
    trajectory = result.get("trajectory")
    frames = trajectory.get("frames") if isinstance(trajectory, dict) else None
    expected_sample_count = video.get("sample_count")
    if (
        not isinstance(frames, list)
        or not frames
        or not isinstance(expected_sample_count, int)
        or isinstance(expected_sample_count, bool)
        or expected_sample_count <= 0
        or not isinstance(probe.get("sample_count"), int)
        or isinstance(probe.get("sample_count"), bool)
        or probe["sample_count"] != expected_sample_count
    ):
        return False, "video sample count is invalid"
    presentation = video.get("presentation")
    if presentation is None:
        if expected_sample_count != len(frames):
            return False, "video sample count does not match the trajectory frames"
    elif (
        not isinstance(presentation, dict)
        or presentation.get("mode") != "piecewise-linear-display-retiming-v1"
        or presentation.get("solver_result_unchanged") is not True
        or presentation.get("sample_count") != expected_sample_count
        or presentation.get("fps") != video.get("fps")
    ):
        return False, "video presentation metadata is invalid"
    expected_duration = video.get("duration_s")
    fps = video.get("fps")
    if (
        not isinstance(expected_duration, (int, float))
        or isinstance(expected_duration, bool)
        or not math.isfinite(float(expected_duration))
        or float(expected_duration) < 0
        or not isinstance(fps, int)
        or isinstance(fps, bool)
        or fps <= 0
        or abs(probe["track_duration_s"] - float(expected_duration))
        > max(1.0e-6, 1.0e-6 * abs(float(expected_duration)))
    ):
        return False, "video duration does not match result metadata"
    if presentation is not None:
        playback_duration = presentation.get("playback_duration_s")
        source_duration = presentation.get("source_physical_duration_s")
        if (
            not isinstance(playback_duration, (int, float))
            or isinstance(playback_duration, bool)
            or not math.isfinite(float(playback_duration))
            or abs(float(playback_duration) - float(expected_duration))
            > max(1.0e-6, 1.0e-6 * abs(float(expected_duration)))
            or not isinstance(source_duration, (int, float))
            or isinstance(source_duration, bool)
            or not math.isfinite(float(source_duration))
        ):
            return False, "video presentation duration metadata is invalid"
    physical_duration = video.get("physical_duration_s")
    time_scale = video.get("time_scale_to_physical")
    diagnostics = trajectory.get("diagnostics") if isinstance(trajectory, dict) else None
    simulated_duration = (
        diagnostics.get("simulated_time_s") if isinstance(diagnostics, dict) else None
    )
    numeric_values = (physical_duration, time_scale, simulated_duration)
    if any(
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) < 0
        for value in numeric_values
    ):
        return False, "video physical-duration mapping is absent or invalid"
    if abs(float(physical_duration) - float(simulated_duration)) > max(
        1.0e-9, 1.0e-9 * abs(float(simulated_duration))
    ):
        return False, "video physical duration does not match the simulated duration"
    if presentation is not None and abs(
        float(presentation["source_physical_duration_s"])
        - float(simulated_duration)
    ) > max(1.0e-9, 1.0e-9 * abs(float(simulated_duration))):
        return False, "video presentation source duration does not match the simulation"
    expected_time_scale = float(physical_duration) / float(expected_duration)
    if abs(float(time_scale) - expected_time_scale) > max(
        1.0e-9, 1.0e-9 * abs(expected_time_scale)
    ):
        return False, "video physical time scale is inconsistent"
    decode_status = probe["status"]
    if decode_status == "passed":
        verification = "verified MP4 video track, dimensions, duration, codec, and every frame"
    elif decode_status == "imageio_all_samples_validated_avasset_sandbox_unavailable":
        verification = "verified MP4 track and every Motion JPEG sample with ImageIO; AVAsset pixel output is sandbox-blocked"
    elif decode_status == "software_all_mjpeg_samples_validated":
        verification = "verified MP4 track, sample table, dimensions, and JPEG framing for every Motion JPEG frame without hardware services"
    else:
        return False, "video full-track validation returned an unsupported status"
    return True, verification


def _required_bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{path} must be a JSON boolean")
    return value


def _required_number(value: Any, path: str, *, nonnegative: bool = False) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"{path} must be a JSON number")
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(f"{path} must be a finite JSON number") from error
    if not math.isfinite(number) or (nonnegative and number < 0.0):
        qualifier = "a finite non-negative" if nonnegative else "a finite"
        raise ValueError(f"{path} must be {qualifier} JSON number")
    return number


def _required_nonnegative_integer(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TypeError(f"{path} must be a non-negative JSON integer")
    return value


def _optional_diagnostic_number(diagnostics: dict[str, Any], key: str) -> float:
    value = diagnostics.get(key)
    if value is None:
        return math.nan
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError(f"trajectory.diagnostics.{key} must be a JSON number")
    try:
        return float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise ValueError(f"trajectory.diagnostics.{key} must be a finite JSON number") from error


_ALLOWED_RESULT_BACKENDS = {
    "native-c11-coupled",
    "native-c11-connections",
    "native-c11-xpbd-mesh",
    "analytic-1d-slider",
    "python-reference",
    "native-c11-dfsph",
    "native-c11-dfsph+fields",
    "native-c11-dfsph+verlet",
    "native-c11-dfsph+verlet+fields",
    "native-c11-dfsph+pbd",
    "native-c11-dfsph+pbd+fields",
    "native-c11-dfsph+pbd+verlet",
    "native-c11-dfsph+pbd+verlet+fields",
    "native-c11-verlet",
    "native-c11-verlet+fields",
}


def _validated_result_scene(
    result: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bool, bool]:
    """Return the exact normalized scene bound to a saved result."""
    legacy_result = "result_version" not in result
    version = result.get("result_version")
    if not legacy_result and (
        not isinstance(version, int) or isinstance(version, bool) or version != RESULT_VERSION
    ):
        raise ValueError(f"result_version must be {RESULT_VERSION} or absent for a legacy result")
    scene = result.get("scene")
    if not isinstance(scene, dict):
        raise TypeError("scene must be an object")
    report = normalize_and_validate(copy.deepcopy(scene))
    if not report["valid"]:
        raise ValueError("scene is not a valid normalized simulation scene")
    normalized = report["scene"]
    legacy_liquid_scene = False
    if legacy_result and any(
        entity.get("type") == "fluid" and "preset" in entity
        for entity in scene.get("entities", [])
        if isinstance(entity, dict)
    ):
        raise ValueError(
            "an unversioned legacy result cannot contain the v1 fluid preset field"
        )
    if _json_bytes(normalized) != _json_bytes(scene):
        legacy_candidate = copy.deepcopy(normalized)
        for entity in legacy_candidate.get("entities", []):
            if entity.get("type") == "fluid" and entity.get("preset") == "water":
                entity.pop("preset")
        if not legacy_result or _json_bytes(legacy_candidate) != _json_bytes(scene):
            raise ValueError("scene must contain the complete normalized scene contract")
        legacy_liquid_scene = True
        report = copy.deepcopy(report)
        report["scene"] = scene
        report["assumptions"] = [
            item.replace("preset='water', ", "")
            for item in report["assumptions"]
            if not item.startswith("Liquid preset model boundary for entity ")
        ]
    scene_hash = result.get("scene_hash")
    actual_hash = hashlib.sha256(_json_bytes(scene)).hexdigest()
    if not isinstance(scene_hash, str) or scene_hash != actual_hash:
        raise ValueError("scene_hash does not match the normalized scene")
    return scene, report, legacy_liquid_scene, legacy_result


def _completion_runtime(result: dict[str, Any], artifact_root: Path) -> float:
    """Read the runtime recorded after the latest result/summary persistence."""
    artifacts = result.get("artifacts")
    if not isinstance(artifacts, dict):
        raise TypeError("artifacts must be an object")
    raw_path = artifacts.get("completion")
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError("artifacts.completion is absent")
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute() or candidate.is_symlink():
        raise ValueError("completion path must be an absolute non-symlink path")
    resolved = candidate.resolve(strict=True)
    if resolved.parent != artifact_root.resolve() or resolved.name != "completion.json":
        raise ValueError("completion record is outside the run directory")
    if not resolved.is_file() or resolved.stat().st_size > 65_536:
        raise ValueError("completion record is not a bounded regular file")
    completion = strict_json_loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(completion, dict):
        raise TypeError("completion record must be an object")
    if completion.get("run_id") != result.get("run_id"):
        raise ValueError("completion run_id does not match result")
    if completion.get("scene_hash") != result.get("scene_hash"):
        raise ValueError("completion scene_hash does not match result")
    runtime = _required_number(
        completion.get("wall_runtime_s"), "completion.wall_runtime_s", nonnegative=True
    )
    reported = _required_number(
        result.get("total_runtime_s"), "total_runtime_s", nonnegative=True
    )
    if runtime + 1.0e-9 < reported:
        raise ValueError("completion runtime precedes the result runtime")
    if completion.get("measurement_boundary") != "through_summary_persistence":
        raise ValueError("completion measurement boundary is invalid")
    return runtime


def _apply_runtime_budget(summary: dict[str, Any], runtime: float | None = None,
                          boundary: str | None = None) -> dict[str, Any]:
    """Apply the same delivery gate to live and persisted runtime observations.

    Completion records measure through the latest summary persistence. A late
    overrun refreshes the failed summary and completion record; no record
    claims to include time spent writing itself.
    """
    elapsed = _required_number(summary["total_runtime_s"] if runtime is None else runtime,
                               "total_runtime_s", nonnegative=True)
    limit = _required_number(summary["plan"]["wall_time_limit_s"], "plan.wall_time_limit_s", nonnegative=True)
    summary["total_runtime_s"] = elapsed
    if boundary is not None:
        summary["runtime_measurement_boundary"] = boundary
    gate = summary["quality_gate"]
    within_budget = elapsed <= limit + 1e-9
    gate["runtime_checks"] = {"wall_time_within_budget": within_budget}
    if not within_budget:
        summary["ok"] = gate["passed"] = False
        summary.setdefault("stage", "quality")
        if "wall_time_within_budget" not in gate["failed_checks"]:
            gate["failed_checks"].append("wall_time_within_budget")
        errors = [error for error in summary.get("errors", []) if error.get("code") != "wall_time_budget_exceeded"]
        errors.append({"code": "wall_time_budget_exceeded", "path": "budget.wall_time_s",
                       "message": f"Measured runtime {elapsed:.6g} s exceeds the {limit:.6g} s wall-time budget.",
                       "retryable": True,
                       "suggestion": "Shorten the simulation, request fewer output frames or a smaller explicit mesh, then prepare a new run."})
        summary["errors"] = errors
    return summary


def _water_checks(diagnostics: dict[str, Any]) -> dict[str, bool]:
    """Each diagnostic belongs to one range; cross-field constraints stay explicit."""
    counts = (
        "density_iterations_total", "divergence_iterations_total",
        "density_iteration_limit_hits", "divergence_iteration_limit_hits",
        "cfl_limited_steps",
    )
    fractions = (
        "density_iteration_limit_fraction", "divergence_iteration_limit_fraction",
        "mean_sand_wetness", "close_water_particle_fraction", "planar_boundary_support_fraction",
    )
    nonnegative = (
        "max_projection_correction_m", "max_particle_contact_correction_m",
        "peak_mean_density_excess", "peak_mean_divergence_error",
        "peak_max_density_error", "peak_max_divergence_error",
        "maximum_particle_speed_m_s", "mean_sand_displacement_m",
        "final_mean_water_density_ratio", "final_max_water_density_ratio",
        "minimum_water_separation_ratio", "water_separation_p01_ratio",
    )
    quantiles = ("water_density_p50_ratio", "water_density_p95_ratio", "water_density_p99_ratio")
    positive = ("minimum_substep_s", "represented_water_volume_m3", *quantiles)
    values = {key: _optional_diagnostic_number(diagnostics, key)
              for key in (*counts, *fractions, *nonnegative, *positive, "substeps")}
    steps = values["substeps"]
    valid = (
        all(math.isfinite(value) for value in values.values())
        and steps >= 1 and steps.is_integer()
        and all(values[key] >= 0 for key in nonnegative)
        and all(0 <= values[key] <= 1 for key in fractions)
        and all(values[key] >= 0 and values[key].is_integer() for key in counts)
        and all(values[key] > 0 for key in positive)
        and values[quantiles[0]] <= values[quantiles[1]] <= values[quantiles[2]]
        and values[quantiles[2]] <= values["final_max_water_density_ratio"] + 1e-12
        and values["final_mean_water_density_ratio"] <= values["final_max_water_density_ratio"] + 1e-12
        and values["cfl_limited_steps"] <= steps
        and all(values[f"{kind}_iteration_limit_hits"] <= steps
                and abs(values[f"{kind}_iteration_limit_fraction"]
                        - values[f"{kind}_iteration_limit_hits"] / steps) <= 1e-12
                for kind in ("density", "divergence"))
    )
    return {
        "water_diagnostics_in_physical_ranges": valid,
        "water_close_particle_fraction_at_most_0_02": valid and values["close_water_particle_fraction"] <= 0.02,
        "water_density_p99_at_most_1_15": valid and values["water_density_p99_ratio"] <= 1.15,
        "density_iteration_limit_fraction_at_most_0_60": valid and values["density_iteration_limit_fraction"] <= 0.60,
    }


# These checks measure precision, not artifact integrity or visible contact failure.
# Keep their values intact so video acceptance never becomes a claim of accuracy.
VISUAL_ADVISORY_CHECKS = frozenset({
    "nbody_relative_energy_drift_at_most_0_02",
    "connection_endpoint_energy_drift_at_most_0_02",
    "connection_peak_energy_drift_at_most_0_02",
    "connection_rod_length_residual", "connection_rope_length_residual",
    "connection_each_constraint_residual", "mesh_volume_ratio_within_20_percent",
    "water_density_p99_at_most_1_15", "density_iteration_limit_fraction_at_most_0_60",
})


def _summary(
    result: dict[str, Any],
    artifact_root: Path | None = None,
    verified_video_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scene, scene_report, legacy_liquid_scene, legacy_result = _validated_result_scene(result)
    trajectory = result.get("trajectory")
    if not isinstance(trajectory, dict) or not isinstance(trajectory.get("diagnostics"), dict):
        raise TypeError("trajectory.diagnostics must be an object")
    diagnostics = trajectory["diagnostics"]
    result_ok = _required_bool(result.get("ok"), "ok")
    video_required = _required_bool(result.get("video_required"), "video_required")
    expected_claims = physics_claims(scene)
    if legacy_result:
        expected_claims["fluid"] = _LEGACY_FLUID_CLAIM
    if result.get("physics_claims") != expected_claims:
        raise ValueError("physics_claims do not match the trusted engine fidelity contract")
    finite = _required_bool(diagnostics.get("finite"), "trajectory.diagnostics.finite")
    completed = _required_bool(diagnostics.get("completed"), "trajectory.diagnostics.completed")
    runtime_s = _required_number(
        diagnostics.get("runtime_s"), "trajectory.diagnostics.runtime_s", nonnegative=True
    )
    simulated_time_s = _required_number(
        diagnostics.get("simulated_time_s"), "trajectory.diagnostics.simulated_time_s", nonnegative=True
    )
    requested_duration_s = _required_number(
        scene["world"].get("duration"), "scene.world.duration", nonnegative=True
    )
    particle_count_value = _required_nonnegative_integer(
        diagnostics.get("particle_count"), "trajectory.diagnostics.particle_count"
    )
    if result.get("warnings") != scene_report["warnings"]:
        raise ValueError("warnings do not match the bound normalized scene")
    if result.get("assumptions") != scene_report["assumptions"]:
        raise ValueError("assumptions do not match the bound normalized scene")
    video_present, video_verification = _verify_video_artifact(
        result, artifact_root, verified_probe=verified_video_probe
    )
    if not video_required and "video" not in result["artifacts"]:
        video_verification = "video was not requested"
    duration_tolerance = max(1.0e-9, requested_duration_s * 1.0e-9)
    completed_duration = completed and abs(simulated_time_s - requested_duration_s) <= duration_tolerance
    numerical_checks: dict[str, bool] = {
        "finite_state": finite,
        "completed_physical_duration": completed_duration,
    }
    has_water = any(
        isinstance(entity, dict) and entity.get("type") == "fluid"
        for entity in scene["entities"]
    )
    has_sand = any(entity.get("type") == "granular" for entity in scene["entities"])
    has_particles = has_water or has_sand
    has_point_masses = any(entity.get("type") == "point_mass" for entity in scene["entities"])
    has_meshes = any(entity.get("type") == "mesh" for entity in scene["entities"])
    has_connections = bool(scene.get("connections"))
    has_coupling = coupled_enabled(scene)
    if has_particles:
        expected_native_backend = "native-c11-dfsph+pbd" if has_sand else "native-c11-dfsph"
        if has_point_masses:
            expected_native_backend += "+verlet"
    else:
        expected_native_backend = "native-c11-verlet"
    if scene.get("force_fields"):
        expected_native_backend += "+fields"
    if has_meshes:
        expected_native_backend = "native-c11-xpbd-mesh"
    if has_connections:
        expected_native_backend = "native-c11-connections"
    if has_coupling:
        expected_native_backend = "native-c11-coupled"
    backend = diagnostics.get("backend")
    if not isinstance(backend, str) or backend not in _ALLOWED_RESULT_BACKENDS:
        raise ValueError("trajectory.diagnostics.backend is not a supported result backend")
    plan = result.get("plan")
    if not isinstance(plan, dict):
        raise TypeError("plan must be an object")
    expected_plan = make_plan(
        scene,
        include_video=video_required,
        legacy_video_estimate=legacy_result,
    )
    if _json_bytes(plan) != _json_bytes(expected_plan):
        raise ValueError("saved plan does not match a fresh plan for the bound normalized scene")
    if particle_count_value != plan["planned_particles"]:
        raise ValueError("trajectory.diagnostics.particle_count does not match plan.planned_particles")
    render_particle_count = _required_nonnegative_integer(
        diagnostics.get("render_particle_count"),
        "trajectory.diagnostics.render_particle_count",
    )
    expected_render_count = min(
        int(plan["planned_particles"]), int(plan["render_particle_limit"])
    )
    if render_particle_count != expected_render_count:
        raise ValueError(
            "trajectory.diagnostics.render_particle_count does not match the execution plan"
        )
    steps = _required_nonnegative_integer(
        diagnostics.get("steps"), "trajectory.diagnostics.steps"
    )
    expected_completed_steps = int(plan["steps"])
    if backend == "analytic-1d-slider":
        expected_completed_steps = math.ceil((scene["world"]["duration"] - 1e-12) / scene["world"]["dt"])
    if backend == "python-reference":
        reference_time = 0.0
        expected_completed_steps = 0
        while reference_time < scene["world"]["duration"] - 1e-12:
            reference_time += min(scene["world"]["dt"], scene["world"]["duration"] - reference_time)
            expected_completed_steps += 1
    if backend.startswith("native-"):
        # The C solver absorbs a terminal remainder below 1e-5 of the
        # requested dt instead of reporting a meaningless nanosecond macro
        # step.  Reproduce that bounded loop when checking its diagnostics.
        duration = float(scene["world"]["duration"])
        requested_dt = float(scene["world"]["dt"])
        snap_tolerance = max(1.0e-12, requested_dt * 1.0e-5)
        native_time = 0.0
        expected_completed_steps = 0
        while native_time < duration - 1.0e-12:
            remaining_time = duration - native_time
            if remaining_time <= snap_tolerance:
                break
            native_time += min(requested_dt, remaining_time)
            expected_completed_steps += 1
    if steps > plan["steps"] or (completed and steps != expected_completed_steps):
        raise ValueError("trajectory.diagnostics.steps does not match the execution plan")
    substeps = _required_nonnegative_integer(
        diagnostics.get("substeps"), "trajectory.diagnostics.substeps"
    )
    if completed and substeps < steps:
        raise ValueError("trajectory.diagnostics.substeps may not be smaller than steps")
    force_field_count = _required_nonnegative_integer(
        diagnostics.get("force_field_count"),
        "trajectory.diagnostics.force_field_count",
    )
    if force_field_count != len(scene.get("force_fields", [])):
        raise ValueError(
            "trajectory.diagnostics.force_field_count does not match the normalized scene"
        )
    frames = trajectory.get("frames")
    if not isinstance(frames, list):
        raise TypeError("trajectory.frames must be an array")
    if len(frames) > plan["output_frames"] or (
        completed and len(frames) != plan["output_frames"]
    ):
        raise ValueError("trajectory frame count does not match the execution plan")
    if not has_meshes and not has_coupling:
        channel_counts = {
            "p": render_particle_count,
            "g": sum(entity["type"] == "point_mass" for entity in scene["entities"]),
            "r": sum(entity["type"] in {"rigid", "slider"} for entity in scene["entities"]),
        }
        for index, frame in enumerate(frames):
            if not isinstance(frame, dict):
                raise ValueError("trajectory frames must be objects")
            expected_time = min(index / scene["world"]["output_fps"], requested_duration_s)
            if not finite_number(frame.get("t")) or abs(frame["t"] - expected_time) > 1e-7:
                raise ValueError("trajectory frame timestamp does not match the prepared output schedule")
            for channel, count in channel_counts.items():
                points = frame.get(channel)
                if not isinstance(points, list) or len(points) != count or any(not finite_vec(point) for point in points):
                    raise ValueError(f"trajectory frame {channel} positions do not match the finite planned population")
            if frame.get("m", []) != []:
                raise ValueError("trajectory frame contains unplanned mesh vertices")
    planned_backend = plan.get("backend")
    if planned_backend not in {"native", "python", "slider", "mesh", "connections", "coupled"}:
        raise ValueError("plan.backend is unsupported")
    if (backend == "native-c11-coupled") != (planned_backend == "coupled"):
        raise ValueError("Coupled backend does not match the prepared plan")
    if (backend == "native-c11-connections") != (planned_backend == "connections"):
        raise ValueError("Connection backend does not match the prepared plan")
    if (backend == "native-c11-xpbd-mesh") != (planned_backend == "mesh"):
        raise ValueError("Mesh backend does not match the prepared plan")
    if (backend == "analytic-1d-slider") != (planned_backend == "slider"):
        raise ValueError("Slider backend does not match the prepared plan")
    if backend.startswith("native-"):
        native_status = diagnostics.get("native_status")
        max_substeps_used = _required_nonnegative_integer(
            diagnostics.get("max_substeps_used"),
            "trajectory.diagnostics.max_substeps_used",
        )
        threads_used = _required_nonnegative_integer(
            diagnostics.get("threads_used"), "trajectory.diagnostics.threads_used"
        )
        if (
            planned_backend != ("coupled" if has_coupling else "connections" if has_connections else "mesh" if has_meshes else "native")
            or backend != expected_native_backend
            or native_status not in {"ok", "timed_out", "nonfinite"}
            or max_substeps_used > substeps
            or threads_used < 1
            or threads_used > plan["threads"]
        ):
            raise ValueError("native backend diagnostics do not match the execution plan")
        if completed and native_status != "ok":
            raise ValueError("a completed native result must have native_status=ok")
    else:
        native_only_keys = {
            "native_status",
            "native_build",
            "state_precision",
            "frame_precision",
            "max_substeps_used",
            "threads_used",
        }
        if any(key in diagnostics for key in native_only_keys):
            raise ValueError("python backend diagnostics contain native-only fields")
        if planned_backend == "native" and not isinstance(diagnostics.get("native_fallback"), str):
            raise ValueError("python fallback must record the native failure")
    attempts = result.get("attempts")
    if not isinstance(attempts, list) or not attempts or not isinstance(attempts[-1], dict):
        raise TypeError("attempts must be a non-empty array of attempt objects")
    if attempts[-1].get("backend") != backend:
        raise ValueError("the final attempt backend does not match trajectory diagnostics")
    water_checks_applied = has_water and (backend.startswith("native-c11-dfsph") or has_coupling)
    if water_checks_applied:
        numerical_checks.update(_water_checks(diagnostics))
    if has_coupling:
        from physics_demo.io.coupled import result_checks as coupled_checks
        numerical_checks.update(coupled_checks(scene, plan, trajectory))
    elif has_meshes:
        from physics_demo.io.mesh_scene import result_checks
        numerical_checks.update(result_checks(scene, plan, trajectory))
    if has_connections and not has_coupling:
        from physics_demo.io.connections import result_checks as connection_checks
        numerical_checks.update(connection_checks(scene, plan, trajectory))
    point_mass_ids = {
        entity["id"] for entity in scene["entities"] if entity.get("type") == "point_mass"
    }
    expected_invariants_applicable = not has_coupling and not has_connections and bool(point_mass_ids) and not any(
        entity.get("type") == "point_mass" and entity.get("fixed")
        for entity in scene["entities"]
    ) and not any(
        any(target in point_mass_ids for target in field.get("targets", []))
        for field in scene.get("force_fields", [])
    )
    invariants_applicable = _required_bool(
        diagnostics.get("nbody_invariants_applicable"),
        "trajectory.diagnostics.nbody_invariants_applicable",
    )
    invariants_conserved = _required_bool(
        diagnostics.get("nbody_invariants_conserved"),
        "trajectory.diagnostics.nbody_invariants_conserved",
    )
    if invariants_applicable != expected_invariants_applicable:
        raise ValueError("N-body invariant applicability does not match the normalized scene")
    if expected_invariants_applicable:
        energy_drift = _required_number(
            diagnostics.get("nbody_relative_energy_drift"),
            "trajectory.diagnostics.nbody_relative_energy_drift",
        )
        momentum_drift = _required_number(
            diagnostics.get("nbody_momentum_drift"),
            "trajectory.diagnostics.nbody_momentum_drift",
            nonnegative=True,
        )
        momentum_tolerance = _required_number(
            diagnostics.get("nbody_momentum_tolerance"),
            "trajectory.diagnostics.nbody_momentum_tolerance",
            nonnegative=True,
        )
        computed_conserved = (
            abs(energy_drift) <= NBODY_MAX_RELATIVE_ENERGY_DRIFT
            and momentum_drift <= momentum_tolerance
        )
        numerical_checks.update({
            "nbody_relative_energy_drift_at_most_0_02":
                abs(energy_drift) <= NBODY_MAX_RELATIVE_ENERGY_DRIFT,
            "nbody_momentum_drift_within_tolerance": momentum_drift <= momentum_tolerance,
            "nbody_conservation_claim_consistent": invariants_conserved == computed_conserved,
        })
    elif invariants_conserved:
        raise ValueError("N-body invariants cannot be marked conserved when they are not applicable")
    measurements = None
    if scene.get("queries"):
        measurements = verified_measurements(result, artifact_root)
        numerical_checks["quantitative_observations_valid"] = True
        for answer in measurements["answers"]:
            if answer["type"] == "nbody_stability":
                numerical_checks["query_nbody_peak_invariants:" + answer["id"]] = answer["numerical_integrity_passed"]
    numerical_passed = all(numerical_checks.values())
    solver_ok = finite and completed_duration
    validation_mode = scene["budget"].get("validation", "strict")
    precision_warnings = [name for name, passed in numerical_checks.items()
                          if not passed and validation_mode == "visual" and
                          (name in VISUAL_ADVISORY_CHECKS or name.startswith("query_nbody_peak_invariants:"))]
    failed_checks = [name for name, passed in numerical_checks.items()
                     if not passed and name not in precision_warnings]
    delivery_ok = result_ok and not failed_checks and (video_present or not video_required)
    if video_required and not video_present:
        failed_checks.append("verified_video_artifact")
    summary: dict[str, Any] = {
        "ok": delivery_ok,
        "solver_ok": solver_ok,
        "result_version": result.get("result_version", 0),
        "run_id": result["run_id"],
        "scene_hash": result.get("scene_hash"),
        "measurements_sha256": result.get("measurements_sha256") if measurements is not None else None,
        "scene_name": scene["name"],
        "quality": plan["quality"],
        "plan": plan,
        "agent_report": plan_report(plan),
        "timing_estimate": plan.get("timing_estimate"),
        "fidelity": expected_claims,
        "warnings": copy.deepcopy(scene_report["warnings"]),
        "assumptions": copy.deepcopy(scene_report["assumptions"]),
        "runtime_s": runtime_s,
        "total_runtime_s": _required_number(
            result.get("total_runtime_s", runtime_s), "total_runtime_s", nonnegative=True
        ),
        "simulated_time_s": simulated_time_s,
        "particle_count": particle_count_value,
        "completed": completed,
        "diagnostics": diagnostics,
        "attempts": attempts,
        "artifacts": result.get("artifacts", {}),
        "quality_gate": {
            "passed": delivery_ok,
            "validation_mode": validation_mode,
            "numerical_passed": numerical_passed,
            "precision_warnings": precision_warnings,
            "requires_video": video_required,
            "video_present": video_present,
            "video_verification": video_verification,
            "water_stability_checks_applied": water_checks_applied,
            "water_stability_check_coverage": "native-full" if water_checks_applied else ("python-basic" if has_water else "not-applicable"),
            "numerical_checks": numerical_checks,
            "failed_checks": failed_checks,
        },
    }
    if measurements is not None:
        summary["measurements"] = compact_measurements(measurements)
        summary["measurements"]["usable"] = numerical_passed
        if not numerical_passed:
            summary["measurements"]["answers"] = [
                {"id": answer["id"], "type": answer["type"], "status": "inconclusive",
                 "reason": "The simulation or numerical quality gate failed; do not claim a quantitative result."}
                for answer in measurements["answers"]
            ]
    if not delivery_ok:
        errors = result.get("errors")
        if not isinstance(errors, list) or not errors:
            if not solver_ok:
                errors = [{
                    "code": "simulation_incomplete",
                    "path": "trajectory.diagnostics",
                    "message": "The solver did not complete the requested physical duration with a finite state.",
                    "retryable": True,
                    "suggestion": "Revise the scene or requested fidelity, prepare a new plan, then run once from the initial state.",
                }]
            else:
                errors = [{
                    "code": "quality_gate_failed",
                    "path": "quality_gate",
                    "message": "The saved result failed its final quality gate: " + ", ".join(failed_checks),
                    "retryable": True,
                    "suggestion": "Correct the failed checks and rerun from the initial state; do not deliver an unverified artifact.",
                }]
        summary["stage"] = result.get("stage") or ("simulate" if not solver_ok else "quality")
        summary["errors"] = errors
    return _apply_runtime_budget(summary)
