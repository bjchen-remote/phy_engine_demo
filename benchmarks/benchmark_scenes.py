#!/usr/bin/env python3
"""Measure expanded canonical scenes with the production planning path."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import tempfile
import time

from physics_demo.runner import load_scene, simulate


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENES = [
    "geyser.json",
    "sand_blast.json",
    "whirlpool.json",
    "water_obstacle_course.json",
    "zero_g_droplet_collision.json",
]


def measure(name: str, budget: float) -> dict[str, object]:
    scene = load_scene(ROOT / "examples" / name)
    with tempfile.TemporaryDirectory() as directory:
        started = time.monotonic()
        result = simulate(scene, directory, budget, make_video=False)
        elapsed = time.monotonic() - started
    diagnostics = result.get("diagnostics", {})
    timing = result.get("timing_estimate", {})
    return {
        "scene": name,
        "ok": bool(result.get("ok")),
        "backend": diagnostics.get("backend"),
        "physical_duration_s": scene["world"]["duration"],
        "particles": diagnostics.get("particle_count"),
        "render_particles": diagnostics.get("render_particle_count"),
        "solver_s": diagnostics.get("runtime_s"),
        "end_to_end_s": elapsed,
        "estimated_p50_s": timing.get("total_p50_s"),
        "estimated_p90_s": timing.get("total_p90_s"),
        "substeps": diagnostics.get("substeps"),
        "minimum_substep_s": diagnostics.get("minimum_substep_s"),
        "maximum_particle_speed_m_s": diagnostics.get("maximum_particle_speed_m_s"),
        "water_separation_p01_ratio": diagnostics.get("water_separation_p01_ratio"),
        "close_water_particle_fraction": diagnostics.get("close_water_particle_fraction"),
        "water_density_p99_ratio": diagnostics.get("water_density_p99_ratio"),
        "density_iteration_limit_fraction": diagnostics.get("density_iteration_limit_fraction"),
        "max_particle_contact_correction_m": diagnostics.get("max_particle_contact_correction_m"),
        "completed": diagnostics.get("completed"),
        "finite": diagnostics.get("finite"),
        "quality_gate_passed": result.get("quality_gate", {}).get("passed"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenes", nargs="*", default=DEFAULT_SCENES)
    parser.add_argument("--budget", type=float, default=60.0)
    parser.add_argument("--output")
    args = parser.parse_args()
    result = {
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "measurement": "native production route, no video; current compiler-cache state",
        "budget_s_per_scene": args.budget,
        "scenes": [measure(name, args.budget) for name in args.scenes],
    }
    result["all_completed"] = all(
        item["ok"] and item["completed"] and item["finite"] and item["quality_gate_passed"]
        for item in result["scenes"]
    )
    encoded = json.dumps(result, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(encoded, encoding="utf-8")
    print(encoded, end="")


if __name__ == "__main__":
    main()
