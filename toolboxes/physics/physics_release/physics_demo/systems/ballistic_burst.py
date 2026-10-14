"""Finite liquid parcels launched once; the existing solver owns all motion."""
from __future__ import annotations

import math

from .validation import _number
from ..core.liquids import LIQUID_PRESET_NAMES
from ..limits import MAX_PHYSICAL_DURATION_S, MAX_WALL_TIME_S

BURST_SCOPE = (
    "Finite initially launched liquid parcels under constant downward gravity. "
    "Apex height and range describe ideal free-flight parcel centres before contact; "
    "DFSPH deformation and collisions can change individual paths. No continuous "
    "emitter, gas pressure, heat, cooling, or calibrated eruption prediction."
)
_FIELDS = frozenset({"type", "source_height", "source_radius", "apex_height",
    "spread_radius", "volume", "parcel_count", "spacing", "preset", "gravity",
    "duration", "dt", "output_fps", "quality", "validation", "wall_time_s"})


def build_ballistic_burst(spec: dict) -> dict:
    unknown = set(spec) - _FIELDS
    if unknown:
        raise ValueError("unknown system fields: " + ", ".join(sorted(unknown)))
    def number(key, default, low, high):
        return _number(spec.get(key, default), key, low, high)
    source_y = number("source_height", 0.9, 0.001, 100)
    source_radius = number("source_radius", 0.35, 0, 100)
    apex = number("apex_height", 0.9, 0.001, 100)
    spread = number("spread_radius", 1.2, 0, 100)
    volume = number("volume", 0.05, 1e-9, 100)
    count_value = number("parcel_count", 8, 1, 24)
    fps = number("output_fps", 24, 1, 60)
    if not count_value.is_integer() or not fps.is_integer():
        raise ValueError("parcel_count and output_fps must be integers")
    count = int(count_value)
    spacing = number("spacing", 0.03, 0.0001, 0.5)
    gravity = number("gravity", 9.81, 1e-9, 250)
    duration = number("duration", 3.2, 0.0001, MAX_PHYSICAL_DURATION_S)
    dt = number("dt", 0.002, 0.0001, 0.05)
    wall = number("wall_time_s", 60, 1, MAX_WALL_TIME_S)
    quality, validation, preset = (spec.get("quality", "balanced"),
        spec.get("validation", "visual"), spec.get("preset", "water"))
    if quality not in ("preview", "balanced", "high"):
        raise ValueError("quality must be preview, balanced or high")
    if validation not in ("visual", "strict"):
        raise ValueError("validation must be visual or strict")
    if not isinstance(preset, str) or preset not in LIQUID_PRESET_NAMES:
        raise ValueError("preset must be one of: " + ", ".join(LIQUID_PRESET_NAMES))
    radius = (3 * volume / (4 * math.pi * count)) ** (1 / 3)
    if radius < spacing:
        raise ValueError("spacing must resolve each parcel radius; increase volume or reduce spacing/count")
    if source_y <= radius + spacing:
        raise ValueError("source_height must clear the ground by parcel radius plus spacing")
    # Do not silently change volume or move overlapping launch parcels apart.
    if count > 1 and 2 * source_radius * math.sin(math.pi / count) < 2 * radius + spacing:
        raise ValueError("source_radius is too small for non-overlapping parcels; increase it or reduce volume/count")
    if count == 1 and source_radius != 0:
        raise ValueError("one parcel uses source_radius=0")
    entities = []
    reach = source_radius + radius
    margin = max(4 * spacing, radius)
    for index in range(count):
        angle = 2 * math.pi * index / count
        # Alternating heights create a finite spread, not a continued force.
        height = apex * (1.0 if index % 2 == 0 else 0.75)
        vy = math.sqrt(2 * gravity * height)
        landing_time = (vy + math.sqrt(vy * vy + 2 * gravity * source_y)) / gravity
        horizontal_speed = spread / landing_time
        vx, vz = horizontal_speed * math.cos(angle), horizontal_speed * math.sin(angle)
        if math.hypot(vx, vy, vz) > 500:
            raise ValueError("derived launch speed exceeds 500 m/s")
        # Leave room for impact redirecting downward kinetic energy sideways.
        impact_speed = math.sqrt(horizontal_speed**2 + vy**2 + 2*gravity*(source_y+radius))
        reach = max(reach, source_radius + impact_speed * duration + radius)
        entities.append({"id": f"parcel-{index+1}", "type": "fluid", "preset": preset,
            "shape": {"type": "sphere", "center": [source_radius*math.cos(angle), source_y,
                source_radius*math.sin(angle)], "radius": radius},
            "spacing": spacing, "velocity": [vx, vy, vz]})
    reach += margin
    return {"version": 1, "name": "Ballistic liquid burst",
        "world": {"gravity": [0, -gravity, 0], "duration": duration, "dt": dt,
            "output_fps": int(fps), "bounds": {"min": [-reach, -margin, -reach],
                "max": [reach, source_y+apex+radius+margin, reach]}},
        "budget": {"quality": quality, "validation": validation, "backend": "native", "wall_time_s": wall},
        "entities": entities, "force_fields": [], "interactions": {"mutual_gravity": False},
        "colliders": [{"id": "ground", "type": "plane", "normal": [0, 1, 0], "offset": 0, "friction": 0.5}]}
