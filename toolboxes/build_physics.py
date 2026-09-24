#!/usr/bin/env python3
"""Build and verify the independently published physics toolbox."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


SOURCE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PACKAGE = SOURCE_ROOT / "physics_demo"
SOURCE_EXAMPLES = SOURCE_ROOT / "examples"
SOURCE_MANUAL = SOURCE_ROOT / "agent" / "physics-simulation"
CANONICAL_SCENE = SOURCE_ROOT / "examples" / "droplet_ground.json"
RELEASE_ROOT = Path(__file__).resolve().parent / "physics"
RELEASE_PACKAGE = RELEASE_ROOT / "physics_release" / "physics_demo"
RELEASE_SCENES = RELEASE_ROOT / "physics_release" / "scenes"
RELEASE_EXAMPLES = RELEASE_ROOT / "physics_release" / "examples"
RELEASE_MANUAL = RELEASE_ROOT / "manual"
PREBUILT = RELEASE_ROOT / "prebuilt"
MANIFEST = RELEASE_ROOT / "physics_release" / "manifest.json"
MANAGED_SUFFIXES = {".py", ".c", ".h", ".m"}
PREBUILT_NAMES = (
    "libphysics_native.dylib",
    "renderer-h264",
    "renderer-mjpeg",
    "decode-probe",
)
PACKAGE_FILES = (
    "toolbox.json", "toolbox_adapter.py", "modeling.py", "run_simulation.py",
    "delivery.py", "pcb_thermal.py", "pcb_video.py", "pcb_thermal_demo.json",
)
PROFILES = {
    "droplet_ground.json": {
        "name": "qq-water-droplet-ground-balanced",
        "spacing": 0.0004,
        "duration": 0.20,
    },
    "droplet_ground_fast.json": {
        "name": "qq-water-droplet-ground-fast",
        "spacing": 0.0005,
        "duration": 0.20,
    },
    "droplet_ground_rapid.json": {
        "name": "qq-water-droplet-ground-rapid",
        "spacing": 0.0006,
        "duration": 0.15,
    },
}


class ReleaseBuildError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _managed_files(root: Path) -> dict[Path, Path]:
    return {
        path.relative_to(root): path
        for path in root.rglob("*")
        if path.is_file() and path.suffix in MANAGED_SUFFIXES
    }


def _copied_files(root: Path, *, source: bool = False) -> dict[Path, Path]:
    """Files in a copied documentation/example tree, excluding Python caches."""
    return {
        relative: path
        for path in root.rglob("*")
        if path.is_file()
        if (relative := path.relative_to(root)).suffix != ".pyc"
        and "__pycache__" not in relative.parts
        and (not source or path.name != ".DS_Store")
    }


def _sync_copy_tree(source_root: Path, target_root: Path) -> None:
    if not source_root.is_dir():
        raise ReleaseBuildError(f"missing source directory: {source_root}")
    source_files = _copied_files(source_root, source=True)
    target_files = _copied_files(target_root)
    for relative, source in source_files.items():
        _atomic_copy(source, target_root / relative)
    for relative in target_files.keys() - source_files.keys():
        target_files[relative].unlink()


def _verify_copy_tree(
    label: str, source_root: Path, target_root: Path, failures: list[str]
) -> None:
    if not source_root.is_dir():
        failures.append(f"missing source directory: {source_root}")
        return
    source_files = _copied_files(source_root, source=True)
    target_files = _copied_files(target_root)
    for relative in sorted(source_files.keys() - target_files.keys()):
        failures.append(f"missing release {label} file: {relative.as_posix()}")
    for relative in sorted(target_files.keys() - source_files.keys()):
        failures.append(f"extra release {label} file: {relative.as_posix()}")
    for relative in sorted(source_files.keys() & target_files.keys()):
        if _sha256(source_files[relative]) != _sha256(target_files[relative]):
            failures.append(f"stale release {label} file: {relative.as_posix()}")


def _atomic_copy(source: Path, target: Path, *, executable: bool = False) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copyfile(source, temporary)
        mode = source.stat().st_mode
        if executable:
            mode |= stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        os.chmod(temporary, stat.S_IMODE(mode))
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _scene_documents() -> dict[str, dict[str, Any]]:
    canonical = json.loads(CANONICAL_SCENE.read_text(encoding="utf-8"))
    if not isinstance(canonical, dict):
        raise ReleaseBuildError("canonical water-drop scene must be an object")
    scenes: dict[str, dict[str, Any]] = {}
    for filename, profile in PROFILES.items():
        scene = deepcopy(canonical)
        scene["name"] = profile["name"]
        scene["world"]["duration"] = profile["duration"]
        scene["world"]["output_fps"] = 60
        scene["budget"].update(
            backend="native", quality="balanced", wall_time_s=30
        )
        scene["entities"][0]["spacing"] = profile["spacing"]
        scenes[filename] = scene
    scene = json.loads((SOURCE_ROOT / 'examples/three_body.json').read_text())
    scene['budget'].update(backend='native', wall_time_s=30)
    scenes['three_body.json'] = scene
    return scenes


def _sync_sources() -> None:
    source_files = _managed_files(SOURCE_PACKAGE)
    target_files = _managed_files(RELEASE_PACKAGE)
    for relative, source in source_files.items():
        _atomic_copy(source, RELEASE_PACKAGE / relative)
    for relative, target in target_files.items():
        if relative not in source_files:
            target.unlink()
    scenes = _scene_documents()
    for filename, scene in scenes.items():
        _atomic_write_json(RELEASE_SCENES / filename, scene)
    for path in RELEASE_SCENES.iterdir():
        if path.is_file() and path.name not in scenes:
            path.unlink()
    _sync_copy_tree(SOURCE_EXAMPLES, RELEASE_EXAMPLES)
    _sync_copy_tree(SOURCE_MANUAL, RELEASE_MANUAL)
    for name in ("tools.json", "scene-v1.schema.json"):
        _atomic_copy(SOURCE_ROOT / "agent" / name, RELEASE_ROOT / name)


def _build_prebuilt() -> None:
    os.environ.pop("PHYSICS_DEMO_NATIVE_LIBRARY", None)
    os.environ.pop("PHYSICS_DEMO_PREBUILT_VIDEO_DIR", None)
    sys.path.insert(0, str(SOURCE_ROOT))

    from physics_demo.core.native_backend import _compile_library
    from physics_demo.io.video import _compile_renderer, _renderer_compiler

    native, _ = _compile_library(60.0)
    _atomic_copy(native, PREBUILT / "libphysics_native.dylib", executable=True)

    clang = _renderer_compiler()
    video_root = SOURCE_PACKAGE / "io"
    render_core = video_root / "video_render_core.m"
    render_header = video_root / "video_render_core.h"
    renderers = (
        (
            "renderer-h264",
            [video_root / "video_renderer.m", render_core, render_header],
            ("Foundation", "AVFoundation", "CoreMedia", "CoreVideo", "CoreGraphics"),
        ),
        (
            "renderer-mjpeg",
            [video_root / "video_mjpeg_renderer.m", render_core, render_header],
            ("Foundation", "CoreGraphics", "ImageIO"),
        ),
        (
            "decode-probe",
            [video_root / "video_decode_probe.m"],
            (
                "Foundation",
                "AVFoundation",
                "CoreMedia",
                "CoreVideo",
                "CoreGraphics",
                "ImageIO",
            ),
        ),
    )
    for name, sources, frameworks in renderers:
        binary = _compile_renderer(
            clang,
            sources=sources,
            frameworks=frameworks,
            cache_name=name,
            timeout_seconds=30.0,
        )
        _atomic_copy(binary, PREBUILT / name, executable=True)
    for path in PREBUILT.rglob("*"):
        if path.is_file() and path.relative_to(PREBUILT).as_posix() not in PREBUILT_NAMES:
            path.unlink()


def _manifest_payload() -> dict[str, Any]:
    files: dict[str, str] = {}
    for relative, path in sorted(_managed_files(RELEASE_PACKAGE).items()):
        files[f"physics_demo/{relative.as_posix()}"] = _sha256(path)
    for filename in sorted(_scene_documents()):
        path = RELEASE_SCENES / filename
        files[f"scenes/{filename}"] = _sha256(path)
    for relative, path in sorted(_copied_files(RELEASE_EXAMPLES).items()):
        files[f"examples/{relative.as_posix()}"] = _sha256(path)
    for relative, path in sorted(_copied_files(RELEASE_MANUAL).items()):
        files[f"manual/{relative.as_posix()}"] = _sha256(path)
    for name in ("tools.json", "scene-v1.schema.json"):
        files[name] = _sha256(RELEASE_ROOT / name)
    for name in PREBUILT_NAMES:
        files[f"prebuilt/{name}"] = _sha256(PREBUILT / name)
    for name in PACKAGE_FILES:
        files[f"package/{name}"] = _sha256(RELEASE_ROOT / name)
    aggregate = hashlib.sha256()
    for name, digest in sorted(files.items()):
        aggregate.update(name.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(digest.encode("ascii"))
        aggregate.update(b"\n")
    return {
        "format_version": 1,
        "release": "qq-simulator-physics",
        "engine_sha256": aggregate.hexdigest(),
        "profiles": {
            filename: profile for filename, profile in sorted(PROFILES.items())
        },
        "files": files,
    }


def _verify() -> dict[str, Any]:
    failures: list[str] = []
    source_files = _managed_files(SOURCE_PACKAGE)
    target_files = _managed_files(RELEASE_PACKAGE)
    if set(source_files) != set(target_files):
        failures.append("embedded engine file list differs from source engine")
    for relative in sorted(set(source_files) & set(target_files)):
        if _sha256(source_files[relative]) != _sha256(target_files[relative]):
            failures.append(f"stale embedded engine file: {relative.as_posix()}")

    expected_scenes = _scene_documents()
    actual_scenes = {
        path.name for path in RELEASE_SCENES.iterdir() if path.is_file()
    } if RELEASE_SCENES.is_dir() else set()
    for filename in sorted(actual_scenes - expected_scenes.keys()):
        failures.append(f"extra release scene: {filename}")
    for filename, expected in expected_scenes.items():
        path = RELEASE_SCENES / filename
        try:
            actual = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            failures.append(f"unreadable release scene {filename}: {error}")
            continue
        if actual != expected:
            failures.append(f"stale release scene: {filename}")

    _verify_copy_tree("example", SOURCE_EXAMPLES, RELEASE_EXAMPLES, failures)
    _verify_copy_tree("manual", SOURCE_MANUAL, RELEASE_MANUAL, failures)
    for name in ("tools.json", "scene-v1.schema.json"):
        source = SOURCE_ROOT / "agent" / name
        target = RELEASE_ROOT / name
        if not target.is_file():
            failures.append(f"missing release file: {name}")
        elif _sha256(source) != _sha256(target):
            failures.append(f"stale release file: {name}")

    actual_prebuilt = {
        path.relative_to(PREBUILT).as_posix()
        for path in PREBUILT.rglob("*") if path.is_file()
    }
    for name in sorted(actual_prebuilt - set(PREBUILT_NAMES)):
        failures.append(f"extra prebuilt asset: {name}")
    for name in PREBUILT_NAMES:
        path = PREBUILT / name
        if not path.is_file() or not os.access(path, os.X_OK):
            failures.append(f"missing executable prebuilt asset: {name}")
    for name in PACKAGE_FILES:
        if not (RELEASE_ROOT / name).is_file():
            failures.append(f"missing package file: {name}")

    try:
        actual_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        expected_manifest = _manifest_payload()
        if actual_manifest != expected_manifest:
            failures.append("release manifest does not match packaged bytes")
    except (OSError, json.JSONDecodeError) as error:
        failures.append(f"unreadable release manifest: {error}")
        expected_manifest = {}

    if failures:
        raise ReleaseBuildError("; ".join(failures))
    return expected_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="verify the release without modifying it"
    )
    args = parser.parse_args()
    if not args.check:
        _sync_sources()
        _build_prebuilt()
        _atomic_write_json(MANIFEST, _manifest_payload())
    manifest = _verify()
    print(
        json.dumps(
            {
                "ok": True,
                "mode": "check" if args.check else "build",
                "engine_sha256": manifest["engine_sha256"],
                "managed_files": len(manifest["files"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, ReleaseBuildError) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        raise SystemExit(1)
