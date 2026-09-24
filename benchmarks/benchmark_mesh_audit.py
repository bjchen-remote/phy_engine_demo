"""Time the bounded Python triangle-intersection audit without asset-build costs.

Run with python3 /path/to/repo/benchmarks/benchmark_mesh_audit.py --repeats 8.
The same command can be used on either side of an optimization commit.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from physics_demo.core import meshes  # noqa: E402


def ellipsoid(segments: int, rings: int):
    profile = [[0, -1]] + [[math.sin(math.pi * i / rings), -math.cos(math.pi * i / rings)]
                            for i in range(1, rings)] + [[0, 1]]
    return meshes._lathe(profile, segments)


def cloth(subdivisions: int):
    vertices = [[i / subdivisions - 0.5, 0, j / subdivisions - 0.5]
                for i in range(subdivisions + 1) for j in range(subdivisions + 1)]
    triangles = []
    for i in range(subdivisions):
        for j in range(subdivisions):
            a = i * (subdivisions + 1) + j
            b, c, d = a + subdivisions + 1, a + subdivisions + 2, a + 1
            triangles.extend([[a, c, b], [a, d, c]])
    return vertices, triangles


def measure(name, vertices, triangles, repeats):
    stats = {}
    if meshes._intersection_pair(vertices, triangles, stats=stats) is not None:
        raise AssertionError(f"{name} unexpectedly self-intersects")
    elapsed = []
    for _ in range(repeats):
        start = time.perf_counter()
        if meshes._intersection_pair(vertices, triangles) is not None:
            raise AssertionError(f"{name} unexpectedly self-intersects")
        elapsed.append(time.perf_counter() - start)
    return {"name": name, "vertices": len(vertices), "triangles": len(triangles),
            "node_tests": stats["node_tests"], "candidate_pairs": stats["candidate_pairs"],
            "exact_tests": stats["exact_tests"], "median_s": statistics.median(elapsed),
            "minimum_s": min(elapsed), "maximum_s": max(elapsed)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=8)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    cases = (("ellipsoid-24x12", ellipsoid(24, 12)),
             ("ellipsoid-48x24", ellipsoid(48, 24)),
             ("cloth-24", cloth(24)))
    print(json.dumps([measure(name, vertices, triangles, args.repeats)
                      for name, (vertices, triangles) in cases], indent=2))


if __name__ == "__main__":
    main()
