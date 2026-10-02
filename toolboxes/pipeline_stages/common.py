"""Small, dependency-free helpers for immutable stage artifacts."""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


MAX_JSON_BYTES = 256 * 1024 * 1024


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key: " + key)
        result[key] = value
    return result


def read_json(path: str | Path) -> dict:
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("Stage input must be a bounded regular JSON file")
    def reject_constant(value):
        raise ValueError("Nonfinite JSON value: " + value)
    value = json.loads(path.read_text(encoding="utf-8"),
                       object_pairs_hook=_unique_object, parse_constant=reject_constant)
    if not isinstance(value, dict):
        raise ValueError("Stage input must be a JSON object")
    canonical_bytes(value)  # Also reject overflowed JSON numbers such as 1e999.
    return value


def write_json(path: Path, value: dict) -> None:
    """Publish a new JSON artifact atomically; never replace a previous run."""
    if path.exists() or path.is_symlink():
        raise ValueError("Stage artifacts are immutable: " + str(path))
    data = canonical_bytes(value) + b"\n"
    descriptor, name = tempfile.mkstemp(prefix=".stage-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        # A hard link publishes only if the target is still absent.
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def new_output_directory(path: str | Path) -> Path:
    candidate = Path(path)
    if candidate.is_symlink():
        raise ValueError("Stage output may not be a symbolic link")
    resolved = candidate.resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve():
        raise ValueError("Stage output must be a dedicated run directory")
    if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
        raise ValueError("Stage output must be a new or empty directory")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def check_budget(budget_seconds: float | None, unlimited: bool) -> None:
    if type(unlimited) is not bool:
        raise ValueError("unlimited must be a host-owned boolean")
    if budget_seconds is not None and (
            type(budget_seconds) not in (int, float)
            or not math.isfinite(budget_seconds) or budget_seconds <= 0):
        raise ValueError("budget_seconds must be finite and positive")
    if unlimited and budget_seconds is not None:
        raise ValueError("Unlimited execution cannot also declare a finite budget")


def physics_root() -> Path:
    """Bind this process to the engine selected by the host's immutable pin."""
    root = Path(os.environ.get("PHYSICS_PIPELINE_PHYSICS_ROOT",
                               str(Path(__file__).resolve().parents[1] / "physics"))).resolve()
    package = root / "physics_release" / "physics_demo"
    if not package.is_dir() or not (root / "pcb_thermal.py").is_file():
        raise ValueError("The pinned physics toolbox is unavailable")
    loaded = sys.modules.get("physics_demo")
    if loaded is not None and Path(loaded.__file__).resolve().parent != package:
        raise ValueError("A stage process cannot switch an already imported physics release")
    for path in (root, root / "physics_release"):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)
    return root


def engine_module(name: str):
    root = physics_root()
    module = importlib.import_module(name)
    expected = root if name == "pcb_thermal" else root / "physics_release"
    if not Path(module.__file__).resolve().is_relative_to(expected):
        raise ValueError("Loaded module is outside the pinned physics toolbox")
    return module


def engine_identity() -> dict:
    root = physics_root()
    manifest = read_json(root / "toolbox.json")
    release_manifest = root / "physics_release" / "manifest.json"
    return {"id": manifest["id"], "version": manifest["version"],
            "release_manifest_sha256": file_digest(release_manifest)}


def artifact(path: Path, root: Path, role: str, media_type: str = "application/json") -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Artifact must be a regular file")
    return {"path": path.resolve().relative_to(root.resolve()).as_posix(),
            "role": role, "media_type": media_type,
            "bytes": path.stat().st_size, "sha256": file_digest(path)}


def failure(code: str, message: str, **details) -> dict:
    return {"ok": False, "code": code, "error": message, **details}

