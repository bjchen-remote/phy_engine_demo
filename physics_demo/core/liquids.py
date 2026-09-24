"""Canonical liquid presets shared by schema, tools, and rendering metadata."""

from __future__ import annotations

import copy
from typing import Any, Iterable


LIQUID_PRESET_VERSION = 1
LIQUID_PRESETS: dict[str, dict[str, Any]] = {
    "water": {
        "aliases": ["water", "水"],
        "properties": {"viscosity": 0.03, "surface_tension": 0.05},
        "appearance": "clear blue, translucent",
        "scope": "General water-like single-phase liquid.",
        "native_required": False,
    },
    "honey": {
        "aliases": ["honey", "蜂蜜"],
        "properties": {"viscosity": 0.35, "surface_tension": 0.08},
        "appearance": "amber, translucent, depth-darkened",
        "scope": "A stable high-viscosity visual approximation; no temperature-dependent rheology.",
        "native_required": True,
    },
    "glue": {
        "aliases": ["glue", "胶水", "黏胶", "白胶"],
        "properties": {"viscosity": 0.70, "surface_tension": 0.12},
        "appearance": "milky white, mostly opaque",
        "scope": "A viscous Newtonian-like approximation; no curing, strings, adhesion, or viscoelasticity.",
        "native_required": True,
    },
    "molten_lead": {
        "aliases": ["molten lead", "liquid lead", "铅水", "熔融铅"],
        "properties": {"viscosity": 0.05, "surface_tension": 0.18},
        "appearance": "silver grey, opaque metallic",
        "scope": "A molten-lead-looking single-phase flow. Density, heat, freezing, oxidation, and toxicity are not modeled.",
        "native_required": True,
    },
}

LIQUID_PRESET_NAMES = tuple(LIQUID_PRESETS)


def liquid_preset(name: str) -> dict[str, Any]:
    """Return a detached, JSON-ready preset or raise a concise error."""
    if not isinstance(name, str) or name not in LIQUID_PRESETS:
        raise ValueError(f"preset must be one of: {', '.join(LIQUID_PRESET_NAMES)}")
    return {"version": LIQUID_PRESET_VERSION, "name": name, **copy.deepcopy(LIQUID_PRESETS[name])}


def render_material_names(scene: dict[str, Any], particles: Iterable[Any]) -> list[str]:
    """Map physical water samples to their entity's visual liquid preset."""
    by_group = {
        entity.get("id"): entity.get("preset", "water")
        for entity in scene.get("entities", [])
        if isinstance(entity, dict) and entity.get("type") == "fluid"
    }
    return [
        "sand" if particle.material == "sand" else by_group.get(particle.group, "water")
        for particle in particles
    ]


def preserve_render_representatives(
    selected: Iterable[int],
    particle_count: int,
    limit: int,
    *,
    groups: list[str] | None = None,
    visual_materials: list[str] | None = None,
) -> list[int]:
    """Keep a bounded legacy sample while representing groups/materials when possible."""
    if particle_count <= 0 or limit <= 0:
        return []
    limit = min(limit, particle_count)
    if particle_count <= limit:
        return list(range(particle_count))

    preferred: list[int] = []
    seen: set[int] = set()
    for index in selected:
        if 0 <= index < particle_count and index not in seen:
            preferred.append(index)
            seen.add(index)
            if len(preferred) == limit:
                break
    if len(preferred) < limit:
        for index in range(particle_count):
            if index not in seen:
                preferred.append(index)
                seen.add(index)
                if len(preferred) == limit:
                    break

    # A normalized particle group belongs to one visual material, so group
    # coverage is the stronger guarantee.  If all groups cannot fit, preserve
    # each visual material when that smaller set does fit.
    labels: list[str] | None = None
    if groups is not None and len(groups) == particle_count:
        group_count = len({label for label in groups if label})
        if group_count and group_count <= limit:
            labels = groups
    if labels is None and visual_materials is not None and len(visual_materials) == particle_count:
        material_count = len({label for label in visual_materials if label})
        if material_count and material_count <= limit:
            labels = visual_materials
    if labels is None:
        return sorted(preferred)

    members_by_label: dict[str, list[int]] = {}
    for index, label in enumerate(labels):
        if label:
            members_by_label.setdefault(label, []).append(index)
    preferred_set = set(preferred)
    representatives: list[int] = []
    for members in members_by_label.values():
        retained = next((index for index in members if index in preferred_set), None)
        representatives.append(retained if retained is not None else members[len(members) // 2])

    covered = set(representatives)
    for index in preferred:
        if len(covered) >= limit:
            break
        covered.add(index)
    return sorted(covered)
