"""Reproducible predeclared-query refinement checks, with independent runs.

Usage: PYTHONPATH=. python3 benchmarks/benchmark_queries.py --out /tmp/query-audit
Each baseline and dt/2 variant is prepared separately; no hidden retry or
accuracy certificate is inferred from a single refinement comparison.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import platform

from physics_demo.runner import prepare, query, simulate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--video", action="store_true", help="Encode both baseline and refined MP4s as well")
    parser.add_argument("--scenes", nargs="+", choices=["three_body_queries", "droplet_radius_query", "sliders_separate"],
                        default=["three_body_queries", "droplet_radius_query", "sliders_separate"])
    args = parser.parse_args()
    output = Path(args.out).resolve()
    output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    records = []
    for name in args.scenes:
        original = json.loads((root / "examples" / (name + ".json")).read_text())
        runs = []
        for variant, scale in (("baseline", 1), ("half_dt", 0.5)):
            scene = copy.deepcopy(original)
            scene["world"]["dt"] *= scale
            preflight = prepare(scene)
            summary = simulate(preflight["scene"], output / (name + "-" + variant), make_video=args.video) if preflight["ok"] else preflight
            retrieved = query(output / (name + "-" + variant)) if summary["ok"] else summary
            runs.append({"variant": variant, "dt_s": scene["world"]["dt"], "ok": retrieved["ok"],
                         "wall_time_s": summary.get("total_runtime_s"), "errors": retrieved.get("errors", []),
                         "answers": retrieved.get("answers", [])})
        differences = []
        if all(run["ok"] for run in runs):
            for a, b in zip(runs[0]["answers"], runs[1]["answers"]):
                comparison = {"id": a["id"], "status_unchanged": a["status"] == b["status"]}
                if a["type"] == "threshold" and a.get("time_s") is not None and b.get("time_s") is not None:
                    comparison["time_difference_s"] = abs(a["time_s"] - b["time_s"])
                    comparison["baseline_bracket_s"] = a["time_bracket_s"]
                    comparison["refined_bracket_s"] = b["time_bracket_s"]
                    comparison["exact_time_error_s"] = abs(b["time_s"] - (2.0 if a["id"] == "separate-1m" else .002)) if name == "sliders_separate" else None
                if a["type"] == "nbody_stability":
                    comparison["energy_drift_reduction_factor"] = a["max_relative_energy_drift"] / max(b["max_relative_energy_drift"], 1e-30)
                    comparison["minimum_pair_distance_change_m"] = abs(a["min_observed_pair_distance_m"] - b["min_observed_pair_distance_m"])
                differences.append(comparison)
        record = {"scene": name, "runs": runs, "refinement": differences}
        records.append(record)
        print(name, "passed" if all(r["ok"] for r in runs) else "failed", flush=True)
    report = {"host": platform.platform(), "video_included": args.video, "records": records,
              "interpretation": "Differences measure sensitivity to dt on this model. They are not a certified temporal/spatial error bound or a physical calibration. Threshold brackets describe sampling only."}
    destination = output / "query-refinement.json"
    destination.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(destination)
    return 0 if all(r["ok"] for record in records for r in record["runs"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
