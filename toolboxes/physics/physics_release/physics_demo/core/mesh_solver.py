"""One C11 call for XPBD triangle surfaces; Python owns topology and artifacts.

The model combines compliant edge lengths, opposite-vertex bending springs,
and signed global volume for closed surfaces. It is an elastic surface proxy,
not tetrahedral FEM. Contact is vertex/triangle with swept moving triangles;
self collision, edge/edge CCD, cutting, and fracture are outside this model.
"""
from __future__ import annotations

import ctypes as C
import math
from pathlib import Path
import time
from typing import Any

from physics_demo.core.native_backend import (CCollider, NativeResourceLimitError,
                             NativeSimulationError, STATUS_NAMES, _array, _colliders)
from physics_demo.core.native_runtime import load_library
from physics_demo.core.math3d import cross as _cross
from physics_demo.analysis.observers import unpack_observations


class Object(C.Structure):
    _fields_ = [(name, C.c_int32) for name in
                ("start", "count", "triangle_start", "triangle_count", "motion", "closed")] + [
        ("velocity", C.c_double * 3)] + [(name, C.c_double) for name in
        ("compliance", "damping", "friction", "thickness", "rest_volume")]


class Edge(C.Structure):
    _fields_ = [("a", C.c_int32), ("b", C.c_int32), ("bending", C.c_int32), ("rest", C.c_double), ("compliance", C.c_double)]


class Metric(C.Structure):
    _fields_ = [(name, C.c_int32) for name in ("type", "a", "b", "axis0", "axis1")] + [("origin", C.c_double * 3)]


class Simulation(C.Structure):
    _fields_ = [("abi", C.c_uint32)] + [(name, C.c_int32) for name in (
        "vertices", "triangles", "objects", "edges", "colliders", "metrics",
        "frame_capacity", "observation_capacity", "iterations", "substeps")] + [
        (name, C.c_double) for name in ("dt", "duration", "fps", "deadline_seconds")] + [
        (name, C.c_double * 3) for name in ("gravity", "bounds_min", "bounds_max")] + [
        (name, C.POINTER(C.c_double)) for name in ("positions", "velocities", "inverse_mass")] + [
        ("indices", C.POINTER(C.c_int32)), ("object", C.POINTER(Object)), ("edge", C.POINTER(Edge)),
        ("collider", C.POINTER(CCollider)), ("metric", C.POINTER(Metric)),
        ("frames", C.POINTER(C.c_float))] + [(name, C.POINTER(C.c_double)) for name in (
        "frame_times", "observation_times", "observation_values")]


class Diagnostics(C.Structure):
    _fields_ = [(name, C.c_int32) for name in (
        "status", "completed", "finite", "frames_written", "observations_written", "steps", "substeps",
        "contact_count", "inverted_objects", "closed_objects", "max_substeps_used")] + [
        (name, C.c_double) for name in ("simulated_time_s", "runtime_s", "max_edge_strain",
        "min_volume_ratio", "max_volume_ratio", "residual_penetration_m", "max_contact_correction_m", "maximum_speed_m_s")]


_LIBRARIES: dict[str, C.CDLL] = {}


def _bind_library(library: C.CDLL) -> None:
    library.mesh_abi_version.argtypes = []
    library.mesh_abi_version.restype = C.c_uint32
    library.mesh_simulate.argtypes = [C.POINTER(Simulation), C.POINTER(Diagnostics)]
    library.mesh_simulate.restype = C.c_int32
    if library.mesh_abi_version() != 1:
        raise OSError("incompatible mesh ABI")


def _load_library(deadline: float) -> tuple[C.CDLL, float, str]:
    return load_library(
        Path(__file__).with_name("native") / "mesh_native.c",
        prefix="libmesh-", name="Mesh", deadline=deadline,
        libraries=_LIBRARIES, bind=_bind_library,
    )


def _topology(entity: dict[str, Any], start: int) -> tuple[list[Edge], list[float], bool, float]:
    vertices, triangles = entity["mesh"]["vertices"], entity["mesh"]["triangles"]
    neighbors: dict[tuple[int, int], list[int]] = {}
    area = [0.0] * len(vertices)
    volume = 0.0
    origin = vertices[0]
    for a, b, c in triangles:
        ab = [vertices[b][k]-vertices[a][k] for k in range(3)]
        ac = [vertices[c][k]-vertices[a][k] for k in range(3)]
        face_area = .5 * math.sqrt(sum(x*x for x in _cross(ab, ac)))
        for i in (a, b, c):
            area[i] += face_area / 3
        relative = [[vertices[i][k]-origin[k] for k in range(3)] for i in (a, b, c)]
        normal = _cross(relative[1], relative[2])
        volume += sum(relative[0][k]*normal[k] for k in range(3)) / 6
        for i, j, opposite in ((a, b, c), (b, c, a), (c, a, b)):
            neighbors.setdefault(tuple(sorted((i, j))), []).append(opposite)
    edges = []
    if entity.get("motion", "soft") == "soft":
        def edge(a: int, b: int, compliance: float, bending: int = 0) -> None:
            rest = math.dist(vertices[a], vertices[b])
            if rest > 1e-15:
                edges.append(Edge(start+a, start+b, bending, rest, compliance))
        for (a, b), opposite in sorted(neighbors.items()):
            edge(a, b, float(entity.get("edge_compliance", 1e-6)))
            if len(opposite) == 2:
                edge(*opposite, float(entity.get("bending_compliance", 1e-4)), bending=1)
    closed = all(len(opposite) == 2 for opposite in neighbors.values()) and abs(volume) > 1e-15
    return edges, area, closed, volume


def _pack(scene: dict[str, Any], plan: dict[str, Any], deadline: float) -> tuple[Simulation, list[dict[str, Any]]]:
    """Build owned native buffers for standalone or coupled surface stepping."""
    entities, world = scene["entities"], scene["world"]
    count = sum(len(entity["mesh"]["vertices"]) for entity in entities)
    triangles_count = sum(len(entity["mesh"]["triangles"]) for entity in entities)
    frame_intervals = float(world["duration"]) * float(world["output_fps"])
    if not math.isfinite(frame_intervals) or frame_intervals > 2400:
        raise NativeResourceLimitError("Mesh video frame count exceeds the native capacity.")
    frames_count = max(1, math.ceil(frame_intervals - 1e-10)) + 1
    observations_count = math.ceil(float(world["duration"]) / float(world["dt"])) + 2 if scene.get("queries") else 0
    if (not 3 <= count <= 12000 or not 1 <= triangles_count <= 24000 or not 1 <= len(entities) <= 16
            or frames_count * count > 4_000_000 or observations_count > 250002 or len(scene.get("queries", [])) > 16):
        raise NativeResourceLimitError("Mesh state, frame, or observation storage exceeds the native capacity.")
    positions, velocities, inverse_mass, indices, objects, edges, mesh_objects = [], [], [], [], [], [], []
    for entity in entities:
        start, triangle_start = len(inverse_mass), len(indices) // 3
        vertices, triangles = entity["mesh"]["vertices"], entity["mesh"]["triangles"]
        motion = {"soft": 0, "static": 1, "kinematic": 2}[entity.get("motion", "soft")]
        position, velocity = entity.get("position", [0, 0, 0]), entity.get("velocity", [0, 0, 0])
        mass = float(entity.get("mass", 1))
        if not math.isfinite(mass) or (motion == 0 and mass <= 0):
            raise NativeResourceLimitError("Soft mesh mass must be positive and finite.")
        if any(not math.isfinite(float(x)) for row in [position, velocity, *vertices] for x in row):
            raise NativeResourceLimitError("Mesh positions and velocities must be finite.")
        local_edges, areas, closed, volume = _topology(entity, start)
        edges.extend(local_edges)
        pins = set(entity.get("pinned_vertices", []))
        total_area = sum(areas)
        for index, vertex in enumerate(vertices):
            positions.extend(float(vertex[k]) + float(position[k]) for k in range(3))
            velocities.extend(float(x) for x in (velocity if motion == 2 or (motion == 0 and index not in pins) else [0, 0, 0]))
            inverse_mass.append(total_area / (mass * areas[index]) if motion == 0 and index not in pins and areas[index] > 0 and mass > 0 else 0)
        indices.extend(start + index for triangle in triangles for index in triangle)
        objects.append(Object(start, len(vertices), triangle_start, len(triangles), motion, int(closed),
            (C.c_double * 3)(*velocity), float(entity.get("volume_compliance", 1e-7)),
            float(entity.get("damping", .15)), float(entity.get("friction", .35)), float(entity.get("thickness", .015)), volume))
        mesh_objects.append({"id": entity["id"], "vertex_start": start, "vertex_count": len(vertices),
                             "triangles": triangles, "color": entity.get("color", [.25, .65, .9]),
                             "motion": entity.get("motion", "soft"), "closed": closed})
    ids = {entity["id"]: index for index, entity in enumerate(entities)}
    metrics = []
    kinds = {"spread_radius": 0, "centroid": 1, "speed": 2, "center_distance": 3,
             "volume_ratio": 4, "max_displacement": 5, "max_edge_strain": 6}
    for query in scene.get("queries", []):
        metric = query["metric"]
        kind = kinds[metric["type"]]
        selected = metric["entities"] if kind == 3 else [metric["entity"]]
        axes = ["xyz".index(x) for x in metric.get("plane", "xz")]
        if kind == 1:
            axes = ["xyz".index(metric["axis"]), 0]
        metrics.append(Metric(kind, ids[selected[0]], ids[selected[-1]], *axes,
                              (C.c_double * 3)(*metric.get("origin", [0, 0, 0]))))
    frame_data = (C.c_float * (frames_count * count * 3))()
    frame_times = (C.c_double * frames_count)()
    observation_times = (C.c_double * max(1, observations_count))()
    observation_values = (C.c_double * max(1, observations_count * len(metrics)))()
    simulation = Simulation(abi=1, vertices=count, triangles=triangles_count, objects=len(objects),
        edges=len(edges), colliders=len(scene.get("colliders", [])), metrics=len(metrics),
        frame_capacity=frames_count, observation_capacity=observations_count,
        iterations=int(plan.get("mesh_iterations", 6)), substeps=int(plan.get("mesh_substeps", 4)),
        dt=float(world["dt"]), duration=float(world["duration"]), fps=float(world["output_fps"]),
        deadline_seconds=deadline-time.monotonic(), gravity=(C.c_double * 3)(*world["gravity"]),
        bounds_min=(C.c_double * 3)(*world["bounds"]["min"]), bounds_max=(C.c_double * 3)(*world["bounds"]["max"]),
        positions=_array(C.c_double, positions), velocities=_array(C.c_double, velocities),
        inverse_mass=_array(C.c_double, inverse_mass), indices=_array(C.c_int32, indices),
        object=_array(Object, objects), edge=_array(Edge, edges), collider=_array(CCollider, _colliders(scene, [])),
        metric=_array(Metric, metrics), frames=frame_data, frame_times=frame_times,
        observation_times=observation_times, observation_values=observation_values)
    return simulation, mesh_objects


def run_scene_mesh(scene: dict[str, Any], plan: dict[str, Any], deadline: float) -> dict[str, Any]:
    started = time.monotonic()
    simulation, mesh_objects = _pack(scene, plan, deadline)
    library, compile_seconds, build = _load_library(deadline)
    simulation.deadline_seconds = deadline-time.monotonic()
    count, triangles_count = simulation.vertices, simulation.triangles
    frame_data, frame_times = simulation.frames, simulation.frame_times
    diag = Diagnostics()
    status = library.mesh_simulate(C.byref(simulation), C.byref(diag))
    if status in {1, 2}:
        raise NativeResourceLimitError("Mesh native solver returned " + STATUS_NAMES[status] + ".")
    if status not in {0, 3, 4}:
        raise NativeSimulationError("Unexpected mesh solver status: " + str(status))
    diagnostics = {name: getattr(diag, name) for name, _ in Diagnostics._fields_
                   if name not in {"status", "frames_written", "observations_written"}}
    diagnostics.update(finite=bool(diag.finite), completed=bool(diag.completed), backend="native-c11-xpbd-mesh",
        native_status=STATUS_NAMES[status], native_build=build, native_compile_s=compile_seconds,
        bridge_runtime_s=time.monotonic()-started, particle_count=0, render_particle_count=0,
        mesh_vertex_count=count, mesh_triangle_count=triangles_count, mesh_object_count=simulation.objects,
        force_field_count=0, nbody_invariants_applicable=False, nbody_invariants_conserved=False,
        threads_used=1, state_precision="float64", frame_precision="float32",
        solver_features=["xpbd-edge-length", "opposite-vertex-bending", "closed-signed-volume",
                         "bvh-vertex-triangle-contact", "moving-triangle-cubic-ccd", "barycentric-contact-mass"])
    frames = [{"t": float(frame_times[j]), "p": [], "g": [], "r": [],
               "m": [[float(frame_data[(j*count+i)*3+k]) for k in range(3)] for i in range(count)]}
              for j in range(diag.frames_written)]
    result = {"frames": frames, "particle_materials": [], "particle_groups": [], "particle_radius": 0,
              "gravity_body_ids": [], "rigid_ids": [], "rigid_shapes": [],
              "mesh_objects": mesh_objects, "diagnostics": diagnostics}
    if simulation.metrics:
        result["observations"] = unpack_observations(
            [query["id"] for query in scene["queries"]], simulation.observation_times,
            simulation.observation_values, diag.observations_written)
    return result
