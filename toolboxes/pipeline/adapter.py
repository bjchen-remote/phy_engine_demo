"""QQ v1 compatibility adapter for separately pinned modeling/simulation/rendering."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from bundle import ROLES, inside, read_json, sha256, verify_bundle


DEFAULT_SETTINGS = {"modeling_quality": "standard", "simulation_quality": "inherit",
                    "rendering_quality": "standard"}


def atomic(path: Path, value: dict, *, sort_keys: bool = True, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".pipeline-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, sort_keys=sort_keys,
                      separators=(',', ':') if compact else None)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def object_hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def compact_engine_models(job: Path) -> None:
    """Keep legacy owned-model files within their existing byte limit.

    The unchanged engine writes pretty JSON; near its mesh budget indentation
    alone can exceed its next-call file admission limit. Preserve every value,
    then atomically serialize without indentation before the next operation.
    """
    for name in ('draft-scene.json', 'prepared-scene.json'):
        path = job / 'work' / name
        if path.exists():
            model = read_json(path, 4_000_000)
            encoded = json.dumps(model, ensure_ascii=False, allow_nan=False, sort_keys=True)
            if len(encoded.encode()) > 1_000_000:
                raise ValueError('model exceeds the existing 1000000-byte data limit')
            atomic(path, model)
    assets = job / 'work/mesh-assets'
    if assets.is_symlink():
        raise ValueError('invalid mesh asset directory')
    for path in assets.glob('*.json'):
        if path.stat().st_size <= 1_000_000:
            continue
        mesh = read_json(path, 4_000_000)
        encoded = json.dumps(mesh, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        if len(encoded.encode()) > 1_000_000 or hashlib.sha256(encoded.encode()).hexdigest() != path.stem:
            raise ValueError('oversized or changed legacy mesh asset')
        # Legacy mesh_ref hashes insertion order; keep it while removing only
        # indentation. Image worker assets already fit and stay byte-identical.
        atomic(path, mesh, sort_keys=False, compact=True)


def load_engine(root: Path, lock: dict) -> Path:
    engine = inside(root, lock["engine"]["path"])
    os.environ["PHYSICS_PIPELINE_PHYSICS_ROOT"] = str(engine)
    os.environ["PHYSICS_DEMO_NATIVE_LIBRARY"] = str(engine / "prebuilt/libphysics_native.dylib")
    os.environ["PHYSICS_DEMO_PREBUILT_VIDEO_DIR"] = str(engine / "prebuilt")
    sys.path.insert(0, str(engine))
    sys.path.insert(0, str(engine / "physics_release"))
    return engine


def image_module(root: Path):
    spec = importlib.util.spec_from_file_location('_pinned_image_modeling', root / 'image_modeling.py')
    module = importlib.util.module_from_spec(spec)
    previous = sys.path[:]
    try:
        sys.path.insert(0, str(root))
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = previous
    return module


def settings(job: Path) -> dict:
    path = job / "work/pipeline/settings.json"
    value = read_json(path) if path.exists() else DEFAULT_SETTINGS.copy()
    if (set(value) != set(DEFAULT_SETTINGS) or value["modeling_quality"] != "standard"
            or value["simulation_quality"] not in ("inherit", "visual", "strict")
            or value["rendering_quality"] not in ("preview", "standard")):
        raise ValueError("unsupported pipeline quality; high/deep are not installed")
    return value


def ensure_task(task: dict) -> None:
    if task.get("schema_version") != 1 or task.get("output_dir") != "artifacts" or task.get("work_dir") != "work":
        raise ValueError("incompatible host task")
    limits = task["limits"]
    if limits.get("network") is not False:
        raise ValueError("pipeline executor must be networkless")
    for key in ("max_output_bytes",):
        if type(limits[key]) is not int or limits[key] <= 0:
            raise ValueError("invalid byte limit")
    wall = limits["wall_time_seconds"]
    if wall is not None and (type(wall) not in (int, float) or not math.isfinite(wall) or wall <= 0):
        raise ValueError("invalid task deadline")


def delivery_ready(task: dict) -> bool:
    value = task.get("delivery", {})
    return (value.get("data_attachments") is True
            and type(value.get("max_data_bytes")) is int and value["max_data_bytes"] > 0)


def freeze(job: Path, lock: dict, estimate: float | None = None) -> dict:
    prepared = read_json(job / "work/prepared-scene.json")
    selected = settings(job)
    body = {"schema_version": "pipeline-plan/1", "prepared_sha256": object_hash(prepared),
            "bundle_sha256": object_hash(lock), "settings": selected,
            "estimated_seconds": max(1.0, float(estimate or prepared.get("estimated_seconds", 25))),
            "mode": "full"}
    body["plan_id"] = object_hash(body)
    atomic(job / "work/pipeline/plan.json", body)
    return body


def check_plan(job: Path, lock: dict) -> tuple[dict, dict]:
    plan = read_json(job / "work/pipeline/plan.json")
    prepared = read_json(job / "work/prepared-scene.json")
    core = {k: v for k, v in plan.items() if k != "plan_id"}
    if (plan.get("schema_version") != "pipeline-plan/1" or plan.get("plan_id") != object_hash(core)
            or plan["bundle_sha256"] != object_hash(lock)
            or plan["prepared_sha256"] != object_hash(prepared) or plan["settings"] != settings(job)):
        raise ValueError("pipeline plan changed; prepare the exact model again")
    return plan, prepared


def capabilities(lock: dict, runtime: dict | None = None) -> dict:
    return {"ok": True, "protocol": "qq-pipeline-compat/1", "engine": lock["engine"],
            "modules": lock["modules"], "modeling_qualities": ["standard"],
            "image_modeling": {"configured": runtime is not None,
                "provider_notice": runtime.get('provider_notice') if runtime else None,
                "operation": "modeling_from_image", "network": False,
                "physical_scale_required": True, "material_assumption_required": True,
                "fidelity": "generative_approximation"},
            "model_preview": {"available": runtime is not None and all(
                lock['modules'][role].get('model_preview') is True for role in ('modeling', 'rendering')),
                "modeling_operation": "modeling_preview_from_image", "render_operation": "modeling_preview_render",
                "physical_scale_required": False, "material_assumption_required": False,
                "simulation_performed": False, "full_geometry_retained": True},
            "simulation_qualities": ["visual", "strict"],
            "rendering_qualities": ["preview", "standard"],
            "providers": [{"id": "local-scientific", "network": False,
                           "fidelity": "trajectory_preserving"}],
            "full_state": False, "outputs": ["video/mp4", "application/zip"],
            "data": ["canonical_json", "solver_observation_csv", "sampled_display_csv", "data_dictionary"],
            "limitations": ["Only the pinned engine's declared physics and queries are supported.",
                            "CAD high precision, deep rendering and commercial diffusion are not advertised as installed."]}


def api(root: Path, engine: Path, job: Path, task: dict, lock: dict) -> None:
    call_path = job / "work/toolbox-call.json"
    call = read_json(call_path)
    operation, arguments = call["operation"], call["arguments"]
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    output = job / "work/toolbox-response.json"
    if operation == "pipeline_capabilities":
        if arguments:
            raise ValueError("pipeline_capabilities takes no arguments")
        result = capabilities(lock, task.get('modeling_runtime'))
    elif operation == 'modeling_from_image':
        result = image_module(root).generate_image_model(root, job, task, lock, arguments, atomic, object_hash)
    elif operation in ('modeling_preview_from_image', 'modeling_preview_render'):
        if not capabilities(lock, task.get('modeling_runtime'))['model_preview']['available']:
            raise ValueError('model preview requires compatible pinned modeling and rendering modules')
        if not delivery_ready(task):
            raise ValueError('data_delivery_unavailable: model preview requires video and model ZIP delivery')
        if operation == 'modeling_preview_from_image':
            result = image_module(root).generate_image_model(root, job, task, lock, arguments, atomic, object_hash, preview=True)
        else:
            from model_preview import render_preview
            result = render_preview(root, engine, job, task, lock, arguments, atomic, object_hash, stage)
    elif operation == "pipeline_configure":
        if set(arguments) - set(DEFAULT_SETTINGS):
            raise ValueError("unknown pipeline setting")
        selected = {**settings(job), **arguments}
        if (selected["modeling_quality"] != "standard"
                or selected["simulation_quality"] not in ("inherit", "visual", "strict")
                or selected["rendering_quality"] not in ("preview", "standard")):
            result = {"ok": False, "code": "unsupported_quality", "supported": capabilities(lock)}
        else:
            atomic(job / "work/pipeline/settings.json", selected)
            # All subsequent executions must be prepared with these exact choices.
            (job / "work/pipeline/plan.json").unlink(missing_ok=True)
            (job / "work/prepared-scene.json").unlink(missing_ok=True)
            result = {"ok": True, "settings": selected,
                      "next_action": "Prepare the explicit model using physics_prepare or pcb_prepare."}
    elif operation == "pipeline_status":
        if arguments:
            raise ValueError("pipeline_status is confined to the current task")
        path = job / "work/pipeline/state.json"
        result = {"ok": True, "state": read_json(path, 4_000_000) if path.exists() else {"status": "not_started"},
                  "settings": settings(job)}
    elif operation == "pipeline_render_prepare":
        if set(arguments) - {"rendering_quality"}:
            raise ValueError("rerender accepts only rendering_quality")
        current = read_json(job / "work/pipeline/state.json", 4_000_000)
        simulation = current.get("simulation", {})
        verify_checkpoint(simulation, job)
        if simulation.get("ok") is not True:
            raise ValueError("a verified saved simulation is required")
        selected = settings(job)
        selected["rendering_quality"] = arguments.get("rendering_quality", selected["rendering_quality"])
        if selected["rendering_quality"] not in ("preview", "standard"):
            raise ValueError("unsupported_render_quality")
        atomic(job / "work/pipeline/settings.json", selected)
        # Reuse the same task's prepared physics, but freeze a render-only plan.
        plan = freeze(job, lock)
        plan["mode"] = "render_only"
        plan["simulation_checkpoint"] = simulation["checkpoint_sha256"]
        plan["plan_id"] = object_hash({k: v for k, v in plan.items() if k != "plan_id"})
        atomic(job / "work/pipeline/plan.json", plan)
        result = {"ok": True, "ready_to_simulate": True, "plan_id": plan["plan_id"],
                  "solver_rerun": False, "next_action": "physics_simulate"}
    elif operation == "help" and arguments.get("topic") in ("pipeline", *ROLES):
        topic = arguments["topic"]
        if topic == "pipeline":
            result = {"ok": True, "text": (
                "Call pipeline_capabilities. Optionally pipeline_configure before preparing. "
                "Build the exact model through existing physics/PCB APIs, then physics_prepare/pcb_prepare. "
                "physics_simulate runs independently pinned modeling, simulation and rendering; host qq_video "
                "queues both verified MP4 and data.zip. Read help(topic=modeling|simulation|rendering). "
                "After a presentation failure, pipeline_render_prepare then physics_simulate reuses saved data. "
                "Never rerun unchanged failed physics. Raw/display data is not certified measurement."),
                      "capabilities": capabilities(lock)}
        else:
            module = inside(root, lock["modules"][topic]["path"])
            result = {"ok": True, "text": (module / "manual/SKILL.md").read_text()}
    else:
        # Legacy tools retain exact request/response semantics and query paths.
        if operation in ("physics_prepare", "pcb_prepare", "physics_system", "physics_example", "physics_patch"):
            (job / "work/pipeline/plan.json").unlink(missing_ok=True)
        if operation == "physics_prepare" and settings(job)["simulation_quality"] != "inherit":
            from physics_demo.jsonio import loads
            scene = (loads(arguments["scene_json"]) if 'scene_json' in arguments else
                     read_json(job / 'work/draft-scene.json', 4_000_000))
            scene.setdefault("budget", {})["validation"] = settings(job)["simulation_quality"]
            arguments = {**arguments, "scene_json": json.dumps(scene, allow_nan=False)}
            atomic(call_path, {**call, "arguments": arguments})
        if operation == "physics_simulate":
            check_plan(job, lock)
            if not delivery_ready(task):
                raise ValueError("data_delivery_unavailable: host must enable verified file delivery")
        spec = importlib.util.spec_from_file_location("_engine_modeling", engine / "modeling.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        compact_engine_models(job)
        module.api_call(engine, job, task)
        compact_engine_models(job)
        # An indented legacy validation report can include its normalized mesh.
        # The host still receives a bounded, compact response after this read.
        result = read_json(output, 4_000_000)["result"]
        if operation in ("physics_prepare", "pcb_prepare") and result.get("ok") and result.get("ready_to_simulate"):
            report = result.get("agent_report", {})
            estimate = report.get("estimated_wall_time_s", {}).get("p90")
            plan = freeze(job, lock, estimate)
            result["pipeline"] = {"plan_id": plan["plan_id"], "settings": plan["settings"],
                                  "required_outputs": ["video", "data"], "engine_version": lock["engine"]["version"]}
    atomic(output, {"schema_version": 1, "result": result})


def remaining(start: float, task: dict) -> float | None:
    wall = task["limits"]["wall_time_seconds"]
    if wall is None:
        return None
    value = wall - (time.monotonic() - start)
    if value <= 0:
        raise TimeoutError("pipeline deadline exhausted")
    return value


def stage(root: Path, engine: Path, job: Path, lock: dict, role: str, arguments: dict,
          start: float, task: dict, action: str = "run") -> dict:
    if role in ("modeling", "simulation"):
        output = Path(arguments["output_dir"])
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            # Preserve even an unsealed worker response after a crash. A retry
            # must not replace evidence with a new worker's directory error.
            raise StageFailure(role, {"ok": False, "code": "stage_incomplete",
                "message": "Unsealed stage output exists; retain it for explicit recovery.",
                "output_dir": str(output)})
    work = job / "work/pipeline/calls"
    work.mkdir(parents=True, exist_ok=True)
    name = role + "-" + action
    request, response = work / (name + ".json"), work / (name + "-result.json")
    response.unlink(missing_ok=True)
    atomic(request, {"schema_version": "stage-call/1", "stage": role, "action": action, "arguments": arguments})
    module = inside(root, lock["modules"][role]["path"])
    metadata = read_json(module / "module.json")
    argv = [sys.executable, str(inside(module, metadata["entrypoint"])),
            "--engine", str(engine), "--request", str(request), "--response", str(response)]
    with (work / (name + ".log")).open("w") as log:
        completed = subprocess.run(argv, input=b"", stdout=log, stderr=subprocess.STDOUT,
                                   timeout=remaining(start, task), check=False)
    if not response.is_file():
        raise RuntimeError(role + " did not produce a stage result")
    result = read_json(response, 4_000_000)
    if completed.returncode or result.get("ok") is False:
        raise StageFailure(role, result)
    return result


class StageFailure(RuntimeError):
    def __init__(self, role: str, result: dict):
        self.role, self.result = role, result
        super().__init__(role + " stage failed")


def seal_checkpoint(result: dict, job: Path) -> dict:
    files = []
    for key in ("bundle_path", "manifest_path", "result_path", "model_path"):
        if key in result:
            raw = Path(result[key])
            relative = raw.relative_to(job).as_posix()
            path = inside(job, relative)
            files.append({"path": relative, "sha256": sha256(path)})
    if "manifest_path" in result:
        manifest_path = Path(result["manifest_path"])
        manifest = read_json(manifest_path, 4_000_000)
        for item in manifest.get("artifacts", []):
            path = inside(manifest_path.parent, item["path"])
            if sha256(path) != item["sha256"]:
                raise ValueError("stage artifact hash mismatch")
            files.append({"path": path.relative_to(job).as_posix(), "sha256": item["sha256"]})
    sealed = {**result, "checkpoint_files": files}
    return {**sealed, "checkpoint_sha256": object_hash(sealed)}


def verify_checkpoint(result: dict, job: Path) -> None:
    files = result.get("checkpoint_files", [])
    body = {k: v for k, v in result.items() if k != "checkpoint_sha256"}
    if not files or result.get("checkpoint_sha256") != object_hash(body):
        raise ValueError("invalid saved stage checkpoint")
    for item in files:
        if sha256(inside(job, item["path"])) != item["sha256"]:
            raise ValueError("saved stage was modified; refusing reuse")


def run(root: Path, engine: Path, job: Path, task: dict, lock: dict) -> None:
    start = time.monotonic()
    if not delivery_ready(task):
        raise ValueError("data_delivery_unavailable: pipeline requires video and data delivery")
    plan, prepared = check_plan(job, lock)
    selected, budget = plan["settings"], task["limits"]["wall_time_seconds"]
    state_path = job / "work/pipeline/state.json"
    state = read_json(state_path, 4_000_000) if state_path.exists() else {}
    compute_key = object_hash({"prepared": prepared, "engine": lock["engine"],
                              "modules": {role: lock["modules"][role] for role in ("modeling", "simulation")}})
    if state and state.get("compute_key") != compute_key:
        # Preserve old evidence; a changed model receives a fresh isolated attempt.
        attempt = job / "work/pipeline/history" / (state["compute_key"][:24] + "-" + str(time.time_ns()))
        attempt.mkdir(parents=True, exist_ok=True)
        for path in (job / "work/pipeline/model", job / "work/physics", job / "work/pcb"):
            if path.exists():
                destination = attempt / path.name
                if destination.exists():
                    raise ValueError("prior attempt already archived")
                os.rename(path, destination)
        atomic(attempt / "state.json", state)
        state = {}
    state.update(schema_version="pipeline-state/1", compute_key=compute_key,
                 plan_id=plan["plan_id"], bundle=lock, status="running")
    domain = prepared.get("domain", "physics")
    current_role = "modeling"
    try:
        if plan["mode"] == "render_only":
            simulation = state.get("simulation", {})
            verify_checkpoint(simulation, job)
            if simulation["checkpoint_sha256"] != plan["simulation_checkpoint"]:
                raise ValueError("render-only simulation changed")
        else:
            if state.get("modeling", {}).get("ok"):
                verify_checkpoint(state["modeling"], job)
            else:
                atomic(state_path, state)
                built = stage(root, engine, job, lock, "modeling", {
                    "domain": domain, "model": prepared["scene"],
                    "output_dir": str(job / "work/pipeline/model"), "budget_seconds": budget,
                    "unlimited": budget is None, "quality": selected["modeling_quality"]}, start, task)
                state["modeling"] = seal_checkpoint(built, job)
                atomic(state_path, state)
            current_role = "simulation"
            if state.get("simulation", {}).get("ok"):
                verify_checkpoint(state["simulation"], job)
            else:
                output = job / ("work/pcb" if domain == "pcb_thermal" else "work/physics/artifacts")
                simulated = stage(root, engine, job, lock, "simulation", {
                    "model_bundle_path": state["modeling"]["bundle_path"], "output_dir": str(output),
                    "budget_seconds": budget, "unlimited": budget is None}, start, task)
                state["simulation"] = seal_checkpoint(simulated, job)
                atomic(state_path, state)
        simulation = state["simulation"]
        current_role = "data_export"
        source = {"result_path": simulation["result_path"], "model_path": simulation["model_path"],
                  "domain": domain, "output_dir": str(job / "artifacts")}
        exported = stage(root, engine, job, lock, "rendering", {
            **source, "max_bytes": task["delivery"]["max_data_bytes"],
            "modeling_assets": image_module(root).used_modeling_assets(job, prepared['scene'], root)}, start, task, "export")
        state["data"] = exported
        atomic(state_path, state)
        current_role = "rendering"
        rendered = stage(root, engine, job, lock, "rendering", {
            **source, "budget_seconds": remaining(start, task),
            "max_output_bytes": task["limits"]["max_output_bytes"],
            "quality": selected["rendering_quality"]}, start, task, "render")
        verify_checkpoint(simulation, job)
        state["rendering"] = rendered
        state.update(status="succeeded", failed_stage=None, solver_rerun=False if plan["mode"] == "render_only" else None)
        atomic(state_path, state)
        gate = simulation["quality_gate"]
        # Reuse the engine's gated, query-specific textual answer alongside the full data ZIP.
        spec = importlib.util.spec_from_file_location("_legacy_results", engine / "toolbox_adapter.py")
        legacy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(legacy)
        if domain == "pcb_thermal" and hasattr(legacy, "_pcb_data_reply"):
            data_status, data_summary, data_answers = legacy._pcb_data_reply(
                read_json(Path(simulation["result_path"]), 128_000_000), task["request"]["text"])
        elif domain == "physics" and hasattr(legacy, "_physics_data_reply"):
            data_status, data_summary, data_answers = legacy._physics_data_reply(
                simulation["summary"], task["request"]["text"])
        else:
            # 1.4.0 predates the optional reply helpers. Data delivery must not
            # depend on later engine additions or fabricate a numerical answer.
            data_status = "available" if gate.get("numerical_passed") is True else "diagnostic_only"
            data_summary = ("原始结果、模型、CSV 和数据字典见 data.zip；定量结论须按已声明查询读取。"
                            if data_status == "available" else
                            "data.zip 仅供诊断和可视化；数值验收未通过，不能作为定量结论。")
            data_answers = []
        video = job / "artifacts/simulation.mp4"
        data = job / "artifacts/data.zip"
        if sha256(video) != rendered["sha256"] or sha256(data) != exported["sha256"]:
            raise ValueError("published presentation changed")
        atomic(job / "artifacts/result-manifest.json", {"schema_version": 1,
            "status": "succeeded", "summary": "模拟、保真视频和 JSON/CSV 数据包已通过检查，等待宿主交付。",
            "outputs": [{"path": video.name, "media_type": "video/mp4", "sha256": sha256(video)}],
            "attachments": [{"path": data.name, "media_type": "application/zip", "role": "data",
                             "size_bytes": data.stat().st_size, "sha256": sha256(data)}],
            "verification": {"passed": True, "numerical_passed": gate.get("numerical_passed") is True,
                "checks": ["model_validation", "simulation_quality", "canonical_hash_unchanged", "video_decode", "data_manifest"],
                "validation_mode": gate.get("validation_mode"),
                "precision_warnings": gate.get("precision_warnings", [])},
            "pipeline": {"bundle": lock, "plan_id": plan["plan_id"], "settings": selected,
                         "simulation_sha256": sha256(Path(simulation["result_path"]))},
            "data_status": data_status, "data_summary": data_summary, "data_answers": data_answers})
        atomic(job / "progress.json", {"schema_version": 1, "state": "completed", "fraction": 1, "updated_at": time.time()})
    except Exception as error:
        state.update(status="failed", failed_stage=current_role)
        atomic(state_path, state)
        detail = error.result if isinstance(error, StageFailure) else {"message": str(error)[:1000]}
        atomic(job / "artifacts/result-manifest.json", {"schema_version": 1, "status": "failed",
            "failure": {"code": "pipeline_stage_failed", "stage": current_role, "retryable": False,
                "message": str(error)[:300], "detail": detail},
            "preserved_simulation": bool(state.get("simulation", {}).get("ok"))})
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("api", "probe", "run"), required=True)
    parser.add_argument("--task", type=Path, required=True)
    args = parser.parse_args()
    root, job = Path(__file__).resolve().parent, args.task.resolve().parent
    task = read_json(args.task)
    ensure_task(task)
    lock = verify_bundle(root)
    engine = load_engine(root, lock)
    (job / "work/pipeline").mkdir(parents=True, exist_ok=True)
    bound = job / "work/pipeline/bundle-lock.json"
    if bound.exists() and read_json(bound) != lock:
        raise ValueError("task cannot change pinned pipeline components")
    atomic(bound, lock)
    with (job / "work/pipeline/.execution.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.phase == "api":
            try:
                api(root, engine, job, task, lock)
            except (ValueError, KeyError, OSError) as error:
                atomic(job / "work/toolbox-response.json", {"schema_version": 1,
                    "result": {"ok": False, "code": "pipeline_request_rejected", "message": str(error)[:1000]}})
        else:
            if not (job / "work/pipeline/plan.json").exists() and not task["request"].get("plan_required"):
                import run_simulation
                from physics_demo.runner import prepare
                _, scene = run_simulation.route(task["request"]["text"])
                scene.setdefault("budget", {}).setdefault("validation", "visual")
                prepared = prepare(scene, task["limits"]["wall_time_seconds"],
                                   unlimited=task["limits"]["wall_time_seconds"] is None)
                if prepared.get("ok") and prepared.get("ready_to_simulate"):
                    atomic(job / "work/prepared-scene.json", {"schema_version": 1, "scene": prepared["scene"]})
                    freeze(job, lock, prepared["agent_report"]["estimated_wall_time_s"]["p90"])
            if args.phase == "probe":
                try:
                    plan, _ = check_plan(job, lock)
                    ready = delivery_ready(task)
                    estimate = plan["estimated_seconds"]
                    wall = task["limits"]["wall_time_seconds"]
                    if wall is not None and estimate > wall:
                        ready = False
                    reason = "" if ready else "data_delivery_unavailable_or_budget"
                except (ValueError, OSError, KeyError) as error:
                    ready, estimate, reason = False, None, str(error)[:300]
                atomic(job / "capability.json", {"schema_version": 1, "supported": ready,
                    "estimated_seconds": estimate, "reason": reason})
            else:
                run(root, engine, job, task, lock)


if __name__ == "__main__":
    main()
