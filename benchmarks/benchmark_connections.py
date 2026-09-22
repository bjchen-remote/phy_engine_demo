"""Reproducible public-tool runs, including dt/2 and saved-result verification."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path

from physics_demo.catalog import example
from physics_demo.runner import inspect, prepare, simulate

NAMES = ("spring_oscillator", "damped_spring", "coupled_springs", "rod_pendulum",
         "rope_catch", "spring_chain", "spring_pendulum")


def benchmark(output: Path, video: bool, refine: bool) -> None:
    output.mkdir(parents=True, exist_ok=True)
    rows, comparisons = [], []
    for name in NAMES:
        results = []
        for variant in (["baseline", "half_dt"] if refine else ["baseline"]):
            scene = deepcopy(example(name)["scene"])
            if variant == "half_dt":
                scene["world"]["dt"] /= 2
            plan = prepare(scene)
            if not plan["ok"]:
                raise RuntimeError(plan)
            run = output / (name + "-" + variant)
            summary = simulate(scene, run, make_video=video)
            if not summary["ok"]:
                raise RuntimeError(summary)
            verified = inspect(run / "result.json")
            if not verified["ok"]:
                raise RuntimeError(verified)
            result = json.loads((run / "result.json").read_text())
            results.append(result)
            rows.append({"name": name, "variant": variant, "ok": True,
                         "physical_duration_s": scene["world"]["duration"],
                         "dt_s": scene["world"]["dt"],
                         "wall_s": summary["total_runtime_s"],
                         "solver_s": summary["runtime_s"],
                         "estimated_p90_s": plan["plan"]["timing_estimate"]["total_p90_s"],
                         "diagnostics": summary["diagnostics"],
                         "result": str(run / "result.json")})
            print(name, variant, round(summary["total_runtime_s"], 3), "s", flush=True)
            (output / "benchmark.json").write_text(json.dumps(rows, indent=2) + "\n")
        if refine:
            base, fine = [item["trajectory"] for item in results]
            displacement = max(math.dist(a, b) for f, g in zip(base["frames"], fine["frames"])
                               for a, b in zip(f["g"], g["g"]))
            query_difference = {ident: max(abs(a-b) for a, b in zip(values, fine["observations"]["columns"][ident][::2]))
                                for ident, values in base["observations"]["columns"].items()}
            comparisons.append({"name": name, "maximum_display_position_difference_m": displacement,
                                "maximum_query_difference_at_common_samples": query_difference,
                                "interpretation": "Timestep sensitivity, not a certified error bound; rope engagement is discontinuous."})
            (output / "refinement-comparison.json").write_text(json.dumps(comparisons, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--video", action="store_true")
    parser.add_argument("--refine", action="store_true")
    args = parser.parse_args()
    benchmark(args.out.resolve(), args.video, args.refine)
