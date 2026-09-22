"""Deterministic, dependency-free solvers used by the demo.

The particle path targets fast previews.  It combines PBF-style liquid
density constraints, PBD particle contacts, simple colliders, and a deliberately
limited wet-sand erosion proxy.  The point-mass path uses velocity Verlet and is
kept numerically separate so it can report conservation diagnostics.
"""

from __future__ import annotations

import math
import time
from typing import Any

from physics_demo.core.colliders import project_box, project_colliders
from physics_demo.core.force_fields import evaluate as field_acceleration
from physics_demo.limits import (
    NBODY_MAX_RELATIVE_ENERGY_DRIFT,
    NBODY_MOMENTUM_ABSOLUTE_FLOOR,
    NBODY_MOMENTUM_MASS_SCALED_FLOOR,
    NBODY_MOMENTUM_RELATIVE_TOLERANCE,
)
from physics_demo.core.math3d import add, clamp, mul, norm, norm_sq, sub, unit
from physics_demo.core.liquids import preserve_render_representatives, render_material_names
from physics_demo.core.state import GravityBody, Particle, RigidBody
from physics_demo.analysis.observers import Observer


class _SimulationDeadlineExceeded(RuntimeError):
    """Internal control flow used to stop the reference solver promptly."""


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise _SimulationDeadlineExceeded


def _next_force_boundary(
    current_time: float,
    nominal_end: float,
    force_fields: list[dict[str, Any]],
    requested_dt: float,
) -> float:
    """Return the next force activation/deactivation event in this step.

    Splitting at these events makes a timed field act for exactly the overlap
    of its half-open window with the requested timestep.  The small tolerance
    prevents a rounded event time from creating a zero-length substep.
    """

    result = nominal_end
    tolerance = max(1.0e-12, requested_dt * 1.0e-5)
    for force_field in force_fields:
        for boundary in (float(force_field["start_time"]), float(force_field["end_time"])):
            if current_time + tolerance < boundary < result:
                result = boundary
    return result


def _axis_values(center: float, extent: float, spacing: float) -> list[float]:
    count = max(1, int(math.floor(extent / spacing)))
    start = center - 0.5 * spacing * (count - 1)
    return [start + index * spacing for index in range(count)]


def sample_shape(shape: dict[str, Any], spacing: float) -> list[list[float]]:
    center = [float(x) for x in shape["center"]]
    if shape["type"] == "box":
        size = [float(x) for x in shape["size"]]
        return [
            [x, y, z]
            for x in _axis_values(center[0], size[0], spacing)
            for y in _axis_values(center[1], size[1], spacing)
            for z in _axis_values(center[2], size[2], spacing)
        ]
    radius = float(shape["radius"])
    values = [_axis_values(center[axis], 2.0 * radius, spacing) for axis in range(3)]
    points = []
    limit = max(0.0, radius - 0.18 * spacing) ** 2
    for x in values[0]:
        for y in values[1]:
            for z in values[2]:
                if (x - center[0]) ** 2 + (y - center[1]) ** 2 + (z - center[2]) ** 2 <= limit:
                    points.append([x, y, z])
    return points or [center]


def _cell(pos: list[float], size: float) -> tuple[int, int, int]:
    return (math.floor(pos[0] / size), math.floor(pos[1] / size), math.floor(pos[2] / size))


def build_neighbors(
    particles: list[Particle],
    h: float,
    deadline: float | None = None,
) -> list[list[int]]:
    grid: dict[tuple[int, int, int], list[int]] = {}
    for index, particle in enumerate(particles):
        if (index & 255) == 0:
            _check_deadline(deadline)
        grid.setdefault(_cell(particle.pos, h), []).append(index)
    neighbors: list[list[int]] = [[] for _ in particles]
    h2 = h * h
    candidates_seen = 0
    for index, particle in enumerate(particles):
        if (index & 31) == 0:
            _check_deadline(deadline)
        cx, cy, cz = _cell(particle.pos, h)
        out = neighbors[index]
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for other in grid.get((cx + dx, cy + dy, cz + dz), []):
                        candidates_seen += 1
                        if (candidates_seen & 4095) == 0:
                            _check_deadline(deadline)
                        if other != index and norm_sq(sub(particle.pos, particles[other].pos)) < h2:
                            out.append(other)
    return neighbors


def _poly6(r2: float, h: float) -> float:
    if r2 >= h * h:
        return 0.0
    return 315.0 * (h * h - r2) ** 3 / (64.0 * math.pi * h**9)


def _spiky_grad(delta: list[float], h: float) -> list[float]:
    length = norm(delta)
    if length <= 1e-12 or length >= h:
        return [0.0, 0.0, 0.0]
    scale = -45.0 * (h - length) ** 2 / (math.pi * h**6 * length)
    return mul(delta, scale)


def _gravity_accelerations(
    bodies: list[GravityBody],
    gravitational_constant: float,
    softening: float,
    force_fields: list[dict[str, Any]],
    time_value: float,
) -> list[list[float]]:
    accelerations = [[0.0, 0.0, 0.0] for _ in bodies]
    for index, body in enumerate(bodies):
        if not body.fixed:
            accelerations[index] = field_acceleration(body.pos, body.ident, time_value, force_fields)
    eps2 = softening * softening
    for i in range(len(bodies)):
        for j in range(i + 1, len(bodies)):
            delta = sub(bodies[j].pos, bodies[i].pos)
            distance2 = norm_sq(delta) + eps2
            inv_distance3 = 1.0 / (distance2 * math.sqrt(distance2))
            direction = mul(delta, gravitational_constant * inv_distance3)
            ai = mul(direction, bodies[j].mass)
            aj = mul(direction, -bodies[i].mass)
            if not bodies[i].fixed:
                accelerations[i] = add(accelerations[i], ai)
            if not bodies[j].fixed:
                accelerations[j] = add(accelerations[j], aj)
    return accelerations


def _gravity_verlet(
    bodies: list[GravityBody],
    dt: float,
    gravitational_constant: float,
    softening: float,
    force_fields: list[dict[str, Any]],
    time_value: float,
) -> None:
    field_sample_time = time_value + 0.5 * dt
    old_accel = _gravity_accelerations(
        bodies, gravitational_constant, softening, force_fields, field_sample_time
    )
    for body, accel in zip(bodies, old_accel):
        if body.fixed:
            continue
        body.vel = add(body.vel, mul(accel, 0.5 * dt))
        body.pos = add(body.pos, mul(body.vel, dt))
    new_accel = _gravity_accelerations(
        bodies, gravitational_constant, softening, force_fields, field_sample_time
    )
    for body, accel in zip(bodies, new_accel):
        if not body.fixed:
            body.vel = add(body.vel, mul(accel, 0.5 * dt))


def _step_gravity(bodies, dt, gravitational_constant, softening, force_fields, time_value, deadline=math.inf):
    """Subcycle close approaches while preserving the outer observation clock."""
    elapsed = 0.0
    while elapsed < dt:
        _check_deadline(deadline)
        remaining = dt - elapsed
        step = remaining
        if gravitational_constant > 0:
            for i, first in enumerate(bodies):
                for second in bodies[i + 1:]:
                    if first.fixed and second.fixed:
                        continue
                    x, y, z = sub(second.pos, first.pos)
                    r2 = x*x + y*y + z*z + softening*softening
                    vx, vy, vz = sub(second.vel, first.vel)
                    mu = gravitational_constant * (first.mass + second.mass)
                    step = min(step, .005 * math.sqrt(r2 * math.sqrt(r2) / mu))
                    speed2 = vx*vx + vy*vy + vz*vz
                    if speed2 > 0:
                        step = min(step, .005 * math.sqrt(r2 / speed2))
        if not step > 0 or elapsed + step == elapsed:
            raise ValueError("gravity timestep is below floating-point resolution")
        _gravity_verlet(bodies, step, gravitational_constant, softening, force_fields, time_value + elapsed)
        elapsed = dt if step == remaining else elapsed + step


def gravity_invariants(bodies: list[GravityBody], gravitational_constant: float, softening: float) -> dict[str, Any]:
    kinetic = sum(0.5 * body.mass * norm_sq(body.vel) for body in bodies)
    potential = 0.0
    momentum = [0.0, 0.0, 0.0]
    weighted_position = [0.0, 0.0, 0.0]
    total_mass = sum(body.mass for body in bodies)
    for i, body in enumerate(bodies):
        momentum = add(momentum, mul(body.vel, body.mass))
        weighted_position = add(weighted_position, mul(body.pos, body.mass))
        for j in range(i + 1, len(bodies)):
            distance = math.sqrt(norm_sq(sub(bodies[j].pos, body.pos)) + softening**2)
            potential -= gravitational_constant * body.mass * bodies[j].mass / distance
    return {
        "energy": kinetic + potential,
        "momentum": momentum,
        "center_of_mass": mul(weighted_position, 1.0 / total_mass) if total_mass else [0.0, 0.0, 0.0],
    }


class NBodyDiagnostics:
    """Snapshot initial invariants before either solver mutates its bodies."""

    def __init__(self, scene: dict[str, Any], bodies: list[GravityBody]):
        interactions = scene["interactions"]
        self.gravity_G = float(interactions["gravity_G"]) if interactions["mutual_gravity"] else 0.0
        self.softening = float(interactions["softening"])
        self.initial = gravity_invariants(bodies, self.gravity_G, self.softening) if bodies else None
        self.scalar_momentum = sum(body.mass * norm(body.vel) for body in bodies)
        self.total_mass = sum(body.mass for body in bodies)
        forced = any(body.ident in field["targets"] for body in bodies
                     for field in scene.get("force_fields", []))
        self.exclusion_reason = (
            "external_force_field" if forced else
            "fixed_body_breaks_closed_system_momentum" if any(body.fixed for body in bodies) else None
        )

    def finish(self, bodies: list[GravityBody]) -> dict[str, Any]:
        final = gravity_invariants(bodies, self.gravity_G, self.softening) if bodies else None
        applicable = bool(bodies) and self.exclusion_reason is None
        energy_drift = momentum_drift = tolerance = None
        conserved = False
        if applicable and self.initial and final:
            denominator = max(abs(float(self.initial["energy"])), 1e-12)
            energy_drift = (float(final["energy"]) - float(self.initial["energy"])) / denominator
            momentum_drift = norm(sub(final["momentum"], self.initial["momentum"]))
            tolerance = max(
                NBODY_MOMENTUM_RELATIVE_TOLERANCE * self.scalar_momentum,
                NBODY_MOMENTUM_MASS_SCALED_FLOOR * self.total_mass,
                NBODY_MOMENTUM_ABSOLUTE_FLOOR,
            )
            conserved = (
                math.isfinite(energy_drift) and math.isfinite(momentum_drift)
                and abs(energy_drift) <= NBODY_MAX_RELATIVE_ENERGY_DRIFT
                and momentum_drift <= tolerance
            )
        return {
            "nbody_relative_energy_drift": energy_drift,
            "nbody_momentum_drift": momentum_drift,
            "nbody_momentum_tolerance": tolerance,
            "nbody_invariants_conserved": conserved,
            "nbody_invariants_applicable": applicable,
            "nbody_invariant_exclusion_reason": self.exclusion_reason,
        }


def _project_world(pos: list[float], radius: float, bounds: dict[str, list[float]]) -> float:
    maximum = 0.0
    for axis in range(3):
        lower = bounds["min"][axis] + radius
        upper = bounds["max"][axis] - radius
        before = pos[axis]
        pos[axis] = clamp(pos[axis], lower, upper)
        maximum = max(maximum, abs(before - pos[axis]))
    return maximum


def _project_rigids(particle: Particle, rigids: list[RigidBody]) -> float:
    maximum = 0.0
    for rigid in rigids:
        if rigid.shape["type"] == "box":
            collider = {"type": "box", "center": rigid.pos, "size": rigid.shape["size"]}
            maximum = max(maximum, project_box(particle.pos, particle.radius, collider))
            continue
        delta = sub(particle.pos, rigid.pos)
        distance = norm(delta)
        minimum = particle.radius + float(rigid.shape["radius"])
        if distance >= minimum:
            continue
        normal = unit(delta, [0.0, 1.0, 0.0])
        penetration = minimum - distance
        particle_weight = 1.0 / (1.0 + rigid.inv_mass)
        rigid_weight = 1.0 - particle_weight
        correction = mul(normal, penetration)
        particle.pos = add(particle.pos, mul(correction, particle_weight))
        if rigid.inv_mass > 0.0:
            rigid.pos = sub(rigid.pos, mul(correction, rigid_weight))
        maximum = max(maximum, penetration)
    return maximum


def _step_rigids(rigids: list[RigidBody], gravity: list[float], dt: float, colliders: list[dict[str, Any]], bounds: dict[str, Any]) -> float:
    maximum = 0.0
    for rigid in rigids:
        if rigid.inv_mass <= 0.0:
            continue
        old = list(rigid.pos)
        rigid.vel = add(rigid.vel, mul(gravity, dt))
        rigid.pos = add(rigid.pos, mul(rigid.vel, dt))
        radius = float(rigid.shape.get("radius", 0.5))
        maximum = max(maximum, project_colliders(rigid.pos, radius, colliders))
        maximum = max(maximum, _project_world(rigid.pos, radius, bounds))
        rigid.vel = mul(sub(rigid.pos, old), 1.0 / dt)
    return maximum


def _density_constraints(
    particles: list[Particle],
    neighbors: list[list[int]],
    h: float,
    spacing: float,
    deadline: float | None = None,
) -> tuple[list[list[float]], float]:
    rest_density = 1000.0
    mass = rest_density * spacing**3
    lambdas = [0.0 for _ in particles]
    density_error = 0.0
    water_indices = [index for index, particle in enumerate(particles) if particle.material == "water"]
    self_density = mass * _poly6(0.0, h)
    neighbor_visits = 0
    for water_offset, index in enumerate(water_indices):
        if (water_offset & 31) == 0:
            _check_deadline(deadline)
        particle = particles[index]
        density = self_density
        gradient_i = [0.0, 0.0, 0.0]
        sum_gradient_sq = 0.0
        for other in neighbors[index]:
            neighbor_visits += 1
            if (neighbor_visits & 4095) == 0:
                _check_deadline(deadline)
            if particles[other].material != "water":
                continue
            delta = sub(particle.pos, particles[other].pos)
            density += mass * _poly6(norm_sq(delta), h)
            gradient_j = mul(_spiky_grad(delta, h), -mass / rest_density)
            gradient_i = sub(gradient_i, gradient_j)
            sum_gradient_sq += norm_sq(gradient_j)
        constraint = max(density / rest_density - 1.0, 0.0)
        density_error += constraint
        denominator = sum_gradient_sq + norm_sq(gradient_i) + 80.0
        lambdas[index] = -constraint / denominator

    deltas = [[0.0, 0.0, 0.0] for _ in particles]
    reference_kernel = max(_poly6((0.3 * h) ** 2, h), 1e-12)
    for water_offset, index in enumerate(water_indices):
        if (water_offset & 31) == 0:
            _check_deadline(deadline)
        particle = particles[index]
        correction = [0.0, 0.0, 0.0]
        for other in neighbors[index]:
            neighbor_visits += 1
            if (neighbor_visits & 4095) == 0:
                _check_deadline(deadline)
            if particles[other].material != "water":
                continue
            delta = sub(particle.pos, particles[other].pos)
            kernel_ratio = _poly6(norm_sq(delta), h) / reference_kernel
            artificial_pressure = -0.0008 * kernel_ratio**4
            coefficient = (lambdas[index] + lambdas[other] + artificial_pressure) * mass / rest_density
            correction = add(correction, mul(_spiky_grad(delta, h), coefficient))
        deltas[index] = correction
    mean_error = density_error / max(len(water_indices), 1)
    return deltas, mean_error


def _project_particle_contacts(
    particles: list[Particle],
    neighbors: list[list[int]],
    wetting_rate: float,
    dt: float,
    deadline: float | None = None,
) -> None:
    touched_sand: set[int] = set()
    neighbor_visits = 0
    for i, particle in enumerate(particles):
        if (i & 31) == 0:
            _check_deadline(deadline)
        for j in neighbors[i]:
            neighbor_visits += 1
            if (neighbor_visits & 4095) == 0:
                _check_deadline(deadline)
            if j <= i:
                continue
            other = particles[j]
            pair = {particle.material, other.material}
            if pair == {"water"}:
                continue
            delta = sub(particle.pos, other.pos)
            distance = norm(delta)
            minimum = particle.radius + other.radius
            if distance >= minimum:
                continue
            normal = unit(delta, [1.0, 0.0, 0.0])
            correction = mul(normal, 0.5 * (minimum - distance))
            particle.pos = add(particle.pos, correction)
            other.pos = sub(other.pos, correction)
            if pair == {"water", "sand"}:
                touched_sand.add(i if particle.material == "sand" else j)
    for index in touched_sand:
        particles[index].wetness = clamp(particles[index].wetness + wetting_rate * dt, 0.0, 1.0)


def _apply_sand_cohesion_velocity(particle: Particle, dt: float) -> None:
    """Apply one stable implicit step of the qualitative sand anchor spring.

    The old position projection fed its correction back through velocity
    reconstruction, making the apparent material hundreds of times stiffer
    when ``dt`` changed.  A critically damped implicit spring keeps the same
    material scale across timesteps and remains stable at the demo's largest
    allowed step.  Wetness weakens the spring to create the erosion proxy.
    """

    if particle.material != "sand":
        return
    strength = clamp(particle.cohesion * (1.0 - particle.wetness), 0.0, 0.35)
    if strength <= 0.0:
        return
    omega = 50.0 * math.sqrt(strength)
    denominator = 1.0 + 2.0 * omega * dt + omega * omega * dt * dt
    spring_scale = dt * omega * omega
    for axis in range(3):
        displacement = particle.pos[axis] - particle.anchor[axis]
        particle.vel[axis] = (particle.vel[axis] - spring_scale * displacement) / denominator


def _velocity_postprocess(
    particles: list[Particle],
    neighbors: list[list[int]],
    h: float,
    spacing: float,
    drag: float,
    dt: float,
    deadline: float | None = None,
) -> None:
    rest_density = 1000.0
    mass = rest_density * spacing**3
    updates = [[0.0, 0.0, 0.0] for _ in particles]
    neighbor_visits = 0
    for i, particle in enumerate(particles):
        if (i & 31) == 0:
            _check_deadline(deadline)
        for j in neighbors[i]:
            neighbor_visits += 1
            if (neighbor_visits & 4095) == 0:
                _check_deadline(deadline)
            other = particles[j]
            weight = mass * _poly6(norm_sq(sub(particle.pos, other.pos)), h) / rest_density
            if particle.material == other.material == "water":
                updates[i] = add(updates[i], mul(sub(other.vel, particle.vel), particle.viscosity * weight))
            elif {particle.material, other.material} == {"water", "sand"}:
                updates[i] = add(updates[i], mul(sub(other.vel, particle.vel), drag * min(weight * 8.0, 0.08)))
    for index, (particle, update) in enumerate(zip(particles, updates)):
        if (index & 255) == 0:
            _check_deadline(deadline)
        particle.vel = add(particle.vel, update)
        if particle.material == "water":
            damping = math.exp(math.log(0.995) * dt / (1.0 / 90.0))
            particle.vel = mul(particle.vel, damping)
        if particle.material == "sand":
            reference = clamp(1.0 - 0.06 * particle.friction, 1.0e-6, 1.0)
            horizontal = math.exp(math.log(reference) * dt / (1.0 / 90.0))
            particle.vel[0] *= horizontal
            particle.vel[2] *= horizontal


def _step_particles(
    particles: list[Particle],
    rigids: list[RigidBody],
    gravity: list[float],
    dt: float,
    spacing: float,
    iterations: int,
    colliders: list[dict[str, Any]],
    bounds: dict[str, Any],
    interactions: dict[str, Any],
    force_fields: list[dict[str, Any]],
    time_value: float,
    deadline: float | None = None,
) -> tuple[float, float]:
    if not particles:
        return 0.0, 0.0
    h = 2.0 * spacing
    for index, particle in enumerate(particles):
        if (index & 255) == 0:
            _check_deadline(deadline)
        particle.prev = list(particle.pos)
        acceleration = add(gravity, field_acceleration(particle.pos, particle.group, time_value + 0.5 * dt, force_fields))
        if particle.material == "water":
            for collider in colliders:
                adhesion = float(collider.get("water_adhesion", 0.0))
                if collider["type"] != "plane" or adhesion <= 0.0:
                    continue
                normal = unit(collider["normal"], [0.0, 1.0, 0.0])
                signed_distance = sum(normal[axis] * particle.pos[axis] for axis in range(3)) - collider["offset"]
                surface_gap = signed_distance - particle.radius
                if surface_gap >= h:
                    continue
                proximity = 1.0 - clamp(surface_gap / h, 0.0, 1.0)
                profile = proximity * proximity * (3.0 - 2.0 * proximity)
                acceleration = sub(acceleration, mul(normal, adhesion * profile))
        particle.vel = add(particle.vel, mul(acceleration, dt))
        _apply_sand_cohesion_velocity(particle, dt)
        particle.pos = add(particle.pos, mul(particle.vel, dt))

    maximum_penetration = 0.0
    density_error = 0.0
    neighbors: list[list[int]] = [[] for _ in particles]
    for _ in range(iterations):
        _check_deadline(deadline)
        neighbors = build_neighbors(particles, h, deadline)
        deltas, density_error = _density_constraints(particles, neighbors, h, spacing, deadline)
        for index, (particle, delta) in enumerate(zip(particles, deltas)):
            if (index & 255) == 0:
                _check_deadline(deadline)
            particle.pos = add(particle.pos, delta)
        _project_particle_contacts(
            particles,
            neighbors,
            float(interactions["wetting_rate"]),
            dt / iterations,
            deadline,
        )
        for index, particle in enumerate(particles):
            if (index & 31) == 0:
                _check_deadline(deadline)
            maximum_penetration = max(
                maximum_penetration,
                project_colliders(particle.pos, particle.radius, colliders, particle.prev),
            )
            maximum_penetration = max(maximum_penetration, _project_rigids(particle, rigids))
            maximum_penetration = max(maximum_penetration, _project_world(particle.pos, particle.radius, bounds))

    for index, particle in enumerate(particles):
        if (index & 255) == 0:
            _check_deadline(deadline)
        particle.vel = mul(sub(particle.pos, particle.prev), 1.0 / dt)
    _velocity_postprocess(
        particles,
        neighbors,
        h,
        spacing,
        float(interactions["water_sand_drag"]),
        dt,
        deadline,
    )
    return maximum_penetration, density_error


def _make_state(scene: dict[str, Any], spacing: float) -> tuple[list[Particle], list[GravityBody], list[RigidBody]]:
    particles: list[Particle] = []
    gravity_bodies: list[GravityBody] = []
    rigids: list[RigidBody] = []
    radius = 0.46 * spacing
    for entity in scene["entities"]:
        kind = entity["type"]
        if kind == "point_mass":
            gravity_bodies.append(GravityBody(entity["id"], list(entity["position"]), list(entity["velocity"]), entity["mass"], entity["fixed"]))
        elif kind in {"fluid", "granular"}:
            material = "water" if kind == "fluid" else "sand"
            properties = entity["properties"]
            for point in sample_shape(entity["shape"], spacing):
                particles.append(Particle(
                    pos=point,
                    vel=list(entity["velocity"]),
                    material=material,
                    group=entity["id"],
                    radius=radius,
                    anchor=list(point),
                    viscosity=float(properties.get("viscosity", 0.03)),
                    surface_tension=float(properties.get("surface_tension", 0.05)),
                    friction=float(properties.get("friction", 0.55)),
                    cohesion=float(properties.get("cohesion", 0.18)),
                ))
        else:
            rigids.append(RigidBody(entity["id"], list(entity["position"]), list(entity["velocity"]), float(entity["mass"]), entity["shape"]))
    return particles, gravity_bodies, rigids


def _render_indices(
    count: int,
    limit: int,
    *,
    groups: list[str] | None = None,
    visual_materials: list[str] | None = None,
) -> list[int]:
    if count <= limit:
        return list(range(count))
    if limit <= 1:
        selected = [count // 2]
    else:
        selected = sorted({round(index * (count - 1) / (limit - 1)) for index in range(limit)})
    return preserve_render_representatives(
        selected,
        count,
        limit,
        groups=groups,
        visual_materials=visual_materials,
    )


def _frame(
    time_value: float,
    particles: list[Particle],
    bodies: list[GravityBody],
    rigids: list[RigidBody],
    render_indices: list[int],
) -> dict[str, Any]:
    return {
        "t": round(time_value, 7),
        "p": [[round(v, 5) for v in particles[index].pos] for index in render_indices],
        "g": [[round(v, 7) for v in body.pos] for body in bodies],
        "r": [[round(v, 5) for v in rigid.pos] for rigid in rigids],
    }


def _interpolated_frame(
    time_value: float,
    particles: list[Particle],
    bodies: list[GravityBody],
    rigids: list[RigidBody],
    render_indices: list[int],
    particle_start: list[list[float]],
    body_start: list[list[float]],
    rigid_start: list[list[float]],
    alpha: float,
) -> dict[str, Any]:
    amount = clamp(alpha, 0.0, 1.0)
    return {
        "t": round(time_value, 7),
        "p": [
            [round(start[axis] + amount * (particles[index].pos[axis] - start[axis]), 5) for axis in range(3)]
            for start, index in zip(particle_start, render_indices)
        ],
        "g": [
            [round(start[axis] + amount * (body.pos[axis] - start[axis]), 7) for axis in range(3)]
            for start, body in zip(body_start, bodies)
        ],
        "r": [
            [round(start[axis] + amount * (rigid.pos[axis] - start[axis]), 5) for axis in range(3)]
            for start, rigid in zip(rigid_start, rigids)
        ],
    }


def run_scene(scene: dict[str, Any], plan: dict[str, Any], deadline: float) -> dict[str, Any]:
    started = time.monotonic()
    spacing = float(plan["effective_spacing"])
    particles, gravity_bodies, rigids = _make_state(scene, spacing)
    observer = Observer(scene, particles, gravity_bodies, rigids)
    observer.capture(0.0, particles, gravity_bodies, rigids)
    visual_materials = render_material_names(scene, particles)
    render_indices = _render_indices(
        len(particles),
        int(plan.get("render_particle_limit", len(particles) or 1)),
        groups=[particle.group for particle in particles],
        visual_materials=visual_materials,
    )
    world = scene["world"]
    interactions = scene["interactions"]
    force_fields = scene.get("force_fields", [])
    gravity_constant = float(interactions["gravity_G"]) if interactions["mutual_gravity"] else 0.0
    gravity = list(world["gravity"])
    dt = float(world["dt"])
    duration = float(world["duration"])
    output_fps = float(world["output_fps"])
    output_interval = 1.0 / output_fps
    target_frame_count = max(1, math.ceil(duration * output_fps - 1.0e-10)) + 1
    nbody_diagnostics = NBodyDiagnostics(scene, gravity_bodies)
    frames = [_frame(0.0, particles, gravity_bodies, rigids, render_indices)]
    next_frame_index = 1
    macro_steps = 0
    substeps = 0
    minimum_substep_s = 0.0
    sim_time = 0.0
    maximum_penetration = 0.0
    peak_density_error = 0.0
    timed_out = False
    while sim_time < duration - 1e-12:
        if time.monotonic() >= deadline:
            timed_out = True
            break
        macro_start_time = sim_time
        particle_start = [list(particles[index].pos) for index in render_indices]
        body_start = [list(body.pos) for body in gravity_bodies]
        rigid_start = [list(rigid.pos) for rigid in rigids]
        step_dt = min(dt, duration - sim_time)
        macro_end_time = sim_time + step_dt
        try:
            while sim_time < macro_end_time - 1.0e-12:
                segment_end = _next_force_boundary(sim_time, macro_end_time, force_fields, dt)
                segment_dt = segment_end - sim_time
                _check_deadline(deadline)
                _step_gravity(
                    gravity_bodies,
                    segment_dt,
                    gravity_constant,
                    float(interactions["softening"]),
                    force_fields,
                    sim_time,
                    deadline,
                )
                maximum_penetration = max(
                    maximum_penetration,
                    _step_rigids(rigids, gravity, segment_dt, scene["colliders"], world["bounds"]),
                )
                penetration, density_error = _step_particles(
                    particles,
                    rigids,
                    gravity,
                    segment_dt,
                    spacing,
                    int(plan["solver_iterations"]),
                    scene["colliders"],
                    world["bounds"],
                    interactions,
                    force_fields,
                    sim_time,
                    deadline,
                )
                maximum_penetration = max(maximum_penetration, penetration)
                peak_density_error = max(peak_density_error, density_error)
                sim_time = segment_end
                substeps += 1
                if minimum_substep_s == 0.0 or segment_dt < minimum_substep_s:
                    minimum_substep_s = segment_dt
                _check_deadline(deadline)
        except _SimulationDeadlineExceeded:
            timed_out = True
        macro_steps += 1
        if timed_out:
            break
        observer.capture(sim_time, particles, gravity_bodies, rigids)
        while next_frame_index < target_frame_count - 1:
            output_time = next_frame_index * output_interval
            if output_time > sim_time + 1.0e-10:
                break
            frames.append(_interpolated_frame(
                output_time,
                particles,
                gravity_bodies,
                rigids,
                render_indices,
                particle_start,
                body_start,
                rigid_start,
                (output_time - macro_start_time) / (macro_end_time - macro_start_time),
            ))
            next_frame_index += 1

    if not timed_out and sim_time >= duration - 1.0e-9:
        if len(frames) != target_frame_count - 1:
            raise RuntimeError("Frame interpolation did not fill the planned output timestamps.")
        frames.append(_frame(duration, particles, gravity_bodies, rigids, render_indices))

    conservation = nbody_diagnostics.finish(gravity_bodies)
    moved_sand = [norm(sub(particle.pos, particle.anchor)) for particle in particles if particle.material == "sand"]
    wet_sand = [particle.wetness for particle in particles if particle.material == "sand"]
    finite = all(math.isfinite(value) for particle in particles for value in particle.pos + particle.vel)
    finite = finite and all(math.isfinite(value) for body in gravity_bodies for value in body.pos + body.vel)
    diagnostics = {
        "finite": finite,
        "completed": not timed_out and sim_time >= duration - 1e-9,
        "simulated_time_s": sim_time,
        "steps": macro_steps,
        "substeps": substeps,
        "minimum_substep_s": minimum_substep_s,
        "runtime_s": time.monotonic() - started,
        "particle_count": len(particles),
        "render_particle_count": len(render_indices),
        "max_projection_correction_m": maximum_penetration,
        "peak_mean_density_excess": peak_density_error,
        **conservation,
        "force_field_count": len(force_fields),
        "mean_sand_displacement_m": sum(moved_sand) / max(len(moved_sand), 1),
        "mean_sand_wetness": sum(wet_sand) / max(len(wet_sand), 1),
    }
    selected_particles = [particles[index] for index in render_indices]
    materials = [visual_materials[index] for index in render_indices]
    groups = [particle.group for particle in selected_particles]
    return {
        "frames": frames,
        "particle_materials": materials,
        "particle_groups": groups,
        "particle_radius": 0.46 * spacing,
        "gravity_body_ids": [body.ident for body in gravity_bodies],
        "rigid_ids": [body.ident for body in rigids],
        "rigid_shapes": [body.shape for body in rigids],
        "diagnostics": diagnostics,
        **({"observations": observer.result} if scene.get("queries") else {}),
    }
