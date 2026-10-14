"""Fixed read-only verifier entrypoint for a published pipeline package.

Sources come from the current task's sealed simulation checkpoint. CLI claims
can confirm their hashes but can never select source paths, an interpreter,
asset inventory, domain, budget, or a different archive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import sys


SCHEMA = "portable-physics-verification/1"
DATA_SCHEMA = "pipeline-data/2"
MAX_SOURCE = 128 * 1024 * 1024
DEFAULT_SETTINGS = {"modeling_quality": "standard", "simulation_quality": "inherit",
                    "rendering_quality": "standard"}


def object_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def regular(root: Path, relative: str, maximum: int = MAX_SOURCE) -> Path:
    if (not isinstance(relative, str) or not relative or "\\" in relative or "\0" in relative
            or PurePosixPath(relative).is_absolute()
            or any(part in {"", ".", ".."} for part in relative.split("/"))):
        raise ValueError("invalid confined verification path")
    path = root / relative
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("verification paths cannot use symlinks")
    path = path.resolve(strict=True)
    path.relative_to(root)
    if not stat.S_ISREG(path.stat().st_mode) or not 0 < path.stat().st_size <= maximum:
        raise ValueError("verification input is not a bounded regular file")
    return path


def checkpoint(value: dict, job: Path, sha256) -> dict[str, str]:
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise ValueError("missing successful simulation checkpoint")
    body = {key: item for key, item in value.items() if key != "checkpoint_sha256"}
    if value.get("checkpoint_sha256") != object_hash(body):
        raise ValueError("simulation checkpoint seal changed")
    files = value.get("checkpoint_files")
    if not isinstance(files, list) or not 1 <= len(files) <= 256:
        raise ValueError("invalid checkpoint file inventory")
    checked, total = {}, 0
    for item in files:
        if not isinstance(item, dict) or re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256"))) is None:
            raise ValueError("invalid checkpoint file hash")
        path = regular(job, item.get("path"))
        if item["path"] not in checked:
            total += path.stat().st_size
        if total > MAX_SOURCE or sha256(path) != item["sha256"]:
            raise ValueError("saved checkpoint source changed")
        if item["path"] in checked and checked[item["path"]] != item["sha256"]:
            raise ValueError("conflicting checkpoint source hashes")
        checked[item["path"]] = item["sha256"]
    return checked


def verify(job: Path, archive_hash: str, archive_size: int,
           result_hash: str, model_hash: str) -> dict:
    root = Path(__file__).resolve().parent
    # -I intentionally removes the script directory from sys.path. Only the
    # fixed package containing this already host-verified script is added back.
    sys.path.insert(0, str(root))
    from bundle import digest_tree, inside, read_json, sha256, verify_bundle

    if not job.is_absolute() or any(path.is_symlink() for path in (job, *job.parents)):
        raise ValueError("job must be an absolute task directory without symlinks")
    job = job.resolve(strict=True)
    pin = read_json(regular(job, "toolbox-pin.json", 65536))
    if (not isinstance(pin.get("registry"), str) or not Path(pin["registry"]).is_absolute()
            or root != (Path(pin["registry"]) / "versions" / str(pin.get("digest"))).resolve()
            or digest_tree(root) != pin.get("digest")):
        raise ValueError("independent verifier package pin changed")
    info = read_json(regular(root, "toolbox.json", 65536))
    if (info.get("portable_physics_data") != DATA_SCHEMA
            or any(info.get(key) != pin.get(key) for key in ("id", "version"))):
        raise ValueError("pinned toolbox does not support portable physics data")
    lock = verify_bundle(root)
    if lock["modules"]["rendering"].get("portable_data") != DATA_SCHEMA:
        raise ValueError("pinned rendering module cannot verify portable data")
    task = read_json(regular(job, "task.json", 1_000_000))
    request = read_json(regular(job, "request.json", 65536))
    if (task.get("schema_version") != 1 or task.get("task_id") != job.name
            or task.get("task_id") != request.get("event_id")
            or task.get("output_dir") != "artifacts" or task.get("work_dir") != "work"
            or task.get("limits", {}).get("network") is not False):
        raise ValueError("portable verification belongs to another task")
    maximum = task.get("delivery", {}).get("max_data_bytes")
    if (task.get("delivery", {}).get("data_attachments") is not True
            or type(maximum) is not int or not 1024 <= maximum <= MAX_SOURCE
            or type(archive_size) is not int or not 0 < archive_size <= maximum):
        raise ValueError("invalid task data delivery budget")
    prepared = read_json(regular(job, "work/prepared-scene.json", 4_000_000), 4_000_000)
    plan = read_json(regular(job, "work/pipeline/plan.json", 65536))
    settings_file = job / "work/pipeline/settings.json"
    selected = read_json(regular(job, "work/pipeline/settings.json", 65536)) if settings_file.exists() else DEFAULT_SETTINGS
    if (plan.get("schema_version") != "pipeline-plan/1" or plan.get("mode") not in {"full", "render_only"}
            or plan.get("plan_id") != object_hash({key: item for key, item in plan.items() if key != "plan_id"})
            or plan.get("prepared_sha256") != object_hash(prepared)
            or plan.get("bundle_sha256") != object_hash(lock) or plan.get("settings") != selected
            or read_json(regular(job, "work/pipeline/bundle-lock.json")) != lock
            or prepared.get("schema_version") != 1 or not isinstance(prepared.get("scene"), dict)):
        raise ValueError("prepared scene or pinned plan changed")
    state = read_json(regular(job, "work/pipeline/state.json", 4_000_000), 4_000_000)
    compute_key = object_hash({"prepared": prepared, "engine": lock["engine"],
        "modules": {role: lock["modules"][role] for role in ("modeling", "simulation")}})
    if (state.get("schema_version") != "pipeline-state/1" or state.get("status") != "succeeded"
            or state.get("plan_id") != plan["plan_id"] or state.get("bundle") != lock
            or state.get("compute_key") != compute_key):
        raise ValueError("saved pipeline state does not match the current plan")
    simulation = state.get("simulation")
    source_hashes = checkpoint(simulation, job, sha256)
    modeling = state.get("modeling")
    modeling_hashes = checkpoint(modeling, job, sha256)
    domain = prepared.get("domain", "physics")
    if domain not in {"physics", "pcb_thermal"} or simulation.get("domain") != domain:
        raise ValueError("simulation domain changed")
    directory = "work/physics/artifacts/" if domain == "physics" else "work/pcb/"
    result_name, model_name = directory + "result.json", directory + "model.json"
    result_path, model_path = regular(job, result_name), regular(job, model_name)
    for key, path in (("result_path", result_path), ("model_path", model_path)):
        if simulation.get(key) != str(path):
            raise ValueError("simulation source is outside the fixed current checkpoint")
    if (source_hashes.get(result_name) != result_hash or source_hashes.get(model_name) != model_hash
            or plan["mode"] == "render_only" and plan.get("simulation_checkpoint") != simulation["checkpoint_sha256"]
            or modeling_hashes.get("work/pipeline/model/model.json") != model_hash
            or modeling.get("bundle_path") != str(regular(job, "work/pipeline/model/model.json"))):
        raise ValueError("expected source hashes differ from the sealed current checkpoint")
    model_document = read_json(model_path, 64 * 1024 * 1024)
    if object_hash(model_document.get("model", model_document.get("scene", model_document))) != object_hash(prepared["scene"]):
        raise ValueError("saved numerical model differs from the prepared scene")
    archive = regular(job, "artifacts/data.zip", maximum)
    if archive.stat().st_size != archive_size or sha256(archive) != archive_hash:
        raise ValueError("portable ZIP differs from host-checked attachment")
    engine = inside(root, lock["engine"]["path"])
    rendering = inside(root, lock["modules"]["rendering"]["path"])
    sys.path[:0] = [str(rendering), str(engine / "physics_release"), str(engine)]
    import image_modeling
    import liquefy
    inventory = image_modeling.used_modeling_assets(job, prepared["scene"], root, readonly=True)
    inventory += liquefy.used_liquefy_assets(job, prepared["scene"], root, task, lock, readonly=True)
    from pipeline_presentation.portable_result import verify_portable_data
    checked = verify_portable_data(result_path, model_path, archive, domain=domain,
                                   max_bytes=maximum, modeling_assets=inventory)
    if (checked.get("verified") is not True or checked.get("schema") != DATA_SCHEMA
            or checked.get("source_result_sha256") != result_hash
            or checked.get("source_model_sha256") != model_hash
            or checked.get("sha256") != archive_hash or checked.get("bytes") != archive_size):
        raise ValueError("official portable verifier returned inconsistent evidence")
    return {"schema": SCHEMA, "data_schema": DATA_SCHEMA, "verified": True,
        "task_id": task["task_id"], "bundle_sha256": pin["digest"], "plan_id": plan["plan_id"],
        "simulation_checkpoint_sha256": simulation["checkpoint_sha256"],
        "engine_sha256": lock["engine"]["digest"], "rendering_sha256": lock["modules"]["rendering"]["digest"],
        "source_result_sha256": result_hash, "source_model_sha256": model_hash,
        "sha256": archive_hash, "bytes": archive_size,
        "projected_result_sha256": checked["projected_result_sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only source-based portable data verification")
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--archive-bytes", type=int, required=True)
    parser.add_argument("--source-result-sha256", required=True)
    parser.add_argument("--source-model-sha256", required=True)
    args = parser.parse_args()
    try:
        for value in (args.archive_sha256, args.source_result_sha256, args.source_model_sha256):
            if re.fullmatch(r"[a-f0-9]{64}", value) is None:
                raise ValueError("expected hashes must be lowercase SHA256")
        value = verify(args.job, args.archive_sha256, args.archive_bytes,
                       args.source_result_sha256, args.source_model_sha256)
    except Exception:
        print(json.dumps({"schema": SCHEMA, "verified": False, "error": "portable_source_verification_failed"}))
        return 1
    print(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
