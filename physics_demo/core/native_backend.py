"""C11 DFSPH backend loaded through a single ctypes call.

Python owns scene validation, initial sampling, metadata and JSON.  The native
library owns the complete hot time-integration loop, including its persistent
worker pool and compact fixed-radius neighbor structure.
"""

from __future__ import annotations

import ctypes
from ctypes import POINTER, c_char_p, c_double, c_float, c_int32, c_uint32
import hashlib
import math
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import time
from typing import Any, Iterable, Optional

from physics_demo.core.engine import NBodyDiagnostics, _make_state
from physics_demo.core.state import GravityBody
from physics_demo.core.math3d import unit
from physics_demo.core.liquids import preserve_render_representatives, render_material_names
from physics_demo.analysis.observers import EntitySpec, metric_specs, unpack_observations


ABI_VERSION = 5
STATUS_NAMES = {
    0: "ok",
    1: "invalid_argument",
    2: "out_of_memory",
    3: "timed_out",
    4: "nonfinite",
    5: "internal_error",
}


class NativeBackendUnavailable(RuntimeError):
    pass


class NativeSimulationError(RuntimeError):
    pass


class NativeResourceLimitError(RuntimeError):
    """Native execution rejected a scene; a slower fallback is unsafe."""


class CCollider(ctypes.Structure):
    _fields_ = [
        ("type", c_int32),
        ("a", c_double * 3),
        ("b", c_double * 3),
        ("c", c_double * 3),
        ("friction", c_double),
    ]


class CForceField(ctypes.Structure):
    _fields_ = [
        ("type", c_int32),
        ("origin", c_double * 3),
        ("vector", c_double * 3),
        ("strength", c_double),
        ("radius", c_double),
        ("secondary_strength", c_double),
        ("start_time", c_double),
        ("end_time", c_double),
    ]


class CObservationEntity(ctypes.Structure):
    _fields_ = [
        ("kind", c_int32), ("start", c_int32), ("count", c_int32), ("shape", c_int32),
        ("position", c_double * 3), ("velocity", c_double * 3), ("size", c_double * 3),
    ]


class CObservationMetric(ctypes.Structure):
    _fields_ = [
        ("type", c_int32), ("axes", c_int32 * 2), ("origin", c_double * 3),
        ("a", CObservationEntity), ("b", CObservationEntity),
    ]


class CSimulation(ctypes.Structure):
    _fields_ = [
        ("abi_version", c_uint32),
        ("particle_count", c_int32),
        ("body_count", c_int32),
        ("collider_count", c_int32),
        ("field_count", c_int32),
        ("render_count", c_int32),
        ("frame_capacity", c_int32),
        ("density_iterations", c_int32),
        ("divergence_iterations", c_int32),
        ("thread_count", c_int32),
        ("max_substeps", c_int32),
        ("spacing", c_double),
        ("dt", c_double),
        ("duration", c_double),
        ("output_fps", c_double),
        ("gravity", c_double * 3),
        ("bounds_min", c_double * 3),
        ("bounds_max", c_double * 3),
        ("gravity_G", c_double),
        ("softening", c_double),
        ("water_sand_drag", c_double),
        ("wetting_rate", c_double),
        ("deadline_seconds", c_double),
        ("material", POINTER(c_int32)),
        ("x", POINTER(c_double)),
        ("y", POINTER(c_double)),
        ("z", POINTER(c_double)),
        ("vx", POINTER(c_double)),
        ("vy", POINTER(c_double)),
        ("vz", POINTER(c_double)),
        ("anchor_x", POINTER(c_double)),
        ("anchor_y", POINTER(c_double)),
        ("anchor_z", POINTER(c_double)),
        ("viscosity", POINTER(c_double)),
        ("surface_tension", POINTER(c_double)),
        ("friction", POINTER(c_double)),
        ("cohesion", POINTER(c_double)),
        ("wetness", POINTER(c_double)),
        ("body_fixed", POINTER(c_int32)),
        ("body_mass", POINTER(c_double)),
        ("body_x", POINTER(c_double)),
        ("body_y", POINTER(c_double)),
        ("body_z", POINTER(c_double)),
        ("body_vx", POINTER(c_double)),
        ("body_vy", POINTER(c_double)),
        ("body_vz", POINTER(c_double)),
        ("colliders", POINTER(CCollider)),
        ("fields", POINTER(CForceField)),
        ("particle_field_mask", POINTER(c_uint32)),
        ("body_field_mask", POINTER(c_uint32)),
        ("render_indices", POINTER(c_int32)),
        ("frame_particles", POINTER(c_float)),
        ("frame_bodies", POINTER(c_double)),
        ("frame_times", POINTER(c_double)),
        ("observation_metric_count", c_int32),
        ("observation_nbody", c_int32),
        ("observation_capacity", c_int32),
        ("observation_metrics", POINTER(CObservationMetric)),
        ("observation_times", POINTER(c_double)),
        ("observation_values", POINTER(c_double)),
        ("observation_nbody_values", POINTER(c_double)),
    ]


class CDiagnostics(ctypes.Structure):
    _fields_ = [
        ("status", c_int32),
        ("completed", c_int32),
        ("finite", c_int32),
        ("frames_written", c_int32),
        ("macro_steps", c_int32),
        ("substeps", c_int32),
        ("max_substeps_used", c_int32),
        ("max_neighbors", c_int32),
        ("threads_used", c_int32),
        ("density_iterations_total", c_int32),
        ("divergence_iterations_total", c_int32),
        ("density_iteration_limit_hits", c_int32),
        ("divergence_iteration_limit_hits", c_int32),
        ("cfl_limited_steps", c_int32),
        ("simulated_time_s", c_double),
        ("runtime_s", c_double),
        ("max_projection_correction_m", c_double),
        ("max_particle_contact_correction_m", c_double),
        ("peak_mean_density_excess", c_double),
        ("peak_mean_divergence_error", c_double),
        ("peak_max_density_error", c_double),
        ("peak_max_divergence_error", c_double),
        ("minimum_substep_s", c_double),
        ("maximum_particle_speed_m_s", c_double),
        ("mean_sand_displacement_m", c_double),
        ("mean_sand_wetness", c_double),
        ("final_mean_water_density_ratio", c_double),
        ("final_max_water_density_ratio", c_double),
        ("minimum_water_separation_ratio", c_double),
        ("water_separation_p01_ratio", c_double),
        ("close_water_particle_fraction", c_double),
        ("water_density_p50_ratio", c_double),
        ("water_density_p95_ratio", c_double),
        ("water_density_p99_ratio", c_double),
        ("planar_boundary_support_fraction", c_double),
        ("represented_water_volume_m3", c_double),
        ("observations_written", c_int32),
    ]


_LIBRARIES: dict[str, ctypes.CDLL] = {}


def _prebuilt_library() -> Optional[Path]:
    configured = os.environ.get("PHYSICS_DEMO_NATIVE_LIBRARY")
    if not configured:
        return None
    path = Path(configured).resolve()
    if not path.is_file():
        raise NativeBackendUnavailable(
            f"Configured prebuilt native library does not exist: {path}"
        )
    return path


def _native_sources() -> tuple[Path, Path]:
    directory = Path(__file__).resolve().parent / "native"
    return directory / "physics_native.c", directory / "physics_native.h"


def _compiler() -> str:
    configured = os.environ.get("PHYSICS_DEMO_CC")
    if configured:
        resolved = shutil.which(configured)
        if resolved:
            return resolved
    for name in ("clang", "cc", "gcc"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise NativeBackendUnavailable("No C11 compiler was found (tried clang, cc, and gcc).")


def _compile_library(deadline_seconds: float) -> tuple[Path, float]:
    prebuilt = _prebuilt_library()
    if prebuilt is not None:
        return prebuilt, 0.0
    source, header = _native_sources()
    compiler = _compiler()
    system = platform.system()
    flags = ["-std=c11", "-O3", "-DNDEBUG", "-fPIC", "-fvisibility=hidden", "-pthread"]
    if platform.machine() in {"arm64", "aarch64", "x86_64", "AMD64"}:
        flags.append("-mcpu=native" if platform.machine() in {"arm64", "aarch64"} else "-march=native")
    if system == "Darwin":
        flags.extend(["-dynamiclib", "-ffp-contract=off"])
        suffix = ".dylib"
    else:
        flags.extend(["-shared", "-fno-fast-math", "-lm"])
        suffix = ".so"
    if deadline_seconds <= 0.05:
        raise NativeBackendUnavailable("The native compilation deadline was exhausted before compiler discovery.")
    try:
        version_process = subprocess.run(
            [compiler, "--version"],
            capture_output=True,
            text=True,
            timeout=max(0.05, min(5.0, deadline_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise NativeBackendUnavailable("The C11 compiler version probe exceeded the remaining deadline.") from error
    except OSError as error:
        raise NativeBackendUnavailable(f"The configured C11 compiler could not be started: {error}") from error
    version_lines = version_process.stdout.strip().splitlines()
    if version_process.returncode != 0 or not version_lines:
        detail = version_process.stderr.strip().splitlines()
        message = detail[-1] if detail else "compiler returned no version string"
        raise NativeBackendUnavailable(f"The configured C11 compiler is unusable: {message}")
    compiler_version = version_lines[0]
    digest = hashlib.sha256()
    digest.update(source.read_bytes())
    digest.update(header.read_bytes())
    digest.update(compiler_version.encode("utf-8"))
    digest.update("\0".join(flags).encode("utf-8"))
    cache = Path(tempfile.gettempdir()) / "physics-agent-demo-native"
    cache.mkdir(parents=True, exist_ok=True)
    output = cache / f"libphysics_native-{digest.hexdigest()[:16]}{suffix}"
    if output.exists():
        return output, 0.0
    temporary = cache / f".{output.name}.{os.getpid()}.tmp"
    started = time.monotonic()
    timeout = max(0.05, min(30.0, deadline_seconds))
    try:
        process = subprocess.run(
            [compiler, *flags, str(source), "-o", str(temporary)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        temporary.unlink(missing_ok=True)
        raise NativeBackendUnavailable("C11 backend compilation exceeded the remaining deadline.") from error
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise NativeBackendUnavailable(f"The C11 compiler failed to start: {error}") from error
    elapsed = time.monotonic() - started
    if process.returncode != 0:
        temporary.unlink(missing_ok=True)
        message = process.stderr.strip().splitlines()
        raise NativeSimulationError(
            "C11 backend compilation failed: " + (message[-1] if message else "unknown compiler error")
        )
    try:
        os.replace(temporary, output)
    except OSError as error:
        # A compiler wrapper can report success without writing the requested
        # artifact.  Keep that environmental failure inside the documented
        # native-unavailable boundary instead of leaking a filesystem error.
        if not output.exists():
            temporary.unlink(missing_ok=True)
            raise NativeBackendUnavailable(
                "The C11 compiler reported success but produced no loadable library."
            ) from error
    return output, elapsed


def _load_library(deadline_seconds: float) -> tuple[ctypes.CDLL, float, str]:
    load_started = time.monotonic()
    path, compile_seconds = _compile_library(deadline_seconds)
    for attempt in range(2):
        key = str(path)
        library = _LIBRARIES.get(key)
        if library is not None:
            build = library.phy_build_string()
            if not build:
                raise NativeBackendUnavailable("The cached native library returned an empty build identifier.")
            return library, compile_seconds, build.decode("utf-8", "replace")
        try:
            library = ctypes.CDLL(key)
            library.phy_abi_version.argtypes = []
            library.phy_abi_version.restype = c_uint32
            library.phy_build_string.argtypes = []
            library.phy_build_string.restype = c_char_p
            library.phy_simulate.argtypes = [POINTER(CSimulation), POINTER(CDiagnostics)]
            library.phy_simulate.restype = c_int32
            if library.phy_abi_version() != ABI_VERSION:
                raise OSError("incompatible native ABI")
            build = library.phy_build_string()
            if not build:
                raise OSError("empty native build identifier")
        except (AttributeError, OSError) as error:
            if attempt == 0:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                remaining = deadline_seconds - (time.monotonic() - load_started)
                try:
                    path, rebuilt_seconds = _compile_library(remaining)
                    compile_seconds += rebuilt_seconds
                except (NativeBackendUnavailable, NativeSimulationError):
                    raise NativeBackendUnavailable(
                        f"The cached native library was invalid and could not be rebuilt: {error}"
                    ) from error
                continue
            raise NativeBackendUnavailable(f"The compiled native library could not be loaded: {error}") from error
        _LIBRARIES[key] = library
        return library, compile_seconds, build.decode("utf-8", "replace")
    raise NativeBackendUnavailable("The native library could not be loaded after one rebuild attempt.")


def native_build_info() -> dict[str, Any]:
    source, _ = _native_sources()
    return {
        "available": bool(source.exists() and any(shutil.which(name) for name in ("clang", "cc", "gcc"))),
        "language": "C11",
        "algorithm": "DFSPH density/divergence projection + analytic planar boundary volume + Akinci surface tension + short-range PBD anti-clumping + PBD granular contacts + analytic force fields",
        "precision": "float64 simulation state; float32 sampled video frames",
        "parallelism": "persistent pthread worker pool",
    }


def _array(kind: type, values: Iterable[Any], minimum: int = 1):
    items = list(values)
    array = (kind * max(minimum, len(items)))()
    array[:len(items)] = items
    return array


def _state_arrays(particles: list[Any], bodies: list[Any]) -> dict[str, Any]:
    """Own the C ABI's separate coordinate arrays for the duration of the call."""
    arrays = {
        name: _array(c_double, (getattr(particle, name) for particle in particles))
        for name in ("viscosity", "surface_tension", "friction", "cohesion", "wetness")
    }
    arrays["body_fixed"] = _array(c_int32, (int(body.fixed) for body in bodies))
    arrays["body_mass"] = _array(c_double, (body.mass for body in bodies))
    for records, attribute, prefix in (
        (particles, "pos", ""), (particles, "vel", "v"), (particles, "anchor", "anchor_"),
        (bodies, "pos", "body_"), (bodies, "vel", "body_v"),
    ):
        for axis, name in enumerate("xyz"):
            arrays[prefix + name] = _array(c_double, (getattr(item, attribute)[axis] for item in records))
    return arrays


def _frame_vectors(values: Any, frame: int, count: int, precision: int) -> list[list[float]]:
    start = frame * count * 3
    return [[round(float(values[offset + axis]), precision) for axis in range(3)]
            for offset in range(start, start + count * 3, 3)]


def _render_indices(
    materials: list[int],
    limit: int,
    *,
    groups: list[str] | None = None,
    visual_materials: list[str] | None = None,
) -> list[int]:
    count = len(materials)
    if count <= limit:
        return list(range(count))
    # Proportional deterministic sampling keeps both water and sand visible.
    selected: list[int] = []
    for material in sorted(set(materials)):
        members = [index for index, value in enumerate(materials) if value == material]
        quota = max(1, round(limit * len(members) / count))
        quota = min(quota, len(members))
        if quota == 1:
            selected.append(members[len(members) // 2])
        else:
            selected.extend(members[round(i * (len(members) - 1) / (quota - 1))] for i in range(quota))
    selected = sorted(set(selected))
    if len(selected) > limit:
        selected = selected[:limit]
    elif len(selected) < limit:
        chosen = set(selected)
        selected.extend(index for index in range(count) if index not in chosen and len(selected) < limit)
        selected.sort()
    return preserve_render_representatives(
        selected,
        count,
        limit,
        groups=groups,
        visual_materials=visual_materials,
    )


def _colliders(scene: dict[str, Any], rigids: list[Any]) -> list[CCollider]:
    values: list[CCollider] = []
    definitions = list(scene["colliders"])
    for rigid in rigids:
        if rigid.mass > 0.0:
            raise NativeBackendUnavailable("The native backend currently accepts static rigid spheres/boxes only.")
        definition = {"type": rigid.shape["type"], "center": rigid.pos, **rigid.shape}
        definitions.append(definition)
    for definition in definitions:
        collider = CCollider()
        collider.friction = float(definition.get("friction", 0.2))
        kind = definition["type"]
        if kind == "plane":
            collider.type = 0
            normal = unit([float(value) for value in definition["normal"]], [0.0, 1.0, 0.0])
            collider.a[:] = normal
            collider.b[:] = [float(definition["offset"]), 0.0, 0.0]
            collider.c[:] = [float(definition.get("water_adhesion", 0.0)), 0.0, 0.0]
        elif kind == "sphere":
            collider.type = 1
            collider.a[:] = [float(value) for value in definition["center"]]
            collider.b[:] = [float(definition["radius"]), 0.0, 0.0]
        elif kind == "box":
            collider.type = 2
            collider.a[:] = [float(value) for value in definition["center"]]
            collider.b[:] = [float(value) for value in definition["size"]]
        else:
            collider.type = 3
            collider.a[:] = [float(value) for value in definition["a"]]
            collider.b[:] = [float(value) for value in definition["b"]]
            collider.c[:] = [float(definition["radius"]), 0.0, 0.0]
        values.append(collider)
    return values


def _force_fields(
    scene: dict[str, Any],
    particles: list[Any],
    gravity_bodies: list[Any],
) -> tuple[list[CForceField], list[int], list[int]]:
    values: list[CForceField] = []
    target_sets: list[set[str]] = []
    for definition in scene.get("force_fields", []):
        field = CForceField()
        kind = definition["type"]
        field.start_time = float(definition["start_time"])
        field.end_time = float(definition["end_time"])
        if kind == "uniform":
            field.type = 0
            field.vector[:] = [float(value) for value in definition["acceleration"]]
        elif kind == "radial":
            field.type = 1
            field.origin[:] = [float(value) for value in definition["center"]]
            field.strength = float(definition["strength"])
            field.radius = float(definition["radius"])
        else:
            field.type = 2
            field.origin[:] = [float(value) for value in definition["center"]]
            field.vector[:] = unit([float(value) for value in definition["axis"]], [0.0, 1.0, 0.0])
            field.strength = float(definition["strength"])
            field.radius = float(definition["radius"])
            field.secondary_strength = float(definition["inward_strength"])
        values.append(field)
        target_sets.append(set(definition["targets"]))

    def mask_for(identifier: str) -> int:
        mask = 0
        for index, targets in enumerate(target_sets):
            if identifier in targets:
                mask |= 1 << index
        return mask

    return (
        values,
        [mask_for(particle.group) for particle in particles],
        [mask_for(body.ident) for body in gravity_bodies],
    )


def _observation_entity(spec: EntitySpec) -> CObservationEntity:
    return CObservationEntity(
        kind=spec.kind, start=spec.start, count=spec.count, shape=spec.shape,
        position=(c_double * 3)(*spec.position), velocity=(c_double * 3)(*spec.velocity),
        size=(c_double * 3)(*spec.size),
    )


def run_scene_native(scene: dict[str, Any], plan: dict[str, Any], deadline: float) -> dict[str, Any]:
    bridge_started = time.monotonic()
    spacing = float(plan["effective_spacing"])
    particles, gravity_bodies, rigids = _make_state(scene, spacing)
    collider_values = _colliders(scene, rigids)
    field_values, particle_field_masks, body_field_masks = _force_fields(scene, particles, gravity_bodies)
    remaining = max(0.0, deadline - time.monotonic())
    library, compile_seconds, build_string = _load_library(remaining)

    particle_count = len(particles)
    body_count = len(gravity_bodies)
    materials = [0 if particle.material == "water" else 1 for particle in particles]
    render_indices = _render_indices(
        materials,
        int(plan.get("render_particle_limit", particle_count or 1)),
        groups=[particle.group for particle in particles],
        visual_materials=render_material_names(scene, particles),
    )
    render_count = len(render_indices)
    frame_capacity = int(plan["output_frames"])

    state_arrays = _state_arrays(particles, gravity_bodies)
    frame_particles = (c_float * max(1, frame_capacity * max(1, render_count) * 3))()
    frame_bodies = (c_double * max(1, frame_capacity * max(1, body_count) * 3))()
    frame_times = (c_double * frame_capacity)()
    specs = metric_specs(scene, particles, gravity_bodies, rigids)
    nbody_query_ids = [query["id"] for query in scene.get("queries", [])
                       if query["type"] == "nbody_stability"]
    observation_capacity = (math.ceil(float(scene["world"]["duration"]) /
                                     float(scene["world"]["dt"])) + 2) if scene.get("queries") else 0
    if len(scene.get("queries", [])) > 16 or observation_capacity > 250002:
        raise NativeResourceLimitError("Observation storage exceeds the bounded native sample allocation.")
    observation_metrics = (CObservationMetric * max(1, len(specs)))()
    for index, spec in enumerate(specs):
        observation_metrics[index] = CObservationMetric(
            type=spec.kind, axes=(c_int32 * 2)(*spec.axes), origin=(c_double * 3)(*spec.origin),
            a=_observation_entity(spec.a), b=_observation_entity(spec.b),
        )
    observation_times = (c_double * max(1, observation_capacity))()
    observation_values = (c_double * max(1, observation_capacity * len(specs)))()
    observation_nbody_values = (c_double * max(1, observation_capacity * 6 if nbody_query_ids else 0))()

    interactions = scene["interactions"]
    gravity_constant = float(interactions["gravity_G"]) if interactions["mutual_gravity"] else 0.0
    nbody_diagnostics = NBodyDiagnostics(scene, gravity_bodies)
    hardware_threads = os.cpu_count() or 1
    requested_threads = int(plan.get("threads", hardware_threads))
    # The persistent native pool amortizes dispatch well for capillary runs,
    # which perform many bounded substeps even below one thousand particles.
    # Keep truly tiny jobs serial but do not penalize fast millimetre previews.
    thread_count = 1 if particle_count < 384 else max(1, min(hardware_threads, requested_threads, 16))
    bounds = scene["world"]["bounds"]
    simulation = CSimulation(
        abi_version=ABI_VERSION,
        particle_count=particle_count,
        body_count=body_count,
        collider_count=len(collider_values),
        field_count=len(field_values),
        render_count=render_count,
        frame_capacity=frame_capacity,
        density_iterations=int(plan.get("density_iterations", plan["solver_iterations"])),
        divergence_iterations=int(plan.get("divergence_iterations", max(1, plan["solver_iterations"] // 2))),
        thread_count=thread_count,
        max_substeps=int(plan.get("max_substeps", 8)),
        spacing=spacing,
        dt=float(scene["world"]["dt"]),
        duration=float(scene["world"]["duration"]),
        output_fps=float(scene["world"]["output_fps"]),
        gravity=(c_double * 3)(*scene["world"]["gravity"]),
        bounds_min=(c_double * 3)(*bounds["min"]),
        bounds_max=(c_double * 3)(*bounds["max"]),
        gravity_G=gravity_constant,
        softening=float(interactions["softening"]),
        water_sand_drag=float(interactions["water_sand_drag"]),
        wetting_rate=float(interactions["wetting_rate"]),
        deadline_seconds=max(0.001, deadline - time.monotonic()),
        **state_arrays,
        material=_array(c_int32, materials),
        colliders=_array(CCollider, collider_values),
        fields=_array(CForceField, field_values),
        particle_field_mask=_array(c_uint32, particle_field_masks),
        body_field_mask=_array(c_uint32, body_field_masks),
        render_indices=_array(c_int32, render_indices),
        frame_particles=frame_particles,
        frame_bodies=frame_bodies,
        frame_times=frame_times,
        observation_metric_count=len(specs), observation_nbody=int(bool(nbody_query_ids)),
        observation_capacity=observation_capacity, observation_metrics=observation_metrics,
        observation_times=observation_times, observation_values=observation_values,
        observation_nbody_values=observation_nbody_values,
    )
    native_diagnostics = CDiagnostics()
    status = int(library.phy_simulate(ctypes.byref(simulation), ctypes.byref(native_diagnostics)))
    if status in {1, 2, 5}:
        raise NativeResourceLimitError(
            f"Native solver returned {STATUS_NAMES.get(status, f'status_{status}')}; "
            "the scene exceeded a native safety or resource limit."
        )
    if status not in {0, 3, 4}:
        raise NativeBackendUnavailable(
            f"Native solver returned {STATUS_NAMES.get(status, f'status_{status}')}."
        )

    frames: list[dict[str, Any]] = []
    rigid_positions = [[round(value, 5) for value in rigid.pos] for rigid in rigids]
    for frame_index in range(native_diagnostics.frames_written):
        frames.append({
            "t": round(float(frame_times[frame_index]), 7),
            "p": _frame_vectors(frame_particles, frame_index, render_count, 5),
            "g": _frame_vectors(frame_bodies, frame_index, body_count, 7),
            "r": rigid_positions,
        })

    final_bodies = [
        GravityBody(
            body.ident,
            [simulation.body_x[index], simulation.body_y[index], simulation.body_z[index]],
            [simulation.body_vx[index], simulation.body_vy[index], simulation.body_vz[index]],
            body.mass,
            body.fixed,
        )
        for index, body in enumerate(gravity_bodies)
    ]
    conservation = nbody_diagnostics.finish(final_bodies)

    bridge_seconds = time.monotonic() - bridge_started
    material_set = set(materials)
    if particle_count:
        backend_name = "native-c11-dfsph+pbd" if 1 in material_set else "native-c11-dfsph"
        if body_count:
            backend_name += "+verlet"
    else:
        backend_name = "native-c11-verlet"
    if field_values:
        backend_name += "+fields"
    diagnostics = {name: getattr(native_diagnostics, name) for name, _ in CDiagnostics._fields_
                   if name not in {"status", "frames_written", "observations_written", "macro_steps"}}
    diagnostics.update({
        "finite": bool(native_diagnostics.finite),
        "completed": bool(native_diagnostics.completed),
        "steps": native_diagnostics.macro_steps,
        "bridge_runtime_s": bridge_seconds,
        "native_compile_s": compile_seconds,
        "particle_count": particle_count,
        "render_particle_count": render_count,
        "density_iteration_limit_fraction": native_diagnostics.density_iteration_limit_hits / max(native_diagnostics.substeps, 1),
        "divergence_iteration_limit_fraction": native_diagnostics.divergence_iteration_limit_hits / max(native_diagnostics.substeps, 1),
        **conservation,
        "force_field_count": len(field_values),
        "backend": backend_name,
        "native_status": STATUS_NAMES.get(status, f"status_{status}"),
        "native_build": build_string,
        "state_precision": "float64",
        "frame_precision": "float32",
        "solver_features": [
            "dfsph-density-divergence",
            "analytic-plane-boundary-volume",
            "analytic-plane-water-adhesion",
            "akinci-surface-tension",
            "capillary-timestep",
            "short-range-pbd-anti-clumping",
            "time-consistent-xsph",
            "time-consistent-implicit-sand-cohesion",
            "force-window-event-substeps",
            "adaptive-softened-gravity",
            "deadline-aware-neighbor-build",
            "positional-collider-friction",
            "swept-box-contact",
        ],
        "boundary_pressure_coupling": "explicit plane colliders provide pressure support and optional water adhesion; sphere, box, capsule, and world bounds use positional projection",
    })
    selected_particles = [particles[index] for index in render_indices]
    observations = {}
    if scene.get("queries"):
        count = native_diagnostics.observations_written
        nbody = {
            "max_com_radius": [float(observation_nbody_values[index * 6]) for index in range(count)],
            "min_pair_distance": [float(observation_nbody_values[index * 6 + 1]) for index in range(count)],
            "energy": [float(observation_nbody_values[index * 6 + 2]) for index in range(count)],
            "momentum": [[float(observation_nbody_values[index * 6 + 3 + axis]) for axis in range(3)]
                         for index in range(count)],
        } if nbody_query_ids else {}
        observations = unpack_observations(
            [spec.query_id for spec in specs], observation_times, observation_values, count)
        observations["nbody"] = {identifier: nbody for identifier in nbody_query_ids}
    return {
        "frames": frames,
        "particle_materials": render_material_names(scene, selected_particles),
        "particle_groups": [particle.group for particle in selected_particles],
        "particle_radius": 0.46 * spacing,
        "gravity_body_ids": [body.ident for body in gravity_bodies],
        "rigid_ids": [body.ident for body in rigids],
        "rigid_shapes": [body.shape for body in rigids],
        "diagnostics": diagnostics,
        **({"observations": observations} if scene.get("queries") else {}),
    }
