#!/usr/bin/env python3
"""Compose immutable stage modules around an existing physics toolbox snapshot."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
import sys

from registry import manifest, publish, write_json
from pipeline.bundle import FORMATS, ROLES, digest_tree, validate_module, verify_bundle

ROOT = Path(__file__).resolve().parent
PIPELINE = ROOT / "pipeline"
VERSION = "0.2.0"
COMPOSED_VERSION = "0.3.0"
ROLE_VERSIONS = {"modeling": "0.3.0", "rendering": "0.3.0"}


def copy_tree(source: Path, target: Path) -> None:
    digest_tree(source)  # Reject links before copytree can follow them.
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))


def build_module(role: str, output: Path) -> dict:
    if role not in ROLES:
        raise ValueError("unknown module role")
    if output.exists():
        raise ValueError("module output must be a new directory")
    output.mkdir(parents=True)
    package = "pipeline_presentation" if role == "rendering" else "pipeline_stages"
    if role == 'rendering':
        copy_tree(ROOT / package, output / package)
    else:
        # A modeling edit must not change the independent simulation module's
        # digest merely because both implementations live in one source folder.
        (output / package).mkdir()
        for filename in ('__init__.py', 'common.py', role + '.py'):
            shutil.copyfile(ROOT / package / filename, output / package / filename)
    if role == 'modeling':
        copy_tree(ROOT / 'modeling_flow', output / 'modeling_flow')
        (output / 'flow_runner.py').write_text('from modeling_flow.__main__ import main\nif __name__ == "__main__":\n    raise SystemExit(main())\n')
    shutil.copyfile(PIPELINE / "worker.py", output / "adapter.py")
    copy_tree(PIPELINE / 'manual' / role, output / 'manual')
    info = {"schema_version": "stage-module/1", "id": "physics-" + role,
        "version": ROLE_VERSIONS.get(role, VERSION), "role": role, "entrypoint": "adapter.py",
        "input_schema": FORMATS[role][0], "output_schema": FORMATS[role][1],
        "engine_contract": "physics-python/1", "network": False}
    if role in ('modeling', 'rendering'):
        info['model_preview'] = True
    write_json(output / "module.json", info)
    # Stage packages can use the existing content-addressed registry as storage.
    write_json(output / "toolbox.json", {"schema_version": 1, "id": info["id"],
        "version": info["version"], "entrypoint": "adapter.py", "package_kind": "stage-module",
        "capabilities": [role + " stage; compose before activating on QQ"]})
    return info


def build(engine: Path, output: Path, modules: dict | None = None) -> dict:
    if set(modules or {}) - set(ROLES):
        raise ValueError("unknown module role override")
    engine = engine.resolve()
    output = output.resolve()
    if output.exists() or engine == output or engine in output.parents or output in engine.parents:
        raise ValueError("output must be a new directory outside the engine package")
    engine_manifest = manifest(engine)
    if engine_manifest.get("id") != "physics" or not (engine / "physics_release/physics_demo/runner.py").is_file():
        raise ValueError("expected a compatible physics toolbox snapshot")
    output.mkdir(parents=True)
    copy_tree(engine, output / "engine")
    lock = {"schema_version": "pipeline-bundle/1", "engine": {
        "id": engine_manifest["id"], "version": engine_manifest["version"],
        "path": "engine", "digest": digest_tree(output / "engine")}, "modules": {}}
    (output / "modules").mkdir()
    for role in ROLES:
        target = output / "modules" / role
        source = (modules or {}).get(role)
        if source:
            source = Path(source).resolve()
            validate_module(source, role)
            copy_tree(source, target)
        else:
            build_module(role, target)
        info = validate_module(target, role)
        lock["modules"][role] = {"id": info["id"], "version": info["version"],
            "path": "modules/" + role, "digest": digest_tree(target)}
        if info.get('model_preview') is True:
            lock['modules'][role]['model_preview'] = True
    for filename in ("adapter.py", "bundle.py", "image_modeling.py", "model_preview.py"):
        shutil.copyfile(PIPELINE / filename, output / filename)
    write_json(output / "bundle-lock.json", lock)
    api = dict(engine_manifest.get("agent_api", {}))
    api["operations"] = sorted(set(api.get("operations", [])) | {
        "pipeline_capabilities", "pipeline_configure", "pipeline_status", "pipeline_render_prepare",
        "modeling_from_image"})
    if all(lock['modules'][role].get('model_preview') is True for role in ('modeling', 'rendering')):
        api['operations'] = sorted(set(api['operations']) | {'modeling_preview_from_image', 'modeling_preview_render'})
        api['model_preview_render_operation'] = 'modeling_preview_render'
    api["execution_operation"] = "physics_simulate"
    # Saved scenes must pass this composed adapter's preparation before probe.
    # Host-readable metadata does not change the original engine/stage bytes.
    preparation = {}
    if "physics_prepare" in api["operations"]:
        preparation["default"] = {"operation": "physics_prepare", "argument": "scene_json"}
        preparation["physics"] = dict(preparation["default"])
    if "pcb_prepare" in api["operations"]:
        preparation["pcb_thermal"] = {"operation": "pcb_prepare", "argument": "spec_json"}
    if preparation:
        api["context_preparation"] = preparation
    api["instructions"] = (
        "This package composes independently pinned modeling, simulation and rendering modules. "
        "Read help(topic=pipeline) first. Video and data ZIP are required; the host must support file delivery. "
        "Use pipeline_capabilities and pipeline_configure before preparing. Defaults are standard modeling, "
        "For an image requiring 3D reconstruction, use modeling_from_image with the current event's image_id, "
        "explicit physical_extent_m and material assumptions; it returns a task-owned mesh_ref. "
        "For shape-only reconstruction and a 360-degree model video, use modeling_preview_from_image "
        "with image_id, then modeling_preview_render with its model_ref, then qq_video using video_path. "
        "This path preserves the full display geometry and exports GLB/OBJ/JSON/reference/license ZIP. "
        "It needs no physical dimensions or density; never invent them. No simulation is performed. "
        "Never replace unavailable image inference with guessed geometric primitives. "
        "model-selected visual/strict simulation and standard faithful rendering. For numerical questions "
        "set simulation_quality=strict and declare queries before physics_prepare. Use existing physics_* "
        "or pcb_* tools to build and prepare the exact model, then physics_simulate and qq_video as before. "
        "Data ZIP is queued by the host alongside the video. Never claim delivered until host receipts exist. "
        "pipeline_render_prepare reuses this task's saved result after presentation failure and must never rerun physics. "
        "High/deep/full-state capabilities must be explicitly advertised, not guessed. "
        + api.get("instructions", ""))
    write_json(output / "toolbox.json", {"schema_version": 1, "id": "physics-pipeline",
        "version": COMPOSED_VERSION, "entrypoint": "adapter.py", "requires_data_delivery": True,
        "capabilities": ["独立建模、数据模拟、保真视频渲染；机械与 PCB 的 MP4+JSON/CSV 数据 ZIP",
                         "原子组合版本热切换，运行中任务保留原引擎和三个模块版本"],
        "limitations": ["需要支持数据附件的 QQ 宿主；保持原物理引擎能力边界",
                        "当前本地渲染不声称 diffusion 或完整高精状态；阶段实际能力由发现接口公布"],
        "agent_api": api})
    verify_bundle(output)
    return lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, default=ROOT / "physics")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--module-only", choices=ROLES)
    for role in ROLES:
        parser.add_argument("--" + role + "-module", type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    if args.activate and (not args.registry or args.module_only):
        parser.error("--activate requires a registry and a composed pipeline, not a standalone stage")
    if args.module_only:
        result = build_module(args.module_only, args.output)
    else:
        result = build(args.engine, args.output,
                       {role: getattr(args, role + "_module") for role in ROLES})
    if args.registry:
        result = {"components": result, "package": publish(args.output, args.registry, args.activate)}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
