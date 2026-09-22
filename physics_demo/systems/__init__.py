"""Named physical systems that assemble ordinary scene-v1 configurations.

Factories contain geometry and initial conditions, never an integration loop.
The returned raw scene must pass the usual public prepare/run workflow.
"""
from __future__ import annotations

from types import MappingProxyType

from .pendulum import build_double_pendulum, build_pendulum


SYSTEM_REGISTRY = MappingProxyType({
    "pendulum": build_pendulum,
    "double_pendulum": build_double_pendulum,
})
SYSTEM_TYPES = tuple(SYSTEM_REGISTRY)


def build_system(spec: dict) -> dict:
    """Build a fresh raw scene; reject malformed specs with ``ValueError``.

    ``type`` is required. Other supported fields and units are documented in
    :mod:`physics_demo.systems.pendulum`. The caller's data is never mutated.
    """
    if not isinstance(spec, dict):
        raise ValueError("system spec must be an object")
    if any(not isinstance(key, str) for key in spec):
        raise ValueError("system spec keys must be strings")
    kind = spec.get("type")
    if not isinstance(kind, str) or kind not in SYSTEM_REGISTRY:
        raise ValueError("type must be one of: " + ", ".join(SYSTEM_TYPES))
    return SYSTEM_REGISTRY[kind](spec)


__all__ = ["build_system", "SYSTEM_REGISTRY", "SYSTEM_TYPES"]
