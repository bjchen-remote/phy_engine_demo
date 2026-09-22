"""Small allocation-friendly 3D vector helpers.

Lists are used deliberately: this demo has no third-party dependencies and
updates particle state in place.
"""

from __future__ import annotations

import math
from typing import Any


def add(a: list[float], b: list[float]) -> list[float]:
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def sub(a: list[float], b: list[float]) -> list[float]:
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def mul(a: list[float], scalar: float) -> list[float]:
    return [a[0] * scalar, a[1] * scalar, a[2] * scalar]


def dot(a: list[float], b: list[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def norm_sq(a: list[float]) -> float:
    return dot(a, a)


def norm(a: list[float]) -> float:
    return math.hypot(a[0], a[1], a[2])


def unit(a: list[float], fallback: list[float] | None = None) -> list[float]:
    scale = max(abs(a[0]), abs(a[1]), abs(a[2]))
    if scale <= 1e-12 or not math.isfinite(scale):
        return list(fallback or [1.0, 0.0, 0.0])
    scaled = [a[0] / scale, a[1] / scale, a[2] / scale]
    length = math.hypot(scaled[0], scaled[1], scaled[2])
    if length <= 1e-12 or not math.isfinite(length):
        return list(fallback or [1.0, 0.0, 0.0])
    return mul(scaled, 1.0 / length)


def cross(a: list[float], b: list[float]) -> list[float]:
    return [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]


def finite_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def finite_vec(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 3
        and all(
            finite_number(x)
            for x in value
        )
    )


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value
