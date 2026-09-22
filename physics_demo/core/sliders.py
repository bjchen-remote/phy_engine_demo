"""Quantitative, event-split translation of boxes on a common horizontal rail.

Only world x translation is solved.  A slider has a constant prescribed drive
acceleration, Coulomb friction (the same coefficient for static and kinetic
friction), and perfectly rigid 1D contacts.  Pair restitution is the smaller
coefficient.  Persistent contacts share mass-weighted acceleration; friction
stops and pair impacts split the exact constant-acceleration free flight.

This deliberately small model does not implement general 3D rigid bodies,
rotation, fluid coupling, finite rail end stops, or compliant contact forces.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any


@dataclass
class Slider:
    ident: str
    pos: list[float]
    vel: list[float]
    shape: dict[str, Any]
    mass: float
    acceleration: float
    friction: float
    restitution: float

    @property
    def half_width(self) -> float:
        return 0.5 * float(self.shape["size"][0])


class SliderResolutionError(RuntimeError):
    """Contact/event resolution exceeded the explicitly bounded solver model."""


_POSITION_TOLERANCE = 1.0e-10
_VELOCITY_TOLERANCE = 1.0e-11
_TIME_TOLERANCE = 1.0e-13
_MAX_EVENTS_PER_STEP = 1024


def _gap(left: Slider, right: Slider) -> float:
    return right.pos[0] - left.pos[0] - left.half_width - right.half_width


def _resolve_contacts(ordered: list[Slider]) -> int:
    """Resolve impulses, in rail order, until no touching pair approaches."""
    impacts = 0
    for _ in range(256):
        changed = False
        for left, right in zip(ordered, ordered[1:]):
            gap = _gap(left, right)
            if gap < -1.0e-7:
                raise SliderResolutionError("slider_nonpenetration_failed")
            if gap > _POSITION_TOLERANCE:
                continue
            if gap < 0.0:
                # Remove only roundoff; preserve the pair center of mass.
                left.pos[0] += gap * right.mass / (left.mass + right.mass)
                right.pos[0] -= gap * left.mass / (left.mass + right.mass)
            relative = right.vel[0] - left.vel[0]
            # Resolve impulses more tightly than the contact-cluster tolerance:
            # averaging two nearly equal velocities must not leave a neighbouring
            # pair just outside the cluster tolerance and let it interpenetrate.
            if relative >= -0.01 * _VELOCITY_TOLERANCE:
                continue
            restitution = min(left.restitution, right.restitution)
            impulse = -(1.0 + restitution) * relative / (1.0 / left.mass + 1.0 / right.mass)
            left.vel[0] -= impulse / left.mass
            right.vel[0] += impulse / right.mass
            impacts += 1
            changed = True
        if not changed:
            return impacts
    raise SliderResolutionError("slider_contact_iteration_limit")


def _group_acceleration(group: list[Slider], normal_gravity: float) -> float:
    mass = sum(slider.mass for slider in group)
    drive = sum(slider.mass * slider.acceleration for slider in group) / mass
    friction = sum(slider.mass * slider.friction for slider in group) * normal_gravity / mass
    velocity = sum(slider.mass * slider.vel[0] for slider in group) / mass
    if abs(velocity) > _VELOCITY_TOLERANCE:
        return drive - math.copysign(friction, velocity)
    return math.copysign(max(0.0, abs(drive) - friction), drive)


def _accelerations(ordered: list[Slider], normal_gravity: float) -> dict[str, float]:
    """Merge compressing contact groups; no tensile contact force is applied."""
    groups: list[list[Slider]] = [[slider] for slider in ordered]
    index = 0
    while index + 1 < len(groups):
        left, right = groups[index], groups[index + 1]
        touching = _gap(left[-1], right[0]) <= _POSITION_TOLERANCE
        same_velocity = abs(left[-1].vel[0] - right[0].vel[0]) <= _VELOCITY_TOLERANCE
        compressing = _group_acceleration(left, normal_gravity) > _group_acceleration(right, normal_gravity) + 1.0e-12
        if touching and same_velocity and compressing:
            merged = left + right
            mass = sum(slider.mass for slider in merged)
            velocity = sum(slider.mass * slider.vel[0] for slider in merged) / mass
            if abs(velocity) <= _VELOCITY_TOLERANCE:
                velocity = 0.0
            for slider in merged:
                slider.vel[0] = velocity
            groups[index:index + 2] = [merged]
            index = max(0, index - 1)
        else:
            index += 1
    return {
        slider.ident: _group_acceleration(group, normal_gravity)
        for group in groups for slider in group
    }


def _collision_time(gap: float, relative_velocity: float, relative_acceleration: float, horizon: float) -> float | None:
    """Earliest positive approaching root of gap + v*t + a*t²/2."""
    quadratic = 0.5 * relative_acceleration
    roots: list[float] = []
    if abs(quadratic) <= 1.0e-15:
        if relative_velocity < -_VELOCITY_TOLERANCE:
            roots = [-gap / relative_velocity]
    else:
        discriminant = relative_velocity * relative_velocity - 4.0 * quadratic * gap
        if discriminant >= 0.0:
            q = -0.5 * (relative_velocity + math.copysign(math.sqrt(discriminant), relative_velocity))
            if q != 0.0:
                roots = [q / quadratic, gap / q]
            else:
                roots = [-relative_velocity / (2.0 * quadratic)]
    admissible = [
        root for root in roots
        if _TIME_TOLERANCE < root <= horizon + _TIME_TOLERANCE
        and relative_velocity + relative_acceleration * root < -_VELOCITY_TOLERANCE
    ]
    return min(admissible) if admissible else None


def _frame(time_value: float, sliders: list[Slider], accelerations: dict[str, float] | None = None, elapsed: float = 0.0) -> dict[str, Any]:
    return {
        "t": round(time_value, 7), "p": [], "g": [],
        "r": [
            [round(slider.pos[0] + elapsed * slider.vel[0] + 0.5 * (accelerations or {}).get(slider.ident, 0.0) * elapsed * elapsed, 7),
             round(slider.pos[1], 7), round(slider.pos[2], 7)]
            for slider in sliders
        ],
    }


def run_scene(scene: dict[str, Any], plan: dict[str, Any], deadline: float) -> dict[str, Any]:
    """Run a normalized all-slider scene within the caller's wall-time deadline."""
    from physics_demo.analysis.observers import Observer

    started = time.monotonic()
    sliders = [Slider(
        ident=entity["id"], pos=list(entity["position"]), vel=list(entity["velocity"]),
        shape={"type": "box", "size": list(entity["size"])}, mass=float(entity["mass"]),
        acceleration=float(entity.get("acceleration", 0.0)), friction=float(entity.get("friction", 0.0)),
        restitution=float(entity.get("restitution", 0.0)),
    ) for entity in scene["entities"]]
    ordered = sorted(sliders, key=lambda slider: slider.pos[0])
    if any(_gap(left, right) < -_POSITION_TOLERANCE for left, right in zip(ordered, ordered[1:])):
        raise SliderResolutionError("slider_initial_overlap")
    world = scene["world"]
    dt, duration, fps = float(world["dt"]), float(world["duration"]), float(world["output_fps"])
    normal_gravity = abs(float(world["gravity"][1]))
    frame_count = max(1, math.ceil(duration * fps - 1.0e-10)) + 1
    frames = [_frame(0.0, sliders)]
    observer = Observer(scene, [], [], sliders)
    observer.capture(0.0, [], [], sliders)
    sim_time = 0.0
    macro_steps = substeps = impacts = 0
    minimum_substep = 0.0
    next_frame = 1
    timed_out = False
    while sim_time < duration - 1.0e-12:
        if time.monotonic() >= deadline:
            timed_out = True
            break
        macro_end = min(duration, (macro_steps + 1) * dt)
        step_events = 0
        while sim_time < macro_end - _TIME_TOLERANCE:
            if time.monotonic() >= deadline:
                timed_out = True
                break
            impacts += _resolve_contacts(ordered)
            accelerations = _accelerations(ordered, normal_gravity)
            horizon = macro_end - sim_time
            segment = horizon
            stops: dict[str, float] = {}
            for slider in sliders:
                acceleration = accelerations[slider.ident]
                if slider.vel[0] * acceleration < 0.0:
                    stop = -slider.vel[0] / acceleration
                    if _TIME_TOLERANCE < stop <= horizon + _TIME_TOLERANCE:
                        stops[slider.ident] = stop
                        segment = min(segment, stop)
            for left, right in zip(ordered, ordered[1:]):
                contact = _collision_time(max(0.0, _gap(left, right)), right.vel[0] - left.vel[0],
                                          accelerations[right.ident] - accelerations[left.ident], segment)
                if contact is not None:
                    segment = min(segment, contact)
            if segment <= _TIME_TOLERANCE:
                raise SliderResolutionError("slider_event_time_stagnation")
            end = sim_time + segment
            while next_frame < frame_count - 1 and next_frame / fps <= end + 1.0e-10:
                frame_time = next_frame / fps
                frames.append(_frame(frame_time, sliders, accelerations, max(0.0, min(segment, frame_time - sim_time))))
                next_frame += 1
            for slider in sliders:
                acceleration = accelerations[slider.ident]
                slider.pos[0] += segment * slider.vel[0] + 0.5 * acceleration * segment * segment
                slider.vel[0] += acceleration * segment
                if slider.ident in stops and abs(stops[slider.ident] - segment) <= _TIME_TOLERANCE:
                    slider.vel[0] = 0.0
                if abs(slider.vel[0]) <= _VELOCITY_TOLERANCE:
                    slider.vel[0] = 0.0
            sim_time = end
            substeps += 1
            minimum_substep = segment if minimum_substep == 0.0 else min(minimum_substep, segment)
            step_events += 1
            if step_events > _MAX_EVENTS_PER_STEP:
                raise SliderResolutionError("slider_event_limit")
        if timed_out:
            break
        impacts += _resolve_contacts(ordered)
        macro_steps += 1
        observer.capture(sim_time, [], [], sliders)
    completed = not timed_out and sim_time >= duration - 1.0e-9
    if completed:
        if len(frames) != frame_count - 1:
            raise SliderResolutionError("slider_frame_count_mismatch")
        frames.append(_frame(duration, sliders))
    finite = all(math.isfinite(value) for slider in sliders for value in slider.pos + slider.vel)
    trajectory = {
        "frames": frames, "particle_materials": [], "particle_groups": [],
        "particle_radius": 0.46 * float(plan.get("effective_spacing", 0.1)),
        "gravity_body_ids": [], "rigid_ids": [slider.ident for slider in sliders],
        "rigid_shapes": [slider.shape for slider in sliders],
        "diagnostics": {
            "backend": "analytic-1d-slider", "finite": finite, "completed": completed,
            "simulated_time_s": sim_time, "steps": macro_steps, "substeps": substeps,
            "minimum_substep_s": minimum_substep, "runtime_s": time.monotonic() - started,
            "particle_count": 0, "render_particle_count": 0, "force_field_count": 0,
            "max_projection_correction_m": 0.0, "peak_mean_density_excess": 0.0,
            "nbody_relative_energy_drift": None, "nbody_momentum_drift": None,
            "nbody_invariants_conserved": False, "nbody_invariants_applicable": False,
            "nbody_momentum_tolerance": None, "nbody_invariant_exclusion_reason": None,
            "mean_sand_displacement_m": 0.0, "mean_sand_wetness": 0.0,
            "slider_impact_count": impacts,
            "slider_min_surface_gap_m": min((_gap(a, b) for a, b in zip(ordered, ordered[1:])), default=0.0),
        },
    }
    if scene.get("queries"):
        trajectory["observations"] = observer.result
    return trajectory
