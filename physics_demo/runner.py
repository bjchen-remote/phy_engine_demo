"""Public operations and orchestration from scene input to verified artifacts."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
import time
from typing import Any

from .agent_contract import failure
from .backend import FallbackBudgetExceeded, run_scene
from .catalog import catalog_summary
from .jsonio import canonical_bytes as _json_bytes, loads as strict_json_loads, write as _write_json
from .math3d import finite_number
from .limits import (
    MAX_WALL_TIME_S,
    MAX_PATCH_OPERATIONS,
    MAX_RESULT_FILE_BYTES,
    MAX_SCENE_FILE_BYTES,
    NBODY_MAX_INITIAL_STEP_RATIO,
)
from .native_backend import (
    NativeBackendUnavailable,
    NativeResourceLimitError,
    NativeSimulationError,
    native_build_info,
)
from .planning import make_plan, plan_report
from .queries import MAX_MEASUREMENT_FILE_BYTES, digest as measurement_digest, evaluate as evaluate_queries
from .results import RESULT_VERSION, physics_claims, _apply_runtime_budget, _completion_runtime, _summary
from .sliders import SliderResolutionError
from .schema import CAPABILITIES, normalize_and_validate
from .video import VideoEncodingError, encode_mp4


RUN_ARTIFACT_NAMES = {
    "scene.normalized.json",
    "plan.json",
    "result.json",
    "summary.json",
    "completion.json",
    "simulation.mp4",
    "measurements.json",
}


def _output_failure(error: BaseException) -> dict[str, Any]:
    return failure(
        'output', 'output_write_failed', 'output_dir',
        str(error),
        retryable=True,
        suggestion='Choose a writable dedicated run directory, then rerun the prepared scene.',
    )


def load_scene(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if source.stat().st_size > MAX_SCENE_FILE_BYTES:
        raise ValueError(f"scene file exceeds the {MAX_SCENE_FILE_BYTES}-byte input limit")
    value = strict_json_loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("scene file must contain a JSON object")
    return value


def validate(scene: Any, *, unlimited: bool = False) -> dict[str, Any]:
    report = normalize_and_validate(scene, allow_unlimited=unlimited)
    result = {"ok": report["valid"], **report}
    if not report["valid"]:
        result["stage"] = "validate"
    return result


def _apply_budget_override(scene: dict[str, Any], budget_seconds: float | None) -> dict[str, Any] | None:
    if budget_seconds is None:
        return None
    if not finite_number(budget_seconds):
        return {"code": "budget_number", "path": "budget_seconds", "message": f"budget_seconds must be one finite number in [1, {MAX_WALL_TIME_S}].", "retryable": True, "suggestion": f"Use a numeric value from 1 through {MAX_WALL_TIME_S}."}
    if not 1.0 <= float(budget_seconds) <= MAX_WALL_TIME_S:
        return {"code": "budget_range", "path": "budget_seconds", "message": f"budget_seconds must be in [1, {MAX_WALL_TIME_S}].", "retryable": True, "suggestion": f"Use a value from 1 through {MAX_WALL_TIME_S}."}
    scene["budget"]["wall_time_s"] = float(budget_seconds)
    return None


def _validated_scene(scene: Any, budget_seconds: float | None, unlimited: bool = False) -> dict[str, Any]:
    if unlimited and isinstance(scene, dict):
        scene = copy.deepcopy(scene)
        from .limits import NO_DEADLINE_WALL_TIME_S
        scene.setdefault("budget", {})["wall_time_s"] = NO_DEADLINE_WALL_TIME_S
        scene["budget"]["unlimited_runtime"] = True
    report = validate(scene, unlimited=unlimited)
    if not report["ok"] or budget_seconds is None:
        return report
    error = _apply_budget_override(report["scene"], budget_seconds)
    if error:
        return {"ok": False, "stage": "arguments", "errors": [error]}
    # Recompute assumptions only when the override changed the scene.
    return validate(report["scene"], unlimited=unlimited)


def _plan_constraint_error(plan: dict[str, Any]) -> dict[str, Any] | None:
    if not plan.get("coupling_fits_limits", True):
        return {"code":"coupling_budget_exceeded","path":"coupling","message":"Shared-clock substeps, mesh size or contact work exceed coupled limits.","retryable":True,"suggestion":"Reduce dt for stiffness resolution; shorten the horizon or simplify explicit geometry for work limits. Do not silently change masses, spring constants or mesh topology."}
    if not plan.get("connection_fits_limits", True):
        return {"code": "connection_budget_exceeded", "path": "connection_settings",
                "message": "Spring stiffness or damping requires more than 64 substeps, or the planned steps/work exceed the connection budget.",
                "retryable": True, "suggestion": "Reduce world.dt when stiffness or damping needs more substeps; shorten the horizon or simplify connections for work limits. Do not silently change material parameters or masses."}
    if not plan.get("mesh_fits_limits", True):
        return {"code": "mesh_budget_exceeded", "path": "entities",
                "message": "Mesh topology, output samples or constraint work exceed the bounded execution budget.",
                "retryable": True, "suggestion": "Explicitly request fewer vertices/triangles, a shorter duration or fewer video frames; mesh topology is never silently simplified."}
    if not plan.get("measurement_plan", {}).get("fits_limits", True):
        return {"code": "measurement_budget_exceeded", "path": "queries",
                "message": "Declared observations exceed the scalar or full-state scan budget.",
                "retryable": True,
                "suggestion": "Request fewer metrics, a shorter horizon, or an explicitly larger world.dt consistent with accuracy; video FPS does not control measurement sampling."}
    if not plan.get("nbody_timestep_feasible", True):
        ratio = float(plan.get("nbody_initial_step_ratio", math.inf))
        return {
            "code": "unstable_nbody_timestep",
            "path": "world.dt",
            "message": (
                f"The initial point-mass step ratio is {ratio:.6g}; the quantitative "
                f"Verlet limit is {NBODY_MAX_INITIAL_STEP_RATIO:.3g}."
            ),
            "retryable": True,
            "suggestion": "Reduce world.dt or increase the initial point-mass separation/softening, then prepare again.",
        }
    if not plan.get("bounds_feasible", True):
        spans = ", ".join(f"{value:.6g}" for value in plan.get("bounds_spans_m", []))
        minimum = float(plan.get("minimum_particle_bounds_span_m", 0.0))
        return {
            "code": "infeasible_bounds",
            "path": "world.bounds",
            "message": f"World spans [{spans}] m cannot contain a particle diameter of {minimum:.6g} m at the planned spacing.",
            "retryable": True,
            "suggestion": "Enlarge world.bounds or request a finer spacing/quality combination; opposite world faces must not overlap for one particle.",
        }
    if not plan.get("initial_bounds_feasible", True):
        violations = ", ".join(plan.get("initial_bounds_violations", []))
        return {
            "code": "initial_state_outside_bounds",
            "path": "world.bounds",
            "message": "Initial movable geometry lies outside world.bounds: " + violations,
            "retryable": True,
            "suggestion": "Expand world.bounds or move/resize the named entity; include its particle or rigid radius margin.",
        }
    return None


def estimate(scene: Any, budget_seconds: float | None = None, *, unlimited: bool = False) -> dict[str, Any]:
    report = _validated_scene(scene, budget_seconds, unlimited)
    if not report["ok"]:
        return report
    plan = make_plan(report["scene"])
    result = {"ok": True, "plan": plan, "agent_report": plan_report(plan)}
    error = _plan_constraint_error(plan)
    if error:
        return {**result, "ok": False, "stage": "estimate", "errors": [error]}
    return {**result, "warnings": report["warnings"], "assumptions": report["assumptions"]}


def prepare(scene: Any, budget_seconds: float | None = None, *, unlimited: bool = False) -> dict[str, Any]:
    """Validate and plan once, returning the exact runnable scene."""
    report = _validated_scene(scene, budget_seconds, unlimited)
    if not report["ok"]:
        return {**report, "ready_to_simulate": False}
    normalized = report["scene"]
    plan = make_plan(normalized)
    result = {
        "ok": True, "ready_to_simulate": True,
        "scene": normalized,
        "scene_json": json.dumps(normalized, separators=(",", ":"), ensure_ascii=False),
        "plan": plan, "agent_report": plan_report(plan),
        "warnings": report["warnings"], "assumptions": report["assumptions"],
    }
    error = _plan_constraint_error(plan)
    if error is None and not plan["timing_estimate"]["fits_budget"]:
        timing = plan["timing_estimate"]
        error = {
            "code": "budget_infeasible", "path": "budget.wall_time_s",
            "message": f"The estimated p90 runtime is {timing['total_p90_s']:.3f} s, above the {timing['hard_limit_s']:.3f} s hard limit after bounded coarsening.",
            "retryable": True,
            "suggestion": f"Propose a shorter duration, lower output_fps, simpler geometry, or a budget up to {MAX_WALL_TIME_S} seconds; do not silently change an explicit user requirement.",
        }
    if error:
        result.update(ok=False, ready_to_simulate=False, stage="estimate", errors=[error])
    return result


def simulate(
    scene: Any,
    output_dir: str | Path,
    budget_seconds: float | None = None,
    make_video: bool = True,
    *, unlimited: bool = False,
) -> dict[str, Any]:
    started = time.monotonic()
    report = _validated_scene(scene, budget_seconds, unlimited)
    if not report["ok"]:
        return report
    normalized = report["scene"]
    wall_budget = float(normalized["budget"]["wall_time_s"])
    reserve = min(12.0, max(2.0, 0.2 * wall_budget)) if make_video else 0.5
    compute_deadline = started + max(0.5, wall_budget - reserve)
    plan = make_plan(normalized, include_video=make_video)
    plan_error = _plan_constraint_error(plan)
    if plan_error:
        return {"ok": False, "stage": "estimate", "plan": plan, "errors": [plan_error]}
    if not plan["timing_estimate"]["fits_budget"]:
        estimate_value = plan["timing_estimate"]["total_p90_s"]
        return failure(
            'estimate', 'budget_infeasible', 'budget.wall_time_s',
            f'The estimated p90 runtime is {estimate_value:.3f} s, above the {wall_budget:.3f} s hard limit after safe coarsening.',
            retryable=True,
            suggestion=f'Reduce physical duration or output_fps, simplify the scene, or increase the wall-time budget up to {MAX_WALL_TIME_S} seconds.',
            plan=plan,
        )
    run_hash = hashlib.sha256(_json_bytes(normalized)).hexdigest()
    run_id = f"{int(time.time())}-{run_hash[:10]}"
    try:
        raw_output = Path(output_dir).expanduser()
        if len(str(raw_output)) > 4096:
            raise ValueError("output path exceeds 4096 characters")
        if raw_output.is_symlink():
            raise ValueError("output directory may not be a symbolic link")
        output = raw_output.resolve()
        if output == Path(output.anchor) or output == Path.home().resolve():
            raise ValueError("output directory must be a dedicated run directory, not a filesystem or home root")
        if output.exists() and not output.is_dir():
            raise ValueError("output path exists and is not a directory")
        if output.exists():
            entries = list(output.iterdir())
            unsafe = sorted(
                item.name for item in entries
                if item.is_symlink() or not item.is_file()
            )
            if unsafe:
                raise ValueError(f"output directory contains symbolic links or non-files: {unsafe[:5]}")
            unexpected = sorted(item.name for item in entries if item.name not in RUN_ARTIFACT_NAMES)
            if unexpected:
                raise ValueError(f"output directory contains unrelated entries: {unexpected[:5]}")
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(mode="w+b", dir=output) as probe:
            probe.write(b"ok")
            probe.flush()
            os.fsync(probe.fileno())
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        output_error = _output_failure(error)
        output_error["errors"][0]["code"] = "unsafe_output_directory"
        return output_error
    attempts: list[dict[str, Any]] = []

    try:
        trajectory = run_scene(normalized, plan, compute_deadline)
    except NativeBackendUnavailable as error:
        return failure(
            'environment', 'native_backend_unavailable', 'budget.backend',
            str(error),
            suggestion=('Restore a working C11 compiler; Mesh, connection and coupled scenes have no Python fallback.' if plan['backend'] in {'mesh', 'connections', 'coupled'} else 'Restore a working C11 compiler/native runtime, or explicitly choose auto or python and prepare a new plan.'),
            plan=plan,
        )
    except FallbackBudgetExceeded as error:
        return failure(
            'estimate', 'fallback_budget_exhausted', 'budget.wall_time_s',
            str(error),
            retryable=True,
            suggestion='Prepare a simpler scene, shorter duration, or lower output rate so the Python fallback p90 fits the full budget.',
            plan=plan,
        )
    except (NativeResourceLimitError, SliderResolutionError) as error:
        return failure(
            'simulate', 'solver_rejected_scene', 'entities',
            str(error),
            retryable=True,
            suggestion='Reduce overlap, particle density, or scene complexity and run physics_estimate again.',
            plan=plan,
        )
    except MemoryError as error:
        return failure(
            'simulate', 'solver_memory_exhausted', 'entities',
            str(error) or 'The solver exhausted available memory.',
            retryable=True,
            suggestion='Reduce particle count or scene complexity and run physics_prepare again.',
            plan=plan,
        )
    except NativeSimulationError as error:
        return failure(
            'environment', 'native_execution_failed', 'budget.backend',
            str(error),
            suggestion=('Inspect the native runtime failure; no approximation fallback is available.' if plan['backend'] in {'mesh', 'connections', 'coupled'} else 'Inspect the native runtime failure; use auto only if an intentional Python fallback remains within budget.'),
            plan=plan,
        )
    attempts.append({
        "quality": plan["quality"],
        "backend": trajectory["diagnostics"].get("backend", plan.get("backend")),
        "completed": trajectory["diagnostics"]["completed"],
        "finite": trajectory["diagnostics"]["finite"],
        "runtime_s": trajectory["diagnostics"]["runtime_s"],
    })

    diagnostics = trajectory["diagnostics"]
    ok = bool(diagnostics["completed"] and diagnostics["finite"])
    result: dict[str, Any] = {
        "ok": ok,
        "result_version": RESULT_VERSION,
        "run_id": run_id,
        "scene_hash": run_hash,
        "scene": normalized,
        "plan": plan,
        "warnings": report["warnings"],
        "assumptions": report["assumptions"],
        "physics_claims": physics_claims(normalized),
        "video_required": make_video,
        "attempts": attempts,
        "trajectory": trajectory,
        "artifacts": {},
    }
    if not ok:
        result["stage"] = "simulate"
        result["errors"] = [{
            "code": "simulation_incomplete",
            "path": "trajectory.diagnostics",
            "message": "The solver did not complete the requested physical duration with a finite state.",
            "retryable": True,
            "suggestion": "Reduce duration, particle count, or initial overlap and prepare the revised scene before retrying.",
        }]
    scene_path = output / "scene.normalized.json"
    plan_path = output / "plan.json"
    result_path = output / "result.json"
    summary_path = output / "summary.json"
    completion_path = output / "completion.json"
    verified_video_probe: dict[str, Any] | None = None
    try:
        _write_json(scene_path, normalized)
        _write_json(plan_path, plan)
        result["artifacts"].update({
            "scene": str(scene_path),
            "plan": str(plan_path),
            "result": str(result_path),
            "summary": str(summary_path),
            "completion": str(completion_path),
        })
        if normalized.get("queries"):
            measurements = evaluate_queries(normalized, plan, trajectory, run_id, run_hash)
            measurement_path = output / "measurements.json"
            _write_json(measurement_path, measurements)
            result["artifacts"]["measurements"] = str(measurement_path)
            result["measurements_sha256"] = measurement_digest(measurements)
        _write_json(result_path, result)

        if make_video and ok:
            video_path = output / "simulation.mp4"
            try:
                remaining = (None if unlimited else wall_budget - (time.monotonic() - started))
                if remaining is not None and remaining <= 0.5:
                    raise VideoEncodingError("The run used its wall-clock budget before the MP4 stage.")
                video = encode_mp4(
                    result_path,
                    video_path,
                    max(1, round(float(normalized["world"]["output_fps"]))),
                    timeout_seconds=remaining,
                )
                physical_duration = max(
                    0.0,
                    float(trajectory["frames"][-1]["t"])
                    - float(trajectory["frames"][0]["t"]),
                )
                video["physical_duration_s"] = physical_duration
                video["time_scale_to_physical"] = (
                    physical_duration / video["duration_s"]
                    if video["duration_s"] > 0
                    else 0.0
                )
                video["width"] = 960
                video["height"] = 540
                result["artifacts"]["video"] = video
                verified_video_probe = copy.deepcopy(video["decode_validation"])
            except VideoEncodingError as error:
                result["ok"] = False
                result["stage"] = "video"
                result["errors"] = [{
                    "code": "video_encoding_failed",
                    "path": "artifacts.video",
                    "message": str(error),
                    "retryable": True,
                    "suggestion": "Install or restore the platform video encoder, then rerun the same normalized scene.",
                }]

        result["total_runtime_s"] = time.monotonic() - started
        summary = _summary(
            result, output, verified_video_probe=verified_video_probe
        )
        if not summary["ok"] and result["ok"]:
            result.update(ok=False, stage=summary["stage"], errors=summary["errors"])
            result["total_runtime_s"] = time.monotonic() - started
            summary = _summary(
                result, output, verified_video_probe=verified_video_probe
            )
        final_gate_runtime_s = time.monotonic() - started
        result["total_runtime_s"] = final_gate_runtime_s
        result["runtime_measurement_boundary"] = "through_final_quality_gate_before_result_persistence"
        _apply_runtime_budget(summary, final_gate_runtime_s, "through_final_quality_gate_before_result_persistence")
        if not summary["ok"] and result["ok"]:
            result.update(ok=False, stage=summary["stage"], errors=summary["errors"])
        _write_json(result_path, result)
        result_persisted_runtime_s = time.monotonic() - started
        _apply_runtime_budget(summary, result_persisted_runtime_s, "through_result_persistence")
        summary["artifact_persistence_s"] = max(
            0.0, result_persisted_runtime_s - final_gate_runtime_s
        )
        persisted_budget_passed = summary["quality_gate"]["runtime_checks"]["wall_time_within_budget"]
        _write_json(summary_path, summary)
        summary_persisted_runtime_s = time.monotonic() - started
        completion = {
            "run_id": run_id,
            "scene_hash": run_hash,
            "wall_runtime_s": summary_persisted_runtime_s,
            "quality_gate_runtime_s": final_gate_runtime_s,
            "artifact_persistence_s": max(
                0.0, summary_persisted_runtime_s - final_gate_runtime_s
            ),
            "measurement_boundary": "through_summary_persistence",
        }
        _write_json(completion_path, completion)
        _apply_runtime_budget(summary, time.monotonic() - started, "through_completion_persistence")
        if (not summary["quality_gate"]["runtime_checks"]["wall_time_within_budget"]
                and persisted_budget_passed):
            # Late persistence crossed the limit: save a failed summary once,
            # then record its completed write using the existing boundary.
            summary["artifact_persistence_s"] = max(0.0, summary["total_runtime_s"] - final_gate_runtime_s)
            _write_json(summary_path, summary)
            summary_persisted_runtime_s = time.monotonic() - started
            completion.update(wall_runtime_s=summary_persisted_runtime_s,
                              artifact_persistence_s=max(0.0, summary_persisted_runtime_s - final_gate_runtime_s))
            _write_json(completion_path, completion)
            _apply_runtime_budget(summary, time.monotonic() - started, "through_completion_persistence")
        summary["artifact_persistence_s"] = max(
            0.0, summary["total_runtime_s"] - final_gate_runtime_s
        )
        return summary
    except OSError as error:
        return _output_failure(error)
    except (KeyError, TypeError, ValueError) as error:
        return failure(
            'quality', 'invalid_' + plan['backend'] + '_result' if plan['backend'] in {'mesh', 'connections', 'coupled'} else 'invalid_measurement_contract',
            'trajectory' if plan['backend'] in {'mesh', 'connections', 'coupled'} else 'trajectory.observations',
            str(error),
            suggestion=('Inspect geometry and native diagnostics; do not deliver invalid simulation results.' if plan['backend'] in {'mesh', 'connections', 'coupled'} else 'Inspect the solver observation contract; do not invent, interpolate from video, or silently omit a requested measurement.'),
        )


def inspect(result_path: str | Path) -> dict[str, Any]:
    try:
        if not isinstance(result_path, (str, Path)):
            raise TypeError("result_path must be a path string")
        if len(str(result_path)) > 4096:
            raise ValueError("result_path exceeds 4096 characters")
        path = Path(result_path).expanduser().resolve()
    except (OSError, RuntimeError, TypeError, ValueError) as error:
        return failure(
            'inspect', 'result_path', 'result_path',
            str(error),
        )
    if path.is_dir():
        path = path / "result.json"
    if not path.exists():
        return failure(
            'inspect', 'result_not_found', str(path),
            'No result.json was found at this location.',
        )
    if path.name != "result.json" or not path.is_file():
        return failure(
            'inspect', 'result_path', str(path),
            'Inspection accepts only a result.json file or its containing run directory.',
        )
    if path.stat().st_size > MAX_RESULT_FILE_BYTES:
        return failure(
            'inspect', 'result_too_large', str(path),
            f'result.json exceeds the {MAX_RESULT_FILE_BYTES}-byte inspection limit.',
        )
    try:
        result = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        return failure(
            'inspect', 'invalid_result_json', str(path),
            str(error),
        )
    if not isinstance(result, dict) or not isinstance(result.get("trajectory"), dict):
        return failure(
            'inspect', 'invalid_result_contract', str(path),
            'result.json does not contain a simulation result object.',
        )
    try:
        summary = _summary(result, path.parent)
        persisted_runtime_s = _completion_runtime(result, path.parent)
        result_runtime_s = float(summary["total_runtime_s"])
        _apply_runtime_budget(summary, persisted_runtime_s, "through_summary_persistence")
        summary["artifact_persistence_s"] = max(
            0.0, persisted_runtime_s - result_runtime_s
        )
        return summary
    except (AttributeError, KeyError, OSError, OverflowError, RecursionError, TypeError, ValueError) as error:
        return failure(
            'inspect', 'invalid_result_contract', str(path),
            f'result.json is missing required simulation fields: {error}',
        )


def query(result_path: str | Path, query_id: str | None = None) -> dict[str, Any]:
    """Retrieve only the declarations made before this inspected simulation."""
    if query_id is not None and (not isinstance(query_id, str) or not query_id.strip() or len(query_id) > 128):
        return failure(
            'arguments', 'query_id', 'query_id',
            'query_id must be a declared string ID or null.',
            retryable=True,
        )
    summary = inspect(result_path)
    if not summary.get("ok"):
        return summary
    measurements = summary.get("measurements")
    if not measurements:
        return failure(
            'query', 'queries_not_recorded', 'queries',
            'This run has no predeclared quantitative observations. Add scene.queries, prepare, and rerun.',
        )
    if not measurements["usable"]:
        return failure(
            'query', 'numerical_precision_unverified', 'quality_gate.numerical_checks',
            'The visual video is deliverable, but this run did not pass numerical precision checks.',
            retryable=True, suggestion='Use budget.validation=strict and refine the integration before reporting numbers.',
            quality_gate=summary['quality_gate'],
        )
    answers = measurements["answers"]
    if query_id is not None:
        answers = [answer for answer in answers if answer["id"] == query_id]
        if not answers:
            return failure(
                'query', 'query_not_declared', 'query_id',
                'The ID was not declared for this run. Change scene.queries and prepare a new simulation.',
                available_query_ids=[a['id'] for a in measurements['answers']],
            )
    response = {"ok": True, "run_id": summary["run_id"], "scene_hash": summary["scene_hash"],
                "quality_gate": summary["quality_gate"], "sampling": measurements["sampling"],
                "answers": answers, "artifacts": {key: summary["artifacts"][key] for key in ("measurements", "result")}}
    if query_id is not None and answers[0]["type"] == "series":
        try:
            path = Path(summary["artifacts"]["measurements"])
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MEASUREMENT_FILE_BYTES:
                raise ValueError("Measurement artifact changed to an unsafe or oversized file")
            stored = strict_json_loads(path.read_text(encoding="utf-8"))
            # Re-bind the re-read samples after inspect so a replaced file is
            # not handed to callers without the same integrity checks.
            if measurement_digest(stored["observations"]) != measurements["observations_sha256"]:
                raise ValueError("Measurement observations changed after inspection")
            response["series"] = {"times_s": stored["observations"]["times"], "values": stored["observations"]["columns"][query_id]}
        except (OSError, KeyError, TypeError, ValueError) as error:
            return failure(
                'query', 'invalid_measurement_artifact', 'measurements',
                str(error),
            )
    return response


def patch_scene(scene: Any, operations: Any, *, unlimited: bool = False) -> dict[str, Any]:
    if not isinstance(scene, dict):
        return failure(
            'patch', 'scene_type', '$',
            'Scene must be an object.',
            retryable=True,
        )
    if not isinstance(operations, list):
        return failure(
            'patch', 'patch_type', 'operations',
            'operations must be an array.',
            retryable=True,
        )
    if len(operations) > MAX_PATCH_OPERATIONS:
        return failure(
            'patch', 'patch_limit', 'operations',
            f'At most {MAX_PATCH_OPERATIONS} patch operations are allowed.',
            retryable=True,
        )
    candidate = copy.deepcopy(scene)

    def list_index(token: str, length: int, allow_append: bool = False) -> int:
        if allow_append and token == "-":
            return length
        if not token.isascii() or not token.isdigit():
            raise ValueError(f"list index {token!r} must be a non-negative decimal integer")
        value = int(token)
        if value > length or (value == length and not allow_append):
            raise IndexError(f"list index {value} is outside length {length}")
        return value

    def resolve_index(target: list, token: str, allow_append: bool = False) -> int:
        if token.startswith("@"):
            matches = [i for i,item in enumerate(target) if isinstance(item,dict) and item.get("id") == token[1:]]
            if len(matches) != 1:
                raise KeyError(f"expected one list item with id {token[1:]!r}; found {len(matches)}")
            return matches[0]
        return list_index(token, len(target), allow_append)

    def descend(target: Any, token: str) -> Any:
        return target[resolve_index(target, token)] if isinstance(target,list) else target[token]

    try:
        for index, operation in enumerate(operations):
            if not isinstance(operation, dict) or operation.get("op") not in {"add", "replace", "remove"}:
                raise ValueError(f"operations[{index}] has an unsupported op")
            path = operation.get("path")
            if not isinstance(path, str) or not path.startswith("/") or len(path) > 1024:
                raise ValueError(f"operations[{index}].path must be a JSON Pointer")
            tokens = [token.replace("~1", "/").replace("~0", "~") for token in path[1:].split("/")]
            target: Any = candidate
            for token in tokens[:-1]:
                target = descend(target, token)
            final = tokens[-1]
            if operation["op"] == "remove":
                if isinstance(target, list):
                    target.pop(resolve_index(target, final))
                else:
                    del target[final]
            elif isinstance(target, list):
                item = copy.deepcopy(operation.get("value"))
                if operation["op"] == "add":
                    target.insert(resolve_index(target, final, allow_append=True), item)
                else:
                    target[resolve_index(target, final)] = item
            else:
                if operation["op"] == "replace" and final not in target:
                    raise KeyError(f"replace target {final!r} does not exist")
                target[final] = copy.deepcopy(operation.get("value"))
    except (KeyError, IndexError, TypeError, ValueError) as error:
        return failure(
            'patch', 'patch_failed', f'operations[{index}]',
            str(error),
            retryable=True,
            suggestion='Read the normalized scene and retry with a valid JSON Pointer.',
        )
    report = normalize_and_validate(candidate, allow_unlimited=unlimited)
    if report["valid"]:
        return {"ok": True, "scene": candidate,
                "scene_json": json.dumps(candidate, separators=(",", ":"), ensure_ascii=False),
                "validation": report}
    errors = [
        {
            **error,
            "retryable": True,
            "suggestion": error.get("suggestion", "Patch the named field, then run physics_prepare again."),
        }
        for error in report["errors"]
    ]
    return {
        "ok": False,
        "stage": "patch",
        "scene": candidate,
        "validation": report,
        "errors": errors,
    }


def capabilities() -> dict[str, Any]:
    return {
        "ok": True,
        "capabilities": CAPABILITIES,
        "examples": catalog_summary(),
        "recommended_workflow": [
            "physics_capabilities once per task",
            "obtain a complete scene_json with physics_system or physics_example, or author scene-v1",
            "after scene_json exists and its fluid entity ID is known, use physics_liquid then physics_patch for a named liquid preset",
            "physics_patch for explicit prompt changes",
            "physics_prepare until ready_to_simulate is true",
            "physics_simulate once with the returned scene_json",
            "return the MP4 only when quality_gate.passed is true",
        ],
        "native_runtime": native_build_info(),
        "physics_claims": physics_claims(),
    }
