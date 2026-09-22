#!/usr/bin/env python3
"""Publish immutable toolbox snapshots. No messaging-service dependencies."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile


def read_json(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError("invalid or oversized protocol file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("unsupported toolbox schema")
    return value


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("symlinks and special files are not allowed")
        if path.is_file():
            digest.update(json.dumps([path.relative_to(root).as_posix(),
                bool(path.stat().st_mode & 0o111), sha256(path)], separators=(",", ":")).encode())
    return digest.hexdigest()


def manifest(root: Path) -> dict:
    value = read_json(root / "toolbox.json")
    for key in ("id", "version"):
        if not isinstance(value.get(key), str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value[key]):
            raise ValueError("invalid toolbox identity")
    entry = value.get("entrypoint")
    if not isinstance(entry, str) or Path(entry).is_absolute() or not entry.endswith(".py"):
        raise ValueError("entrypoint must be a package-relative Python script")
    path = (root / entry).resolve()
    path.relative_to(root.resolve())
    if not path.is_file():
        raise ValueError("missing entrypoint")
    capabilities = value.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities or not all(
        isinstance(item, str) and item.strip() for item in capabilities
    ):
        raise ValueError("declare at least one capability")
    return value


@contextmanager
def locked(registry: Path):
    registry.mkdir(parents=True, exist_ok=True)
    with (registry / ".lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def verify(registry: Path, pointer: dict) -> Path:
    digest = pointer.get("digest", "")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("invalid digest")
    root = registry / "versions" / digest
    if root.is_symlink():
        raise ValueError("version must not be a symlink")
    info = manifest(root)
    if package_digest(root) != digest or any(pointer.get(key) != info[key] for key in ("id", "version")):
        raise ValueError("version integrity check failed")
    return root


def publish(source: Path, registry: Path, activate: bool = False) -> dict:
    source, registry = source.resolve(), registry.expanduser().resolve()
    if source == registry or source in registry.parents or registry in source.parents:
        raise ValueError("package and registry cannot contain one another")
    with locked(registry):
        versions = registry / "versions"
        versions.mkdir(exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=".package-", dir=registry))
        try:
            package_digest(source)  # Reject symlinks before shutil can follow them.
            shutil.copytree(source, temporary, dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"))
            info = manifest(temporary)
            digest = package_digest(temporary)
            pointer = {"schema_version": 1, "digest": digest, "id": info["id"], "version": info["version"]}
            destination = versions / digest
            if destination.exists():
                verify(registry, pointer)
            else:
                os.rename(temporary, destination)
            if activate:
                write_json(registry / "active.json", pointer)
            return pointer
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)


def activate(registry: Path, digest: str) -> dict:
    registry = registry.expanduser().resolve()
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("invalid digest")
    with locked(registry):
        info = manifest(registry / "versions" / digest)
        pointer = {"schema_version": 1, "digest": digest, "id": info["id"], "version": info["version"]}
        verify(registry, pointer)
        write_json(registry / "active.json", pointer)
        return pointer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("publish")
    add.add_argument("source", type=Path)
    add.add_argument("--activate", action="store_true")
    select = commands.add_parser("activate")
    select.add_argument("digest")
    commands.add_parser("status")
    args = parser.parse_args()
    registry = args.registry.expanduser().resolve()
    if args.command == "publish":
        result = publish(args.source, registry, args.activate)
    elif args.command == "activate":
        result = activate(registry, args.digest)
    else:
        result = read_json(registry / "active.json")
        result = {**result, "manifest": manifest(verify(registry, result))}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
