"""Thirty deterministic mesh contact cases through prepare/simulate/inspect.

This matrix checks bounded behavior across resolution, quality and material choices;
it is neither a speed claim nor physical calibration. It never relaxes quality gates.
Run with PYTHONPATH=. python3 benchmarks/stress_meshes.py --out /tmp/mesh-stress.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import platform
import time

from physics_demo.jsonio import write
from physics_demo.meshes import build_mesh
from physics_demo.runner import inspect, prepare, simulate


def cases():
    qualities = ("preview", "balanced", "high")
    materials = (("soft", 1e-4, 1), ("stiff", 1e-8, 1), ("heavy", 1e-6, 20))

    def scene(name, quality, entities, gravity, duration, colliders):
        return {"name": name, "world": {"gravity": gravity, "duration": duration,
                "dt": 1 / 90, "output_fps": 12, "bounds": {"min": [-3, -1, -3], "max": [3, 5, 3]}},
                "budget": {"wall_time_s": 55, "quality": quality, "backend": "auto"},
                "entities": entities, "colliders": colliders,
                "queries": [{"id": entity["id"] + "-volume", "type": "series",
                             "metric": {"type": "volume_ratio", "entity": entity["id"]}} for entity in entities]}

    for segments, rings in ((12, 6), (20, 10), (32, 16)):
        mesh = build_mesh({"type": "ellipsoid", "radii": [0.45] * 3, "segments": segments, "rings": rings})
        for quality in qualities:
            for material, compliance, mass in materials:
                name = f"drop-{segments}x{rings}-{quality}-{material}"
                entities = [{"id": "soft", "type": "mesh", "mesh": copy.deepcopy(mesh), "position": [0, 1.2, 0],
                             "edge_compliance": compliance, "mass": mass}]
                yield name, {"load": "floor_drop", "resolution": [segments, rings], "quality": quality,
                             "material": material, "mass": mass, "edge_compliance": compliance}, scene(
                    name, quality, entities, [0, -9.81, 0], 0.6,
                    [{"id": "floor", "type": "plane", "normal": [0, 1, 0], "offset": 0, "friction": 0.4}])

    mesh = build_mesh({"type": "ellipsoid", "radii": [0.25] * 3, "segments": 16, "rings": 8})
    for quality in qualities:
        name = "four-soft-contact-" + quality
        entities = [{"id": f"soft-{i}", "type": "mesh", "mesh": copy.deepcopy(mesh),
                     "position": [0.6 * x, 1.2, 0.6 * z], "velocity": [-0.9 * x, 0, -0.9 * z]}
                    for i, (x, z) in enumerate(((-1, -1), (-1, 1), (1, -1), (1, 1)))]
        yield name, {"load": "four_soft_contact", "resolution": [16, 8], "quality": quality,
                     "material": "default", "mass": 1, "edge_compliance": 1e-6}, scene(
            name, quality, entities, [0, 0, 0], 0.9, [])


def run_case(name, parameters, scene, output):
    started = time.monotonic()
    prepared = prepare(scene)
    prepare_time = time.monotonic() - started
    result, recovered, diagnostic, execution_plan = prepared, None, {}, {}
    simulation_time, inspection_time = 0.0, 0.0
    if prepared.get("ready_to_simulate"):
        started = time.monotonic()
        result = simulate(prepared["scene"], output / name, make_video=False)
        simulation_time = time.monotonic() - started
        artifact = result.get("artifacts", {}).get("result")
        if artifact:
            saved = json.loads(Path(artifact).read_text(encoding="utf-8"))
            diagnostic, execution_plan = saved["trajectory"]["diagnostics"], saved["plan"]
            started = time.monotonic()
            recovered = inspect(artifact)
            inspection_time = time.monotonic() - started
    plan = execution_plan or prepared.get("plan", {})
    contact_exercised = diagnostic.get("contact_count", 0) > 0
    return {"case": name, "parameters": parameters,
            "ok": bool(result.get("ok") and recovered and recovered.get("ok") and contact_exercised),
            "simulation_ok": result.get("ok", False), "inspect_ok": recovered.get("ok", False) if recovered else False,
            "contact_exercised": contact_exercised, "video_included": False,
            "vertices": plan.get("mesh_vertices"), "triangles": plan.get("mesh_triangles"),
            "estimated_p90_s": plan.get("timing_estimate", {}).get("total_p90_s"),
            "estimate_includes_video": not bool(execution_plan),
            "prepare_wall_s": prepare_time, "simulate_wall_s": simulation_time, "inspect_wall_s": inspection_time,
            "actual_runtime_s": result.get("total_runtime_s"), "solver_runtime_s": diagnostic.get("runtime_s"),
            "max_edge_strain": diagnostic.get("max_edge_strain"), "min_volume_ratio": diagnostic.get("min_volume_ratio"),
            "max_volume_ratio": diagnostic.get("max_volume_ratio"), "residual_penetration_m": diagnostic.get("residual_penetration_m"),
            "maximum_speed_m_s": diagnostic.get("maximum_speed_m_s"), "contact_count": diagnostic.get("contact_count"),
            "quality_gate": result.get("quality_gate"), "gate_failures": result.get("quality_gate", {}).get("failed_checks", []),
            "errors": result.get("errors", []), "inspect_errors": recovered.get("errors", []) if recovered else [],
            "artifacts": result.get("artifacts", {})}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="Dedicated output directory; existing runs are never overwritten")
    parser.add_argument("--report", help="Defaults to OUT/stress-results.json")
    args = parser.parse_args()
    output = Path(args.out).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    destination = Path(args.report).expanduser().resolve() if args.report else output / "stress-results.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    report = {"host": platform.platform(), "video_included": False, "requested_case_count": 30,
              "interpretation": "Deterministic contact stress matrix; timing excludes initial asset creation. Quality gates are unchanged, failures retained, no automatic retries or topology reduction. This does not certify physical accuracy or universal runtime.",
              "records": []}
    for name, parameters, scene in cases():
        try:
            row = run_case(name, parameters, scene, output)
        except Exception as error:
            row = {"case": name, "parameters": parameters, "ok": False,
                   "exception": type(error).__name__, "errors": [{"message": str(error)}]}
        report["records"].append(row)
        report["passed"] = sum(record["ok"] for record in report["records"])
        report["failed"] = len(report["records"]) - report["passed"]
        write(destination, report)
        print(json.dumps({key: row.get(key) for key in ("case", "ok", "actual_runtime_s", "solver_runtime_s", "contact_count", "gate_failures", "errors")}), flush=True)
    return 0 if report["passed"] == 30 else 2


if __name__ == "__main__":
    raise SystemExit(main())
