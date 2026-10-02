"""Validate the immutable component set inside a QQ-compatible toolbox."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROLES = ("modeling", "simulation", "rendering")
FORMATS = {
    "modeling": ("prepared-scene/1", "pipeline-model/1"),
    "simulation": ("pipeline-model/1", "pipeline-simulation/1"),
    "rendering": ("pipeline-simulation/1", "pipeline-presentation/1"),
}


def supports_model_preview(lock: dict) -> bool:
    modules = lock['modules']
    return (all(modules[role].get('model_preview') is True for role in ('modeling', 'rendering'))
            and (not modules['modeling'].get('display_cleanup')
                 or (modules['modeling'].get('display_cleanup') == 'bounded-floaters/1'
                     and modules['rendering'].get('preview_geometry_scope') == 'render_input')))


def read_json(path: Path, limit: int = 1_000_000) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("invalid or oversized JSON file")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs,
                       parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    json.dumps(value, allow_nan=False)
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def digest_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("symlinks and special files are not module content")
        if path.is_file():
            digest.update(json.dumps([path.relative_to(root).as_posix(),
                bool(path.stat().st_mode & 0o111), sha256(path)], separators=(",", ":")).encode())
    return digest.hexdigest()


def inside(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("path must be relative")
    if ".." in Path(relative).parts:
        raise ValueError("path traversal")
    raw = root / relative
    if any(p.is_symlink() for p in (raw, *raw.parents)):
        raise ValueError("symlinks are not allowed")
    path = raw.resolve()
    path.relative_to(root.resolve())
    return path


def validate_module(root: Path, role: str) -> dict:
    value = read_json(root / "module.json")
    if value.get("schema_version") != "stage-module/1" or value.get("role") != role:
        raise ValueError("module role/protocol mismatch")
    if (value.get("input_schema"), value.get("output_schema")) != FORMATS[role]:
        raise ValueError("incompatible module formats")
    if value.get("engine_contract") != "physics-python/1" or value.get("network") is not False:
        raise ValueError("incompatible engine contract or network capability")
    if not inside(root, value["entrypoint"]).is_file():
        raise ValueError("missing module entrypoint")
    if not isinstance(value.get("id"), str) or not isinstance(value.get("version"), str):
        raise ValueError("module identity missing")
    return value


def verify_bundle(root: Path) -> dict:
    lock = read_json(root / "bundle-lock.json")
    if lock.get("schema_version") != "pipeline-bundle/1" or set(lock.get("modules", {})) != set(ROLES):
        raise ValueError("invalid component lock")
    engine = inside(root, lock["engine"]["path"])
    engine_info = read_json(engine / "toolbox.json")
    if (digest_tree(engine) != lock["engine"]["digest"] or
            engine_info["version"] != lock["engine"]["version"] or
            engine_info["id"] != lock["engine"]["id"]):
        raise ValueError("pinned engine integrity mismatch")
    for role in ROLES:
        pin = lock["modules"][role]
        path = inside(root, pin["path"])
        info = validate_module(path, role)
        if digest_tree(path) != pin["digest"] or any(info[key] != pin[key] for key in ("id", "version")):
            raise ValueError("pinned module integrity mismatch: " + role)
        if (pin.get('model_preview') is True) != (info.get('model_preview') is True):
            raise ValueError('pinned preview capability mismatch: ' + role)
        for feature in ('display_cleanup', 'preview_geometry_scope', 'surface_cleanup'):
            if pin.get(feature) != info.get(feature):
                raise ValueError('pinned preview feature mismatch: ' + role + '/' + feature)
    return lock
