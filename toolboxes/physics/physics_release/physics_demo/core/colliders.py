"""Reference collision projections for supported analytic boundaries."""

from __future__ import annotations

from typing import Any

from physics_demo.core.math3d import add, clamp, dot, mul, norm, norm_sq, sub, unit


def project_plane(pos: list[float], radius: float, collider: dict[str, Any]) -> float:
    normal = unit(collider["normal"], [0.0, 1.0, 0.0])
    signed = dot(normal, pos) - float(collider["offset"])
    penetration = radius - signed
    if penetration > 0.0:
        pos[0] += normal[0] * penetration
        pos[1] += normal[1] * penetration
        pos[2] += normal[2] * penetration
        return penetration
    return 0.0


def project_sphere(pos: list[float], radius: float, collider: dict[str, Any]) -> float:
    delta = sub(pos, collider["center"])
    distance = norm(delta)
    minimum = float(collider["radius"]) + radius
    if distance < minimum:
        normal = unit(delta, [0.0, 1.0, 0.0])
        correction = minimum - distance
        pos[0] += normal[0] * correction
        pos[1] += normal[1] * correction
        pos[2] += normal[2] * correction
        return correction
    return 0.0


def project_box(
    pos: list[float],
    radius: float,
    collider: dict[str, Any],
    previous: list[float] | None = None,
) -> float:
    half = [0.5 * float(collider["size"][axis]) + radius for axis in range(3)]
    local = sub(pos, collider["center"])
    if previous is not None:
        previous_local = sub(previous, collider["center"])
        previous_inside = all(abs(previous_local[axis]) < half[axis] for axis in range(3))
        if previous_inside:
            face_distances = [half[axis] - abs(previous_local[axis]) for axis in range(3)]
            axis = min(range(3), key=lambda item: face_distances[item])
            sign = 1.0 if previous_local[axis] >= 0.0 else -1.0
            direction = local[axis] - previous_local[axis]
            if direction * sign <= 0.0:
                epsilon = max(1e-12, radius * 1e-9)
                target = sign * (half[axis] + epsilon)
                center = float(collider["center"][axis])
                correction = abs(pos[axis] - center - target)
                pos[axis] = center + target
                return correction
        if not previous_inside:
            direction = sub(local, previous_local)
            entry = 0.0
            exit_time = 1.0
            hit_axis = -1
            hit_sign = 0.0
            intersects = True
            for axis in range(3):
                if abs(direction[axis]) <= 1e-15:
                    if abs(previous_local[axis]) >= half[axis]:
                        intersects = False
                        break
                    continue
                near = (-half[axis] - previous_local[axis]) / direction[axis]
                far = (half[axis] - previous_local[axis]) / direction[axis]
                normal_sign = -1.0 if direction[axis] > 0.0 else 1.0
                if near > far:
                    near, far = far, near
                if near > entry or (hit_axis < 0 and near >= entry - 1e-12):
                    entry = near
                    hit_axis = axis
                    hit_sign = normal_sign
                exit_time = min(exit_time, far)
                if entry > exit_time:
                    intersects = False
                    break
            if intersects and hit_axis >= 0 and -1e-12 <= entry <= 1.0 + 1e-12 and exit_time >= 0.0:
                entry = clamp(entry, 0.0, 1.0)
                remaining_normal = direction[hit_axis] * (1.0 - entry) * hit_sign
                if remaining_normal < 0.0:
                    epsilon = max(1e-12, radius * 1e-9)
                    correction = -remaining_normal + epsilon
                    pos[hit_axis] -= remaining_normal * hit_sign
                    pos[hit_axis] += epsilon * hit_sign
                    return correction
    if all(abs(local[axis]) < half[axis] for axis in range(3)):
        distances = [half[axis] - abs(local[axis]) for axis in range(3)]
        axis = min(range(3), key=lambda item: distances[item])
        sign = 1.0 if local[axis] >= 0.0 else -1.0
        correction = distances[axis]
        pos[axis] += sign * correction
        return correction
    return 0.0


def project_capsule(pos: list[float], radius: float, collider: dict[str, Any]) -> float:
    start = collider["a"]
    segment = sub(collider["b"], start)
    segment_squared = norm_sq(segment)
    amount = clamp(dot(sub(pos, start), segment) / max(segment_squared, 1.0e-18), 0.0, 1.0)
    closest = add(start, mul(segment, amount))
    delta = sub(pos, closest)
    distance = norm(delta)
    minimum = radius + float(collider["radius"])
    if distance >= minimum:
        return 0.0
    direction = unit(delta, [1.0, 0.0, 0.0])
    correction = minimum - distance
    pos[0] += direction[0] * correction
    pos[1] += direction[1] * correction
    pos[2] += direction[2] * correction
    return correction


def project_colliders(
    pos: list[float],
    radius: float,
    colliders: list[dict[str, Any]],
    previous: list[float] | None = None,
) -> float:
    maximum = 0.0
    projectors = {
        "plane": project_plane,
        "sphere": project_sphere,
        "box": project_box,
        "capsule": project_capsule,
    }
    for collider in colliders:
        before = list(pos)
        if collider["type"] == "box":
            correction = project_box(pos, radius, collider, previous)
        else:
            correction = projectors[collider["type"]](pos, radius, collider)
        maximum = max(maximum, correction)
        friction = float(collider.get("friction", 0.2))
        if previous is None or correction <= 1.0e-15 or friction <= 0.0:
            continue
        normal = [(pos[axis] - before[axis]) / correction for axis in range(3)]
        displacement = sub(pos, previous)
        normal_displacement = dot(displacement, normal)
        tangent = sub(displacement, mul(normal, normal_displacement))
        tangent_length = norm(tangent)
        if tangent_length > 1.0e-15:
            amount = min(1.0, friction * correction / tangent_length)
            for axis in range(3):
                pos[axis] -= amount * tangent[axis]
    return maximum
