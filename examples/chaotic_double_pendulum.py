"""Build and run a large-angle double pendulum through the public API.

From the project directory:
    PYTHONPATH=. python3 examples/chaotic_double_pendulum.py --out runs/double-pendulum

A large-angle trajectory alone does not demonstrate chaos; compare nearby
initial conditions and time-step refinement before interpreting sensitivity.
"""
from __future__ import annotations

import argparse
import json

from physics_demo import build_system, load_run, run_system
from physics_demo.analysis import pendulum_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="Dedicated run output directory")
    parser.add_argument("--duration", type=float, default=8.0, help="Physical duration in seconds")
    parser.add_argument("--dt", type=float, default=.002, help="Macro timestep in seconds")
    args = parser.parse_args()
    spec = {"type": "double_pendulum", "lengths": [1, 1], "masses": [1, 1],
            "angles": [2, 2.4], "angular_velocities": [0, 0], "gravity": 9.81,
            "duration": args.duration, "dt": args.dt, "output_fps": 30,
            "quality": "balanced", "wall_time_s": 60}
    scene = build_system(spec)
    print(json.dumps({"system": spec["type"], "declared_queries": len(scene["queries"])}))
    summary = run_system(spec, args.out)
    if not summary.get("ok"):
        print(json.dumps(summary, indent=2))
        return 1
    run = load_run(args.out)
    report = pendulum_report(run)
    print(json.dumps({"final_bob2": run.state("bob2"),
                      "peak_energy_drift_fraction_of_scale": report["peak_energy_drift_fraction_of_scale"],
                      "maximum_rod_length_error_m": report["maximum_rod_length_error_m"],
                      "interpretation": report["interpretation"],
                      "video": summary["artifacts"]["video"]["path"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
