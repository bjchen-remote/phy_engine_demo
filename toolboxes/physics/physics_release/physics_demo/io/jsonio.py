"""Strict JSON parsing, canonical hashing input, and atomic file writes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any


class StrictJSONSemanticError(ValueError):
    """Valid-looking JSON whose meaning is ambiguous or non-standard."""


def _reject_constant(value: str) -> None:
    raise StrictJSONSemanticError(f"non-standard numeric constant {value!r} is not allowed")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise StrictJSONSemanticError(f"duplicate object key {key!r} is not allowed")
        result[key] = value
    return result


def _contains_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _reject_surrogates(root: Any) -> None:
    stack = [root]
    while stack:
        value = stack.pop()
        if isinstance(value, str):
            if _contains_surrogate(value):
                raise StrictJSONSemanticError("lone UTF-16 surrogate code points are not valid UTF-8 JSON text")
        elif isinstance(value, dict):
            for key, child in value.items():
                if _contains_surrogate(key):
                    raise StrictJSONSemanticError("lone UTF-16 surrogate code points are not valid in JSON object keys")
                stack.append(child)
        elif isinstance(value, list):
            stack.extend(value)


def loads(value: str) -> Any:
    parsed = json.loads(value, parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    _reject_surrogates(parsed)
    return parsed


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def write(path: Path, value: Any) -> None:
    payload = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # os.replace replaces a destination symlink itself; it never follows
        # the link to overwrite an unrelated target.
        os.replace(temporary_name, path)
        temporary_name = None
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
