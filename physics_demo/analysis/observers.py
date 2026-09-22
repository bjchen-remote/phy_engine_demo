"""Float64 reductions of complete solver state, independent of video sampling.

Observers are deliberately passive: they neither change the integration step
nor retain particle trajectories. Entity membership is resolved once, and only
the requested scalar columns are retained. The native bridge uses the same
metric specifications without per-step Python callbacks.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


def unpack_observations(query_ids: list[str], times: Any, values: Any, count: int) -> dict:
    """Copy completed native samples from a row-major scalar buffer.

    All native solvers share this output contract. Read only ``count`` written
    rows, retain float64 values, and leave optional N-body invariants to callers.
    """
    width = len(query_ids)
    return {
        "version": 1, "source": "solver_macro_steps", "nbody": {},
        "times": [float(times[row]) for row in range(count)],
        "columns": {identifier: [float(values[row * width + column]) for row in range(count)]
                    for column, identifier in enumerate(query_ids)},
    }


@dataclass(frozen=True)
class EntitySpec:
    kind: int  # 0: contiguous particle group, 1: gravity body, 2: rigid body
    start: int
    count: int
    shape: int = 0  # 0: none, 1: sphere, 2: axis-aligned box
    position: tuple[float, ...] = (0.0, 0.0, 0.0)
    velocity: tuple[float, ...] = (0.0, 0.0, 0.0)
    size: tuple[float, ...] = (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class MetricSpec:
    query_id: str
    kind: int  # radius, centroid component, centroid speed, distance, signed gap
    a: EntitySpec
    b: EntitySpec
    axes: tuple[int, int] = (0, 2)
    origin: tuple[float, ...] = (0.0, 0.0, 0.0)


def metric_specs(scene: dict[str, Any], particles: list[Any], bodies: list[Any],
                 rigids: list[Any]) -> list[MetricSpec]:
    if not any(query["type"] != "nbody_stability" for query in scene.get("queries", [])):
        return []
    entities: dict[str, EntitySpec] = {}
    groups: dict[str, list[int]] = {}
    for index, particle in enumerate(particles):
        groups.setdefault(particle.group, []).append(index)
    for identifier, indices in groups.items():
        if indices[-1] - indices[0] + 1 != len(indices):
            raise ValueError("Observer particle groups must be contiguous in solver state.")
        entities[identifier] = EntitySpec(0, indices[0], len(indices))
    for index, body in enumerate(bodies):
        entities[body.ident] = EntitySpec(1, index, 1)
    for index, body in enumerate(rigids):
        shape = body.shape
        shape_kind = 1 if shape["type"] == "sphere" else 2
        size = (float(shape["radius"]), 0.0, 0.0) if shape_kind == 1 else tuple(shape["size"])
        velocity = tuple(body.vel) if body.mass > 0 else (0.0, 0.0, 0.0)
        entities[body.ident] = EntitySpec(2, index, 1, shape_kind,
                                        tuple(body.pos), velocity, size)
    result = []
    kinds = {"spread_radius": 0, "centroid": 1, "speed": 2,
             "center_distance": 3, "surface_gap": 4}
    for query in scene.get("queries", []):
        if query["type"] == "nbody_stability":
            continue
        metric = query["metric"]
        kind = kinds[metric["type"]]
        ids = metric["entities"] if kind >= 3 else [metric["entity"]]
        a, b = entities[ids[0]], entities[ids[-1]]
        axes = tuple("xyz".index(axis) for axis in metric["plane"]) if kind == 0 else (0, 2)
        if kind == 1:
            axes = ("xyz".index(metric["axis"]), 0)
        result.append(MetricSpec(query["id"], kind, a, b, axes,
                                 tuple(float(x) for x in metric.get("origin", [0, 0, 0]))))
    return result


def _entity_state(spec: EntitySpec, particles: list[Any], bodies: list[Any],
                  rigids: list[Any]) -> tuple[list[float], list[float]]:
    if spec.kind == 0:
        selected = particles[spec.start:spec.start + spec.count]
        return ([sum(item.pos[axis] for item in selected) / spec.count for axis in range(3)],
                [sum(item.vel[axis] for item in selected) / spec.count for axis in range(3)])
    item = bodies[spec.start] if spec.kind == 1 else rigids[spec.start]
    if (spec.kind == 1 and item.fixed) or (spec.kind == 2 and item.mass <= 0):
        return item.pos, [0.0, 0.0, 0.0]
    return item.pos, item.vel


def _reduce(spec: MetricSpec, particles: list[Any], bodies: list[Any], rigids: list[Any]) -> float:
    if spec.kind == 0:
        return math.sqrt(max(sum((particle.pos[axis] - spec.origin[axis]) ** 2
                                 for axis in spec.axes)
                             for particle in particles[spec.a.start:spec.a.start + spec.a.count]))
    pa, va = _entity_state(spec.a, particles, bodies, rigids)
    if spec.kind == 1:
        return float(pa[spec.axes[0]])
    if spec.kind == 2:
        return math.sqrt(sum(value * value for value in va))
    pb, _ = _entity_state(spec.b, particles, bodies, rigids)
    delta = [pa[axis] - pb[axis] for axis in range(3)]
    distance = math.sqrt(sum(value * value for value in delta))
    if spec.kind == 3:
        return distance
    if spec.a.shape == spec.b.shape == 1:
        return distance - spec.a.size[0] - spec.b.size[0]
    if spec.a.shape == spec.b.shape == 2:
        return max(abs(delta[axis]) - 0.5 * (spec.a.size[axis] + spec.b.size[axis])
                   for axis in range(3))
    raise ValueError("surface_gap requires two spheres or two axis-aligned boxes.")


def _nbody_snapshot(bodies: list[Any], gravity_constant: float, softening: float) -> tuple[Any, ...]:
    mass = sum(body.mass for body in bodies)
    center = [sum(body.mass * body.pos[axis] for body in bodies) / mass for axis in range(3)]
    radius = max(math.sqrt(sum((body.pos[axis] - center[axis]) ** 2 for axis in range(3)))
                 for body in bodies)
    energy = sum(0.5 * body.mass * sum(value * value for value in body.vel) for body in bodies)
    minimum = math.inf
    for index, body in enumerate(bodies):
        for other in bodies[index + 1:]:
            distance_squared = sum((body.pos[axis] - other.pos[axis]) ** 2 for axis in range(3))
            minimum = min(minimum, math.sqrt(distance_squared))
            energy -= gravity_constant * body.mass * other.mass / math.sqrt(distance_squared + softening * softening)
    momentum = [sum(body.mass * body.vel[axis] for body in bodies) for axis in range(3)]
    return radius, minimum, energy, momentum


class Observer:
    """Passive macro-step collector; callers must capture t=0 explicitly."""

    def __init__(self, scene: dict[str, Any], particles: list[Any], bodies: list[Any],
                 rigids: list[Any]) -> None:
        self.specs = metric_specs(scene, particles, bodies, rigids)
        self.nbody_ids = [query["id"] for query in scene.get("queries", [])
                          if query["type"] == "nbody_stability"]
        interactions = scene["interactions"]
        self.gravity_constant = float(interactions["gravity_G"]) if interactions["mutual_gravity"] else 0.0
        self.softening = float(interactions["softening"])
        self.result: dict[str, Any] = {
            "version": 1, "source": "solver_macro_steps", "times": [],
            "columns": {spec.query_id: [] for spec in self.specs},
            "nbody": {identifier: {"max_com_radius": [], "min_pair_distance": [],
                                    "energy": [], "momentum": []}
                      for identifier in self.nbody_ids},
        }

    def capture(self, time_value: float, particles: list[Any], bodies: list[Any],
                rigids: list[Any]) -> None:
        if not self.specs and not self.nbody_ids:
            return
        self.result["times"].append(float(time_value))
        for spec in self.specs:
            self.result["columns"][spec.query_id].append(_reduce(spec, particles, bodies, rigids))
        if self.nbody_ids:
            radius, minimum, energy, momentum = _nbody_snapshot(bodies, self.gravity_constant, self.softening)
            for identifier in self.nbody_ids:
                columns = self.result["nbody"][identifier]
                columns["max_com_radius"].append(radius)
                columns["min_pair_distance"].append(minimum)
                columns["energy"].append(energy)
                columns["momentum"].append(list(momentum))
