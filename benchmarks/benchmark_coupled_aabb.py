"""Time rigid/mesh triangle sweeps with sparse, near and dense candidates.

Run this script before and after a native broad-phase change on the same host.
Packing and compilation are outside the timed native solve. The reported
candidate count describes the initial triangle positions and rigid samples;
it is not a counter for every subsequent solver iteration.
"""
from __future__ import annotations

import argparse
import ctypes as C
import json
import math
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from physics_demo import coupled_solver as coupled  # noqa: E402
from physics_demo.native_backend import CDiagnostics  # noqa: E402


def cloth(size: float, divisions: int = 40) -> dict:
    """The same regular topology as the public cloth recipe, without audit time."""
    vertices = [[size * (i / divisions - .5), 0, size * (j / divisions - .5)]
                for i in range(divisions + 1) for j in range(divisions + 1)]
    triangles = []
    for i in range(divisions):
        for j in range(divisions):
            a = i * (divisions + 1) + j
            b, c, d = a + divisions + 1, a + divisions + 2, a + 1
            triangles.extend([[a, c, b], [a, d, c]])
    return {"vertices": vertices, "triangles": triangles}


def case(name: str) -> tuple[dict, dict]:
    if name == "sparse":
        size, thickness, center_y, height = 8.0, .015, .21, .4
    elif name == "near":
        size, thickness, center_y, height = .4, .25, .259, .02
    elif name == "dense":
        # All 24 samples fall inside every triangle's expanded AABB.
        size, thickness, center_y, height = .4, .5, .259, .02
    else:
        raise ValueError(name)
    scene = {
        "world": {"duration": .2, "dt": .005, "output_fps": 10,
                  "gravity": [0, 0, 0],
                  "bounds": {"min": [-5, -2, -5], "max": [5, 2, 5]}},
        "entities": [
            {"id": "body", "type": "rigid_body", "shape": {"type": "cylinder", "radius": .2, "height": height},
             "position": [0, center_y, 0], "velocity": [0, 0, 0], "angular_velocity": [0, 0, 0],
             "orientation": [1, 0, 0, 0], "mass": 1, "fixed": True, "friction": 0, "restitution": 0},
            {"id": "sheet", "type": "mesh", "mesh": cloth(size),
             "position": [0, 0, 0], "velocity": [0, 0, 0], "motion": "static", "mass": 1,
             "thickness": thickness, "friction": 0},
        ],
        "coupling": {"substeps": 1, "iterations": 2, "friction": 0},
        "colliders": [], "connections": [], "force_fields": [], "queries": [],
        "interactions": {"mutual_gravity": False},
    }
    plan = {"effective_spacing": .1, "render_particle_limit": 1, "threads": 1,
            "density_iterations": 1, "divergence_iterations": 1, "max_substeps": 32,
            "coupling_substeps": 1, "coupling_iterations": 2, "mesh_substeps": 1, "mesh_iterations": 1}
    return scene, plan


def initial_candidates(scene: dict) -> tuple[int, int]:
    body, sheet = scene["entities"]
    vertices = sheet["mesh"]["vertices"]
    triangles = sheet["mesh"]["triangles"]
    radius = body["shape"]["radius"]
    half_height = body["shape"]["height"] / 2
    reach = sheet["thickness"]
    samples = [(radius * math.cos(2 * math.pi * (k % 12) / 12),
                body["position"][1] + (-half_height if k < 12 else half_height),
                radius * math.sin(2 * math.pi * (k % 12) / 12)) for k in range(24)]
    candidates = 0
    for p in samples:
        for ids in triangles:
            corners = [vertices[i] for i in ids]
            if all(min(v[j] for v in corners) - reach <= p[j] <= max(v[j] for v in corners) + reach
                   for j in range(3)):
                candidates += 1
    return candidates, len(samples) * len(triangles)


def benchmark(name: str, repeats: int, library: C.CDLL) -> dict:
    scene, plan = case(name)
    candidates, triangle_tests = initial_candidates(scene)
    timings = []
    diagnostic_values = []
    for _ in range(repeats):
        simulation, _ = coupled._pack(scene, plan, time.monotonic() + 60)
        diagnostic, particle_diagnostic, mesh_diagnostic = coupled.Diagnostics(), CDiagnostics(), coupled.mesh.Diagnostics()
        status = library.coupled_simulate(C.byref(simulation), C.byref(diagnostic),
                                          C.byref(particle_diagnostic), C.byref(mesh_diagnostic))
        if status != 0 or not diagnostic.completed or not diagnostic.finite:
            raise RuntimeError(f"{name}: status={status}, completed={diagnostic.completed}, finite={diagnostic.finite}")
        timings.append(diagnostic.runtime_s)
        diagnostic_values.append((diagnostic.contact_count, diagnostic.max_penetration_m,
                                  diagnostic.simulated_time_s))
    if any(value != diagnostic_values[0] for value in diagnostic_values):
        raise RuntimeError(f"{name}: diagnostics varied between identical runs")
    return {"case": name, "triangles": len(scene["entities"][1]["mesh"]["triangles"]),
            "samples": 24, "initial_aabb_candidates": candidates, "initial_triangle_tests": triangle_tests,
            "initial_candidate_fraction": candidates / triangle_tests,
            "native_runtime_s": timings, "median_native_runtime_s": statistics.median(timings),
            "contact_count": diagnostic_values[0][0], "max_penetration_m": diagnostic_values[0][1],
            "simulated_time_s": diagnostic_values[0][2]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    library, _, build = coupled._load_library(time.monotonic() + 60)
    rows = [benchmark(name, args.repeats, library) for name in ("sparse", "near", "dense")]
    result = {"native_build": build, "results": rows}
    output = json.dumps(result, indent=2)
    if args.output:
        args.output.write_text(output + "\n")
    print(output)


if __name__ == "__main__":
    main()
