"""Analytic force-field model shared by the reference solver and planner."""

from __future__ import annotations

import math
from typing import Any

from physics_demo.core.math3d import add, cross, dot, mul, norm, sub, unit


def acceleration_bound(field: dict[str, Any]) -> float:
    """Return a conservative magnitude bound for one normalized field."""
    if field["type"] == "uniform":
        return math.sqrt(sum(float(value) ** 2 for value in field["acceleration"]))
    if field["type"] == "vortex":
        return abs(float(field["strength"])) + float(field["inward_strength"])
    return abs(float(field["strength"]))


def maximum_acceleration(force_fields: list[dict[str, Any]]) -> float:
    """Return the largest conservative sum for any targeted entity."""
    by_target: dict[str, float] = {}
    for field in force_fields:
        bound = acceleration_bound(field)
        for target in field["targets"]:
            by_target[target] = by_target.get(target, 0.0) + bound
    return max(by_target.values(), default=0.0)


def evaluate(
    position: list[float],
    target: str,
    time_value: float,
    force_fields: list[dict[str, Any]],
) -> list[float]:
    """Evaluate all active normalized fields for one named entity group."""
    acceleration = [0.0, 0.0, 0.0]
    for field in force_fields:
        if target not in field["targets"] or not (field["start_time"] <= time_value < field["end_time"]):
            continue
        if field["type"] == "uniform":
            acceleration = add(acceleration, field["acceleration"])
            continue
        radial = sub(position, field["center"])
        if field["type"] == "vortex":
            axis = unit(field["axis"], [0.0, 1.0, 0.0])
            radial = sub(radial, mul(axis, dot(radial, axis)))
        distance = norm(radial)
        radius = float(field["radius"])
        if distance <= 1.0e-12 or distance >= radius:
            continue
        direction = mul(radial, 1.0 / distance)
        falloff = 1.0 - distance / radius
        if field["type"] == "radial":
            acceleration = add(acceleration, mul(direction, float(field["strength"]) * falloff))
        else:
            tangent = unit(cross(axis, direction), [1.0, 0.0, 0.0])
            acceleration = add(acceleration, mul(tangent, float(field["strength"]) * falloff))
            acceleration = add(acceleration, mul(direction, -float(field["inward_strength"]) * falloff))
    return acceleration
