"""Point-mass pendulum scene factories backed by the existing rod solver.

The arrays ``lengths`` (m), ``masses`` (kg), ``angles`` (rad) and
``angular_velocities`` (rad/s) have one entry per link. Angles are absolute
world angles from downward Y, positive toward X; they are not relative joint
angles. The fixed anchor is the origin. Rods are massless and have no collision
geometry. Explicit uniform gravity acts on each bob.

Limits: lengths [1e-6,1000], total length <=400 m, masses [1e-9,1e12],
angles [-2*pi,2*pi], angular velocities [-500,500], resulting bob speeds
<=500 m/s, gravity [1e-9,250], duration [1e-4,30], dt [1e-4,.05], integer
output_fps [1,60], wall_time_s [1,300]. Quality is preview/balanced/high.
Prepare still decides numerical and resource feasibility; these checks do not
certify trajectory accuracy, stability or chaotic behaviour.
"""
from __future__ import annotations

import math

from ..limits import MAX_WALL_TIME_S


_FIELDS = frozenset({
    "type", "lengths", "masses", "angles", "angular_velocities", "gravity",
    "duration", "dt", "output_fps", "quality", "wall_time_s",
})


from .validation import _number


def _array(spec: dict, name: str, defaults: list, lower: float, upper: float) -> list:
    values = spec.get(name, defaults)
    if not isinstance(values, list) or len(values) != len(defaults):
        raise ValueError(f"{name} must be an array of {len(defaults)} numbers")
    return [_number(value, f"{name}[{index}]", lower, upper)
            for index, value in enumerate(values)]


def _build(spec: dict, count: int, expected_type: str) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("system spec must be an object")
    if any(not isinstance(key, str) for key in spec):
        raise ValueError("system spec keys must be strings")
    unknown = set(spec) - _FIELDS
    if unknown:
        raise ValueError("unknown system fields: " + ", ".join(sorted(unknown)))
    if spec.get("type") != expected_type:
        raise ValueError(f"type must be {expected_type!r}")

    lengths = _array(spec, "lengths", [1.0] * count, 1e-6, 1000.0)
    reach = sum(lengths)
    if reach > 400.0:
        raise ValueError("sum(lengths) must be <=400 m to fit world bounds")
    masses = _array(spec, "masses", [1.0] * count, 1e-9, 1e12)
    angles = _array(spec, "angles", [0.6] if count == 1 else [2.0, 2.4],
                    -2.0 * math.pi, 2.0 * math.pi)
    omegas = _array(spec, "angular_velocities", [0.0] * count, -500.0, 500.0)
    gravity = _number(spec.get("gravity", 9.81), "gravity", 1e-9, 250.0)
    duration = _number(spec.get("duration", 8.0), "duration", 1e-4, 30.0)
    dt = _number(spec.get("dt", 0.002), "dt", 1e-4, 0.05)
    fps = _number(spec.get("output_fps", 30), "output_fps", 1, 60)
    if not fps.is_integer():
        raise ValueError("output_fps must be an integer in [1, 60]")
    wall_time = _number(spec.get("wall_time_s", 60.0), "wall_time_s", 1.0, MAX_WALL_TIME_S)
    quality = spec.get("quality", "balanced")
    if not isinstance(quality, str) or quality not in ("preview", "balanced", "high"):
        raise ValueError("quality must be preview, balanced or high")

    entities = [{"id": "anchor", "type": "point_mass", "mass": 1.0,
                 "position": [0.0, 0.0, 0.0], "velocity": [0.0, 0.0, 0.0],
                 "fixed": True}]
    links, queries, targets = [], [], []
    position, velocity = [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
    previous_id = "anchor"
    for index, (length, mass, angle, omega) in enumerate(zip(lengths, masses, angles, omegas), 1):
        bob_id = f"bob{index}"
        sine, cosine = math.sin(angle), math.cos(angle)
        position = [position[0] + length * sine,
                    position[1] - length * cosine, 0.0]
        velocity = [velocity[0] + length * omega * cosine,
                    velocity[1] + length * omega * sine, 0.0]
        if math.hypot(*velocity) > 500.0:
            raise ValueError(f"derived speed for {bob_id} must be <=500 m/s")
        entities.append({"id": bob_id, "type": "point_mass", "mass": mass,
                         "position": position, "velocity": velocity, "fixed": False})
        links.append({"id": f"rod{index}", "type": "rod",
                      "entities": [previous_id, bob_id], "rest_length": length})
        targets.append(bob_id)
        for axis in ("x", "y", "z"):
            queries.append({"id": f"{bob_id}-{axis}", "type": "series",
                            "metric": {"type": "centroid", "entity": bob_id, "axis": axis}})
        queries.append({"id": f"{bob_id}-speed", "type": "series",
                        "metric": {"type": "speed", "entity": bob_id}})
        previous_id = bob_id

    span, depth = max(1.0, 1.2 * reach), max(0.5, 0.1 * reach)
    return {
        "version": 1,
        "name": "Single pendulum" if count == 1 else "Double pendulum",
        "world": {"duration": duration, "dt": dt, "output_fps": int(fps),
                  "gravity": [0.0, 0.0, 0.0],
                  "bounds": {"min": [-span, -span, -depth], "max": [span, span, depth]}},
        "budget": {"wall_time_s": wall_time, "quality": quality, "backend": "native"},
        "entities": entities, "connections": links,
        "force_fields": [{"id": "gravity", "type": "uniform", "targets": targets,
                          "acceleration": [0.0, -gravity, 0.0]}],
        "colliders": [], "interactions": {"mutual_gravity": False},
        "queries": queries,
    }


def build_pendulum(spec: dict) -> dict:
    """Assemble one mass and one rod; return an unnormalized scene."""
    return _build(spec, 1, "pendulum")


def build_double_pendulum(spec: dict) -> dict:
    """Assemble two masses and two rods; return an unnormalized scene."""
    return _build(spec, 2, "double_pendulum")
