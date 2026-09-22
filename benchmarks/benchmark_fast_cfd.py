#!/usr/bin/env python3
"""Compare bounded native DFSPH profiles for an agent-facing water drop.

This is a product-path benchmark, not a claim of grid/particle convergence.
Every profile must pass the same finite-state and water-stability gates before
its timing is reported as eligible.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from physics_demo.runner import load_scene, simulate


PROFILES = {
    "balanced": {
        "quality": "balanced",
        "spacing": 0.0004,
        "duration": 0.20,
        "output_fps": 60,
    },
    "fast": {
        "quality": "balanced",
        "spacing": 0.0005,
        "duration": 0.20,
        "output_fps": 60,
    },
    "rapid": {
        "quality": "balanced",
        "spacing": 0.0006,
        "duration": 0.15,
        "output_fps": 60,
    },
}


def scene_for(name: str) -> dict:
    settings = PROFILES[name]
    scene = deepcopy(load_scene(ROOT / "examples" / "droplet_ground.json"))
    scene["name"] = f"fast-cfd-{name}"
    scene["budget"].update(
        backend="native", quality=settings["quality"], wall_time_s=30
    )
    scene["entities"][0]["spacing"] = settings["spacing"]
    scene["world"].update(
        duration=settings["duration"], output_fps=settings["output_fps"]
    )
    return scene


def run_profile(name: str, repeats: int) -> dict:
    samples = []
    representative = None
    for _ in range(repeats):
        with tempfile.TemporaryDirectory() as directory:
            started = time.monotonic()
            result = simulate(scene_for(name), directory, make_video=False)
            wall = time.monotonic() - started
        if not result.get("ok") or not result.get("quality_gate", {}).get("passed"):
            return {"profile": name, "eligible": False, "failure": result}
        representative = result
        samples.append(
            {
                "solver_s": result["runtime_s"],
                "end_to_end_s": wall,
            }
        )
    diagnostics = representative["diagnostics"]
    return {
        "profile": name,
        "eligible": True,
        "settings": PROFILES[name],
        "particles": representative["particle_count"],
        "solver_s_median": statistics.median(x["solver_s"] for x in samples),
        "end_to_end_s_median": statistics.median(
            x["end_to_end_s"] for x in samples
        ),
        "samples": samples,
        "stability": {
            "finite": diagnostics["finite"],
            "completed": diagnostics["completed"],
            "density_iteration_limit_fraction": diagnostics[
                "density_iteration_limit_fraction"
            ],
            "divergence_iteration_limit_fraction": diagnostics[
                "divergence_iteration_limit_fraction"
            ],
            "water_density_p99_ratio": diagnostics["water_density_p99_ratio"],
            "water_separation_p01_ratio": diagnostics[
                "water_separation_p01_ratio"
            ],
            "close_water_particle_fraction": diagnostics[
                "close_water_particle_fraction"
            ],
            "minimum_substep_s": diagnostics["minimum_substep_s"],
            "max_substeps_used": diagnostics["max_substeps_used"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error("--repeats must be in [1, 20]")

    # Warm the content-addressed native build before product-path timing.
    run_profile("fast", 1)
    profiles = [run_profile(name, args.repeats) for name in PROFILES]
    baseline = next(item for item in profiles if item["profile"] == "balanced")
    for item in profiles:
        if item.get("eligible") and baseline.get("eligible"):
            item["solver_speedup_vs_balanced"] = (
                baseline["solver_s_median"] / item["solver_s_median"]
            )
            item["end_to_end_speedup_vs_balanced"] = (
                baseline["end_to_end_s_median"] / item["end_to_end_s_median"]
            )
    payload = {
        "benchmark": "bounded native DFSPH water-drop profiles",
        "repeats": args.repeats,
        "profiles": profiles,
        "claim_boundary": (
            "Timing plus existing delivery stability gates; this does not establish "
            "particle convergence or calibrated engineering CFD accuracy."
        ),
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    print(encoded, end="")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
