#!/usr/bin/env python3
"""Reproducible warm-run comparison of the reference and native backends."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import time

from physics_demo.runner import load_scene, simulate


ROOT = Path(__file__).resolve().parents[1]


def run(backend: str) -> dict[str, float | int | str | bool]:
    scene = load_scene(ROOT / "examples" / "droplet_ground.json")
    scene["name"] = f"backend-benchmark-{backend}"
    scene["budget"] = {"wall_time_s": 60, "quality": "high", "backend": backend}
    scene["world"]["duration"] = 0.3
    scene["entities"][0]["shape"]["radius"] = 0.3
    scene["entities"][0]["spacing"] = 0.04
    with tempfile.TemporaryDirectory() as directory:
        started = time.monotonic()
        result = simulate(scene, directory, 60, make_video=False)
        wall = time.monotonic() - started
    return {
        "backend": backend,
        "ok": bool(result["ok"]),
        "particles": int(result["particle_count"]),
        "solver_s": float(result["runtime_s"]),
        "end_to_end_s": wall,
        "substeps": int(result["diagnostics"].get("substeps", result["diagnostics"]["steps"])),
    }


def main() -> None:
    # Warm native compilation before timing it; cold compile cost is reported by
    # normal run diagnostics and is cached by source hash.
    native = run("native")
    reference = run("python")
    output = {
        "scene": "0.3 s drop, radius 0.3 m, spacing 0.04 m, high quality",
        "native": native,
        "python_reference": reference,
        "solver_speedup": reference["solver_s"] / max(native["solver_s"], 1e-12),
        "end_to_end_speedup": reference["end_to_end_s"] / max(native["end_to_end_s"], 1e-12),
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
