"""Mutable state shared by the reference solver and native particle bridge."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Particle:
    pos: list[float]
    vel: list[float]
    material: str
    group: str
    radius: float
    prev: list[float] = field(default_factory=list)
    anchor: list[float] = field(default_factory=list)
    wetness: float = 0.0
    viscosity: float = 0.03
    surface_tension: float = 0.05
    friction: float = 0.55
    cohesion: float = 0.18


@dataclass
class GravityBody:
    ident: str
    pos: list[float]
    vel: list[float]
    mass: float
    fixed: bool


@dataclass
class RigidBody:
    ident: str
    pos: list[float]
    vel: list[float]
    mass: float
    shape: dict[str, Any]

    @property
    def inv_mass(self) -> float:
        return 0.0 if self.mass <= 0.0 else 1.0 / self.mass
