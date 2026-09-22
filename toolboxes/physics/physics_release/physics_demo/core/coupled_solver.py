"""One native shared-clock call for existing particle/mesh and rigid dynamics.

Packing reuses the particle state/field helpers and the complete mesh packer.
The bridge only maps the public coupled contract and owns native buffers; no
physics is duplicated here and no alternate approximate solver is selected.
"""
from __future__ import annotations

import ctypes as C
import math
from pathlib import Path
import time
from typing import Any

from physics_demo.io.coupled import endpoints
from physics_demo.core.rigid_body import inertia, rotate
from physics_demo.core.engine import _make_state
from physics_demo.core.liquids import render_material_names
from physics_demo.core import mesh_solver as mesh
from physics_demo.core.native_backend import (
    ABI_VERSION, CCollider, CDiagnostics, CForceField, CObservationMetric, CSimulation,
    NativeResourceLimitError, NativeSimulationError, STATUS_NAMES, _array, _colliders,
    _force_fields, _render_indices, _state_arrays,
)
from physics_demo.core.native_runtime import load_library
from physics_demo.analysis.observers import unpack_observations


class Vec3(C.Structure):
    _fields_ = [(name, C.c_double) for name in ("x", "y", "z")]


class Quaternion(C.Structure):
    _fields_ = [(name, C.c_double) for name in ("w", "x", "y", "z")]


class Body(C.Structure):
    _fields_ = [("position", Vec3), ("velocity", Vec3), ("orientation", Quaternion),
                ("angular_momentum", Vec3), ("inv_mass", C.c_double), ("inertia_body_diag", Vec3)]


class Point(C.Structure):
    _fields_ = [("position", Vec3), ("velocity", Vec3), ("inverse_mass", C.c_double),
                ("radius", C.c_double), ("field_mask", C.c_uint32)]


class Rigid(C.Structure):
    _fields_ = [("body", Body), ("size", Vec3), ("pivot_point", Vec3), ("pivot_local", Vec3)] + [
        (name, C.c_double) for name in ("radius", "height", "mass", "friction", "restitution")] + [
        (name, C.c_int32) for name in ("shape", "fixed", "pivot")]


class Endpoint(C.Structure):
    _fields_ = [("kind", C.c_int32), ("index", C.c_int32), ("local_point", Vec3)]


class Link(C.Structure):
    _fields_ = [("type", C.c_int32), ("a", Endpoint), ("b", Endpoint)] + [
        (name, C.c_double) for name in ("rest", "stiffness", "damping", "radius")]


class Entity(C.Structure):
    _fields_ = [(name, C.c_int32) for name in ("kind", "start", "count")]


class Metric(C.Structure):
    _fields_ = [(name, C.c_int32) for name in ("type", "a", "b", "axis0", "axis1")] + [("origin", Vec3)]


class Simulation(C.Structure):
    _fields_ = [("abi", C.c_uint32)] + [(name, C.c_int32) for name in (
        "points", "rigids", "links", "entities", "metrics", "fields", "colliders",
        "substeps", "iterations", "frame_capacity", "observation_capacity")] + [
        (name, C.c_double) for name in ("dt", "duration", "fps", "deadline_seconds", "friction")] + [
        ("gravity", Vec3), ("particles", C.POINTER(CSimulation)), ("mesh", C.POINTER(mesh.Simulation)),
        ("point", C.POINTER(Point)), ("rigid", C.POINTER(Rigid)), ("link", C.POINTER(Link)),
        ("entity", C.POINTER(Entity)), ("metric", C.POINTER(Metric)),
        ("field", C.POINTER(CForceField)), ("collider", C.POINTER(CCollider))] + [
        (name, C.POINTER(C.c_double)) for name in (
            "point_frames", "rigid_frames", "frame_times", "observation_times", "observation_values")]


class Diagnostics(C.Structure):
    _fields_ = [(name, C.c_int32) for name in (
        "status", "completed", "finite", "frames_written", "observations_written",
        "steps", "substeps", "max_substeps_used", "contact_count")] + [
        (name, C.c_double) for name in (
            "simulated_time_s", "runtime_s", "max_penetration_m", "max_speed_m_s", "max_quaternion_error",
            "contact_impulse_norm", "attachment_impulse_norm", "max_link_constraint_error")] + [
        (name, Vec3) for name in ("initial_momentum", "final_momentum", "support_impulse")]


_LIBRARIES: dict[str, C.CDLL] = {}


def _bind_library(library: C.CDLL) -> None:
    library.coupled_abi_version.argtypes = []
    library.coupled_abi_version.restype = C.c_uint32
    library.coupled_simulate.argtypes = [C.POINTER(Simulation), C.POINTER(Diagnostics),
                                       C.POINTER(CDiagnostics), C.POINTER(mesh.Diagnostics)]
    library.coupled_simulate.restype = C.c_int32
    if library.coupled_abi_version() != 1:
        raise OSError("incompatible coupled ABI")


def _load_library(deadline: float) -> tuple[C.CDLL, float, str]:
    directory = Path(__file__).with_name("native")
    return load_library(directory / "coupled_native.c", prefix="libcoupled-", name="Coupled",
                        deadline=deadline, libraries=_LIBRARIES, bind=_bind_library,
                        extra_sources=(directory / "physics_native.c", directory / "mesh_native.c"),
                        dependencies=(directory / "rigid_math.h",), pthread=True)


def _pack_particles(scene, plan, particles, fields, masks, frames, deadline):
    """Reuse the existing sampled DFSPH state and SoA packing, without N-body."""
    if not particles:
        return None, []
    materials = [0 if p.material == "water" else 1 for p in particles]
    selected = _render_indices(
        materials,
        int(plan.get("render_particle_limit", len(particles))),
        groups=[particle.group for particle in particles],
        visual_materials=render_material_names(scene, particles),
    )
    world, interactions = scene["world"], scene["interactions"]
    bounds = world["bounds"]
    iterations = int(plan.get("density_iterations", plan.get("solver_iterations", 5)))
    simulation = CSimulation(
        abi_version=ABI_VERSION, particle_count=len(particles), body_count=0,
        collider_count=len(scene["colliders"]), field_count=len(fields), render_count=len(selected),
        frame_capacity=frames, density_iterations=iterations,
        divergence_iterations=int(plan.get("divergence_iterations", max(1, iterations // 2))),
        thread_count=max(1, min(32, int(plan.get("threads", 1)))),
        max_substeps=max(1, min(32, int(plan.get("max_substeps", 8)))),
        spacing=float(plan["effective_spacing"]), dt=float(world["dt"]), duration=float(world["duration"]),
        output_fps=float(world["output_fps"]), gravity=(C.c_double * 3)(*world["gravity"]),
        bounds_min=(C.c_double * 3)(*bounds["min"]), bounds_max=(C.c_double * 3)(*bounds["max"]),
        gravity_G=0, softening=float(interactions["softening"]),
        water_sand_drag=float(interactions["water_sand_drag"]), wetting_rate=float(interactions["wetting_rate"]),
        deadline_seconds=max(0, deadline-time.monotonic()), **_state_arrays(particles, []),
        material=_array(C.c_int32, materials), colliders=_array(CCollider, _colliders(scene, [])),
        fields=_array(CForceField, fields), particle_field_mask=_array(C.c_uint32, masks),
        body_field_mask=_array(C.c_uint32, []), render_indices=_array(C.c_int32, selected),
        frame_particles=(C.c_float * max(1, frames * len(selected) * 3))(),
        frame_bodies=(C.c_double * 1)(), frame_times=(C.c_double * frames)(),
        observation_metrics=_array(CObservationMetric, []), observation_times=(C.c_double * 1)(),
        observation_values=(C.c_double * 1)(), observation_nbody_values=(C.c_double * 1)())
    return simulation, selected


def _pack_rigid(entity: dict) -> Rigid:
    q = entity["orientation"]
    principal = inertia(entity)
    omega_body = rotate([q[0], -q[1], -q[2], -q[3]], entity["angular_velocity"])
    momentum = rotate(q, [principal[i]*omega_body[i] for i in range(3)])
    pivot = entity.get("pivot")
    body = Body(Vec3(*entity["position"]), Vec3(*entity["velocity"]), Quaternion(*q), Vec3(*momentum),
                0 if entity["fixed"] or pivot else 1/entity["mass"], Vec3(*principal))
    shape = entity["shape"]
    return Rigid(body=body, size=Vec3(*shape.get("size", [0, 0, 0])),
                 pivot_point=Vec3(*(pivot["point"] if pivot else [0, 0, 0])),
                 pivot_local=Vec3(*(pivot["local_point"] if pivot else [0, 0, 0])),
                 radius=float(shape.get("radius", 0)), height=float(shape.get("height", 0)),
                 mass=float(entity["mass"]), friction=float(entity["friction"]),
                 restitution=float(entity["restitution"]),
                 shape={"sphere": 0, "box": 1, "cylinder": 2}[shape["type"]],
                 fixed=int(entity["fixed"]), pivot=int(bool(pivot)))


_METRICS = {"spread_radius": 0, "centroid": 1, "speed": 2, "center_distance": 3,
            "volume_ratio": 4, "max_displacement": 5, "max_edge_strain": 6,
            "connection_length": 7, "connection_extension": 8, "spring_force": 9,
            "spring_energy": 10, "angular_speed": 11, "angular_momentum": 12,
            "rotational_energy": 13, "axis_tilt": 14}


def _pack(scene: dict[str, Any], plan: dict[str, Any], deadline: float) -> tuple[Simulation, dict[str, Any]]:
    world, definitions = scene["world"], scene["entities"]
    frames = max(1, math.ceil(world["duration"] * world["output_fps"] - 1e-10)) + 1
    queries = scene.get("queries", [])
    observations = math.ceil(world["duration"] / world["dt"]) + 2 if queries else 0
    substeps, iterations = int(plan["coupling_substeps"]), int(plan["coupling_iterations"])
    if (frames > 2401 or len(queries) > 16 or observations > 250002 or len(definitions) > 128
            or not 1 <= substeps <= 64 or not 1 <= iterations <= 16):
        raise NativeResourceLimitError("Coupled state, output or substep allocation exceeds its bounded capacity.")
    sampled_scene = {**scene, "entities": [e for e in definitions if e["type"] in {"fluid", "granular", "point_mass"}]}
    particles, point_records, _ = _make_state(sampled_scene, float(plan["effective_spacing"]))
    point_defs = [e for e in definitions if e["type"] == "point_mass"]
    rigid_defs = [e for e in definitions if e["type"] == "rigid_body"]
    mesh_defs = [e for e in definitions if e["type"] == "mesh"]
    link_defs = scene.get("connections", [])
    if len(particles) > 4096 or len(point_defs) > 64 or len(rigid_defs) > 16 or len(link_defs) > 128:
        raise NativeResourceLimitError("Coupled particle, point, rigid or connection capacity exceeded.")
    fields, particle_masks, point_masks = _force_fields(scene, particles, point_records)
    particle_sim, selected = _pack_particles(scene, plan, particles, fields, particle_masks, frames, deadline)
    mesh_sim, mesh_objects = None, []
    if mesh_defs:
        if sum(len(e["mesh"]["vertices"]) for e in mesh_defs) > 2048:
            raise NativeResourceLimitError("Coupled meshes are limited to 2048 explicit vertices.")
        mesh_plan = {**plan, "mesh_substeps": min(32, int(plan.get("mesh_substeps", 4)))}
        mesh_sim, mesh_objects = mesh._pack({**scene, "entities": mesh_defs, "queries": []}, mesh_plan, deadline)
    point_ids = {e["id"]: i for i, e in enumerate(point_defs)}
    rigid_ids = {e["id"]: i for i, e in enumerate(rigid_defs)}
    mesh_ids = {e["id"]: i for i, e in enumerate(mesh_defs)}
    effective_masses = {e["id"]: float(e["mass"]) for e in point_defs}
    for link in link_defs:
        mass = float(link.get("solid", {}).get("mass", 0))
        for end in endpoints(link):
            if mass:
                if end["entity"] not in effective_masses:
                    raise NativeResourceLimitError("Positive solid connection mass requires point endpoints.")
                effective_masses[end["entity"]] += mass / 2
    points = [Point(Vec3(*e["position"]), Vec3(*e["velocity"]),
                    0 if e["fixed"] else 1/effective_masses[e["id"]],
                    float(e.get("collision_radius", 0)), point_masks[i]) for i, e in enumerate(point_defs)]

    def attachment(end):
        ident = end["entity"]
        if ident in point_ids:
            return Endpoint(0, point_ids[ident], Vec3())
        if ident in mesh_ids:
            obj = mesh_objects[mesh_ids[ident]]
            return Endpoint(1, obj["vertex_start"] + end["vertex"], Vec3())
        return Endpoint(2, rigid_ids[ident], Vec3(*end.get("local_point", [0, 0, 0])))

    links = [Link({"spring": 0, "rod": 1, "rope": 2}[e["type"]], *(attachment(x) for x in endpoints(e)),
                  float(e["rest_length"]), float(e.get("stiffness", 0)), float(e.get("damping", 0)),
                  float(e.get("solid", {}).get("radius", 0))) for e in link_defs]
    ranges: dict[str, list[int]] = {}
    for i, particle in enumerate(particles):
        if particle.group not in ranges:
            ranges[particle.group] = [i, 0]
        ranges[particle.group][1] += 1
    entities = []
    for definition in definitions:
        ident, kind = definition["id"], definition["type"]
        if kind in {"fluid", "granular"}:
            start, count = ranges.get(ident, [0, 0])
            entities.append(Entity(0, start, count))
        elif kind == "point_mass":
            entities.append(Entity(1, point_ids[ident], 1))
        elif kind == "mesh":
            entities.append(Entity(2, mesh_ids[ident], len(definition["mesh"]["vertices"])))
        else:
            entities.append(Entity(3, rigid_ids[ident], 1))
    entity_ids = {e["id"]: i for i, e in enumerate(definitions)}
    link_ids = {e["id"]: i for i, e in enumerate(link_defs)}
    metrics = []
    for query in queries:
        metric = query["metric"]
        kind = _METRICS[metric["type"]]
        if 7 <= kind <= 10:
            a = b = link_ids[metric["connection"]]
        else:
            ids = metric.get("entities", [metric.get("entity")])
            a, b = entity_ids[ids[0]], entity_ids[ids[-1]]
        axes = ["xyz".index(x) for x in metric.get("plane", "xz")]
        if kind in {1, 12}:
            axes = ["xyz".index(metric["axis"]), 0]
        metrics.append(Metric(kind, a, b, *axes, Vec3(*metric.get("origin", [0, 0, 0]))))
    colliders = _colliders(scene, [])
    simulation = Simulation(abi=1, points=len(points), rigids=len(rigid_defs), links=len(links),
        entities=len(entities), metrics=len(metrics), fields=len(fields), colliders=len(colliders),
        substeps=substeps, iterations=iterations, frame_capacity=frames, observation_capacity=observations,
        dt=float(world["dt"]), duration=float(world["duration"]), fps=float(world["output_fps"]),
        deadline_seconds=max(0, deadline-time.monotonic()), friction=float(scene["coupling"]["friction"]),
        gravity=Vec3(*world["gravity"]), particles=C.pointer(particle_sim) if particle_sim is not None else None,
        mesh=C.pointer(mesh_sim) if mesh_sim is not None else None,
        point=_array(Point, points), rigid=_array(Rigid, [_pack_rigid(e) for e in rigid_defs]),
        link=_array(Link, links), entity=_array(Entity, entities), metric=_array(Metric, metrics),
        field=_array(CForceField, fields), collider=_array(CCollider, colliders),
        point_frames=(C.c_double * max(1, frames * len(points) * 3))(),
        rigid_frames=(C.c_double * max(1, frames * len(rigid_defs) * 7))(),
        frame_times=(C.c_double * frames)(), observation_times=(C.c_double * max(1, observations))(),
        observation_values=(C.c_double * max(1, observations * len(metrics)))())
    metadata = {"particle_materials": render_material_names(scene, [particles[i] for i in selected]),
                "particle_groups": [particles[i].group for i in selected],
                "particle_radius": .46 * float(plan["effective_spacing"]) if particles else 0.,
                "gravity_body_ids": [e["id"] for e in point_defs], "rigid_ids": [e["id"] for e in rigid_defs],
                "rigid_shapes": [e["shape"] for e in rigid_defs], "mesh_objects": mesh_objects,
                "point_effective_masses": effective_masses}
    return simulation, metadata


def _diagnostic_values(diagnostic):
    return {name: [getattr(value, axis) for axis in "xyz"] if isinstance(value, Vec3) else value
            for name, _ in diagnostic._fields_ for value in [getattr(diagnostic, name)]}


def _unpack(scene, simulation, metadata, diagnostic, particle_diagnostic, mesh_diagnostic,
            status, compile_seconds, build, started):
    p = simulation.particles.contents if simulation.particles else None
    m = simulation.mesh.contents if simulation.mesh else None
    particle_stats, mesh_stats = _diagnostic_values(particle_diagnostic), _diagnostic_values(mesh_diagnostic)
    diagnostics = _diagnostic_values(diagnostic)
    diagnostics.pop("status")
    diagnostics.pop("frames_written")
    diagnostics.pop("observations_written")
    for name, value in particle_stats.items():
        if name not in diagnostics and name not in {"status", "frames_written", "observations_written", "macro_steps"}:
            diagnostics[name] = value
    diagnostics.update(finite=bool(diagnostic.finite), completed=bool(diagnostic.completed),
        backend="native-c11-coupled", native_status=STATUS_NAMES.get(status, f"status_{status}"),
        native_build=build, native_compile_s=compile_seconds, bridge_runtime_s=time.monotonic()-started,
        particle_count=p.particle_count if p else 0, render_particle_count=p.render_count if p else 0,
        mesh_vertex_count=m.vertices if m else 0, mesh_triangle_count=m.triangles if m else 0,
        mesh_object_count=m.objects if m else 0, force_field_count=simulation.fields,
        threads_used=particle_diagnostic.threads_used if p else 1,
        state_precision="float64", frame_precision="float32 particles/mesh; float64 points/rigids",
        nbody_invariants_applicable=False, nbody_invariants_conserved=False,
        nbody_invariant_exclusion_reason="coupled_contact_system", nbody_relative_energy_drift=None,
        nbody_momentum_drift=None, nbody_momentum_tolerance=None,
        density_iteration_limit_fraction=particle_diagnostic.density_iteration_limit_hits / max(particle_diagnostic.substeps, 1),
        divergence_iteration_limit_fraction=particle_diagnostic.divergence_iteration_limit_hits / max(particle_diagnostic.substeps, 1),
        particle_diagnostics=particle_stats, mesh_diagnostics=mesh_stats,
        solver_features=["shared-native-clock", "persistent-dfsph-particles", "persistent-xpbd-surfaces",
                         "paired-attachment-impulses", "finite-mass-contact-reactions", "quaternion-rigid-rotation"],
        boundary_pressure_coupling="existing planar boundary support; dynamic cross-domain contacts are partitioned finite-radius impulses",
        coupling_scope="Partitioned two-way contact; no monolithic dynamic boundary pressure solve or calibrated buoyancy.")
    frames = []
    for j in range(diagnostic.frames_written):
        frames.append({"t": float(simulation.frame_times[j]),
            "p": [[float(p.frame_particles[(j*p.render_count+i)*3+k]) for k in range(3)] for i in range(p.render_count)] if p else [],
            "g": [[float(simulation.point_frames[(j*simulation.points+i)*3+k]) for k in range(3)] for i in range(simulation.points)],
            "r": [[float(simulation.rigid_frames[(j*simulation.rigids+i)*7+k]) for k in range(3)] for i in range(simulation.rigids)],
            "q": [[float(simulation.rigid_frames[(j*simulation.rigids+i)*7+3+k]) for k in range(4)] for i in range(simulation.rigids)],
            "m": [[float(m.frames[(j*m.vertices+i)*3+k]) for k in range(3)] for i in range(m.vertices)] if m else []})
    result = {**metadata, "frames": frames, "diagnostics": diagnostics}
    if simulation.metrics:
        result["observations"] = unpack_observations(
            [query["id"] for query in scene["queries"]], simulation.observation_times,
            simulation.observation_values, diagnostic.observations_written)
    return result


def run_scene_coupled(scene: dict[str, Any], plan: dict[str, Any], deadline: float) -> dict[str, Any]:
    started = time.monotonic()
    simulation, metadata = _pack(scene, plan, deadline)
    library, compile_seconds, build = _load_library(deadline)
    remaining = max(0., deadline-time.monotonic())
    simulation.deadline_seconds = remaining
    if simulation.particles:
        simulation.particles.contents.deadline_seconds = remaining
    if simulation.mesh:
        simulation.mesh.contents.deadline_seconds = remaining
    diagnostic, particle_diagnostic, mesh_diagnostic = Diagnostics(), CDiagnostics(), mesh.Diagnostics()
    status = int(library.coupled_simulate(C.byref(simulation), C.byref(diagnostic),
                 C.byref(particle_diagnostic), C.byref(mesh_diagnostic)))
    if status in {1, 2, 5}:
        if status == 5:
            raise NativeResourceLimitError("Coupled native solver exceeded its substep or contact-work limit; refine dt or reduce the prepared scene workload.")
        raise NativeResourceLimitError("Coupled native solver returned " + STATUS_NAMES[status] + ".")
    if status not in {0, 3, 4}:
        raise NativeSimulationError("Unexpected coupled native solver status: " + str(status))
    return _unpack(scene, simulation, metadata, diagnostic, particle_diagnostic, mesh_diagnostic,
                   status, compile_seconds, build, started)
