"""Validation of the intentionally narrow, quantitative common-x-rail model."""
from __future__ import annotations

from typing import Any

from physics_demo.limits import MAX_ABS_COORDINATE, MAX_ACCELERATION, MAX_SHAPE_EXTENT
from physics_demo.core.math3d import finite_number, finite_vec


def normalize_slider(entity: dict[str, Any], path: str, errors: list[dict[str, Any]]) -> None:
    def issue(key: str, message: str) -> None:
        errors.append({"code": "slider_parameter", "path": path + "." + key, "message": message})

    for key, default in (("position", [0.0, 0.5, 0.0]), ("size", [1.0, 1.0, 1.0])):
        entity.setdefault(key, default)
        if not finite_vec(entity[key]):
            issue(key, "Expected three finite numbers.")
            entity[key] = default
        else:
            entity[key] = [float(x) for x in entity[key]]
    if any(abs(x) > MAX_ABS_COORDINATE for x in entity["position"]):
        issue("position", f"Coordinates must stay within ±{MAX_ABS_COORDINATE:g} m.")
    if any(x <= 0 or x > MAX_SHAPE_EXTENT for x in entity["size"]):
        issue("size", f"Each extent must lie in (0, {MAX_SHAPE_EXTENT:g}] m.")
    for key, default, low, high in (("mass", 1.0, 1e-12, 1e12),
                                   ("acceleration", 0.0, -MAX_ACCELERATION, MAX_ACCELERATION),
                                   ("friction", 0.0, 0.0, 5.0), ("restitution", 0.0, 0.0, 1.0)):
        entity.setdefault(key, default)
        if not finite_number(entity[key]) or not low <= entity[key] <= high:
            issue(key, f"{key} must be a finite number in [{low:g}, {high:g}].")
            entity[key] = default
        else:
            entity[key] = float(entity[key])
    if entity["velocity"][1:] != [0.0, 0.0]:
        issue("velocity", "Rail sliders allow only x velocity; y and z must be zero.")


def validate_slider_scene(scene: dict[str, Any]) -> list[dict[str, Any]]:
    sliders = [e for e in scene["entities"] if e["type"] == "slider"]
    if not sliders:
        return []
    errors = []

    def issue(path: str, message: str) -> None:
        errors.append({"code": "slider_model_boundary", "path": path, "message": message})

    if len(sliders) != len(scene["entities"]) or len(sliders) > 16:
        issue("entities", "A slider scene contains only 1–16 sliders on one common x rail.")
    if scene["colliders"] or scene["force_fields"] or scene["interactions"]["mutual_gravity"]:
        issue("entities", "Rail sliders do not support colliders, force fields, or mutual gravity; use per-slider acceleration.")
    if scene["world"]["gravity"][0] != 0 or scene["world"]["gravity"][2] != 0:
        issue("world.gravity", "Only y gravity sets the rail normal load; prescribe x drive using slider.acceleration.")
    if scene["budget"]["backend"] == "native":
        issue("budget.backend", "The analytic slider backend requires auto or python; it is not a native rigid-body solver.")
    reference = sliders[0]
    for entity in sliders:
        if entity["position"][1:] != reference["position"][1:] or entity["size"][1:] != reference["size"][1:]:
            issue("entities", "Slider centres y/z and cross-section sizes y/z must match the common rail.")
    ordered = sorted(sliders, key=lambda e: e["position"][0])
    for left, right in zip(ordered, ordered[1:]):
        gap = right["position"][0] - left["position"][0] - 0.5 * (left["size"][0] + right["size"][0])
        if gap < -1e-10:
            issue("entities", f"Sliders {left['id']!r} and {right['id']!r} initially overlap along x.")
    return errors
