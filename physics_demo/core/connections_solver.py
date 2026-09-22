"""One bounded native call for massless connections between point masses.

The original particle/N-body ABI is unchanged. Full double precision states are
observed at macro steps; rendering interpolates positions without changing physics.
"""
from __future__ import annotations

import ctypes as C
import math
from pathlib import Path
import time
from types import SimpleNamespace
from typing import Any

from physics_demo.core.native_backend import (CForceField, NativeResourceLimitError,
                             NativeSimulationError, STATUS_NAMES, _array, _force_fields)
from physics_demo.core.native_runtime import load_library
from physics_demo.analysis.observers import unpack_observations

ABI_VERSION = 1


class Connection(C.Structure):
    _fields_ = [(name, C.c_int32) for name in ("type", "a", "b")] + [
        (name, C.c_double) for name in ("rest", "stiffness", "damping")]


class Metric(C.Structure):
    _fields_ = [(name, C.c_int32) for name in ("type", "a", "b", "axis")]


class Simulation(C.Structure):
    _fields_ = [("abi", C.c_uint32)] + [(name, C.c_int32) for name in (
        "nodes", "connections", "fields", "metrics", "frame_capacity", "observation_capacity",
        "iterations", "substeps")] + [(name, C.c_double) for name in (
        "dt", "duration", "fps", "deadline_seconds")] + [
        (name, C.POINTER(C.c_double)) for name in ("positions", "velocities", "mass")] + [
        ("fixed", C.POINTER(C.c_int32)), ("field_mask", C.POINTER(C.c_uint32)),
        ("connection", C.POINTER(Connection)), ("field", C.POINTER(CForceField)),
        ("metric", C.POINTER(Metric))] + [(name, C.POINTER(C.c_double)) for name in (
        "frames", "frame_times", "observation_times", "observation_values")]


class Diagnostics(C.Structure):
    _fields_ = [(name, C.c_int32) for name in (
        "status", "completed", "finite", "frames_written", "observations_written", "steps",
        "substeps", "max_substeps_used")] + [(name, C.c_double) for name in (
        "simulated_time_s", "runtime_s", "max_rod_error_m", "max_rope_extension_m", "max_constraint_error_ratio", "max_speed_m_s",
        "initial_kinetic_energy", "final_kinetic_energy", "initial_spring_energy", "final_spring_energy",
        "connection_peak_relative_energy_drift")]


_LIBRARIES: dict[str, C.CDLL] = {}


def _bind_library(library: C.CDLL) -> None:
    library.connections_abi_version.argtypes = []
    library.connections_abi_version.restype = C.c_uint32
    library.connections_simulate.argtypes = [C.POINTER(Simulation), C.POINTER(Diagnostics)]
    library.connections_simulate.restype = C.c_int32
    if library.connections_abi_version() != ABI_VERSION:
        raise OSError("incompatible connections ABI")


def _load_library(deadline: float) -> tuple[C.CDLL, float, str]:
    return load_library(
        Path(__file__).with_name("native") / "connections_native.c",
        prefix="libconnections-", name="Connections", deadline=deadline,
        libraries=_LIBRARIES, bind=_bind_library,
    )


def _pack(scene: dict[str, Any], plan: dict[str, Any], deadline_seconds: float) -> Simulation:
    """Allocate bounded owned buffers; C independently validates all ABI parameters."""
    entities, world, links = scene["entities"], scene["world"], scene["connections"]
    queries = scene.get("queries", [])
    dt, duration, fps = (float(world[name]) for name in ("dt", "duration", "output_fps"))
    if (not 2 <= len(entities) <= 64 or not 1 <= len(links) <= 256
            or len(queries) > 16 or len(scene.get("force_fields", [])) > 8
            or any(entity["type"] != "point_mass" for entity in entities)
            or any(not math.isfinite(x) or x <= 0 for x in (dt, duration, fps))
            or duration / dt > 250000 or duration * fps > 2400):
        raise NativeResourceLimitError("Connections state or sample counts exceed native limits.")
    frame_capacity = max(1, math.ceil(duration*fps-1e-10))+1
    observation_capacity = math.ceil(duration/dt)+1 if queries else 0
    if observation_capacity * (len(queries)+1) > 250000:
        raise NativeResourceLimitError("Connections observation storage exceeds its scalar limit.")
    ids = {entity["id"]: i for i, entity in enumerate(entities)}
    link_ids = {link["id"]: i for i, link in enumerate(links)}
    connections = [Connection({"spring": 0, "rod": 1, "rope": 2}[link["type"]],
        *(ids[ident] for ident in link["entities"]), float(link["rest_length"]),
        float(link.get("stiffness", 0)), float(link.get("damping", 0))) for link in links]
    fields, _, masks = _force_fields(scene, [], [SimpleNamespace(ident=e["id"]) for e in entities])
    kinds = {"centroid": 0, "speed": 1, "center_distance": 2, "connection_length": 3,
             "connection_extension": 4, "spring_force": 5, "spring_energy": 6}
    metrics = []
    for query in queries:
        metric = query["metric"]
        kind = kinds[metric["type"]]
        if kind >= 3:
            a = b = link_ids[metric["connection"]]
        elif kind == 2:
            a, b = (ids[ident] for ident in metric["entities"])
        else:
            a = b = ids[metric["entity"]]
        metrics.append(Metric(kind, a, b, "xyz".index(metric.get("axis", "x"))))
    return Simulation(abi=ABI_VERSION, nodes=len(entities), connections=len(links), fields=len(fields),
        metrics=len(metrics), frame_capacity=frame_capacity, observation_capacity=observation_capacity,
        iterations=int(plan["connection_iterations"]), substeps=int(plan["connection_substeps"]),
        dt=dt, duration=duration, fps=fps, deadline_seconds=deadline_seconds,
        positions=_array(C.c_double, [float(x) for e in entities for x in e["position"]]),
        velocities=_array(C.c_double, [float(x) for e in entities for x in e["velocity"]]),
        mass=_array(C.c_double, [float(e["mass"]) for e in entities]),
        fixed=_array(C.c_int32, [int(e["fixed"]) for e in entities]),
        field_mask=_array(C.c_uint32, masks), connection=_array(Connection, connections),
        field=_array(CForceField, fields), metric=_array(Metric, metrics),
        frames=(C.c_double * (frame_capacity*len(entities)*3))(),
        frame_times=(C.c_double * frame_capacity)(),
        observation_times=(C.c_double * max(1, observation_capacity))(),
        observation_values=(C.c_double * max(1, observation_capacity*len(metrics)))())


def run_scene_connections(scene: dict[str, Any], plan: dict[str, Any], deadline: float) -> dict[str, Any]:
    started = time.monotonic()
    library, compile_seconds, build = _load_library(deadline)
    simulation = _pack(scene, plan, deadline-time.monotonic())
    simulation.deadline_seconds = deadline-time.monotonic()
    diag = Diagnostics()
    status = int(library.connections_simulate(C.byref(simulation), C.byref(diag)))
    if status in {1, 2, 5}:
        raise NativeResourceLimitError("Connections native solver returned " + STATUS_NAMES[status] + ".")
    if status not in {0, 3, 4}:
        raise NativeSimulationError("Unexpected connections native status: " + str(status))
    diagnostics = {name: getattr(diag, name) for name, _ in Diagnostics._fields_
                   if name not in {"status", "frames_written", "observations_written"}}
    energy_applicable = (not scene.get("force_fields") and
        all(e["type"] == "spring" and e.get("damping", 0) == 0 for e in scene["connections"]))
    diagnostics.update(finite=bool(diag.finite), completed=bool(diag.completed), backend="native-c11-connections",
        native_status=STATUS_NAMES[status], native_build=build, native_compile_s=compile_seconds,
        bridge_runtime_s=time.monotonic()-started, particle_count=0, render_particle_count=0,
        force_field_count=len(scene.get("force_fields", [])), nbody_invariants_applicable=False,
        nbody_invariants_conserved=False, nbody_invariant_exclusion_reason="connection_network",
        nbody_relative_energy_drift=None, nbody_momentum_drift=None, nbody_momentum_tolerance=None,
        threads_used=1, state_precision="float64", frame_precision="float64",
        connection_count=len(scene["connections"]), connection_energy_conservation_applicable=energy_applicable,
        connection_energy_note="Kinetic plus spring energy only; external field potential and work are excluded. "
                               "Rope engagement and distance projection can dissipate energy. "
                               "Peak relative drift uses max(initial energy, 1e-12 J); conservation is required "
                               "only when connection_energy_conservation_applicable is true.",
        solver_features=["hooke-verlet", "exact-pair-dashpot-splitting", "shake-rod-position", "rattle-rod-velocity",
                         "inelastic-tension-only-rope", "force-field-event-splitting"])
    frames = [{"t": float(simulation.frame_times[j]), "p": [], "r": [],
               "g": [[float(simulation.frames[(j*simulation.nodes+i)*3+k]) for k in range(3)]
                     for i in range(simulation.nodes)]} for j in range(diag.frames_written)]
    result = {"frames": frames, "particle_materials": [], "particle_groups": [], "particle_radius": 0,
              "gravity_body_ids": [entity["id"] for entity in scene["entities"]],
              "rigid_ids": [], "rigid_shapes": [], "diagnostics": diagnostics}
    if simulation.metrics:
        result["observations"] = unpack_observations(
            [query["id"] for query in scene["queries"]], simulation.observation_times,
            simulation.observation_values, diag.observations_written)
    return result
