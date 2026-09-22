"""Mesh customer scenes, optional dt/2 refinement and verified MP4 output."""
from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path
import time

from physics_demo.catalog import EXAMPLE_CATALOG, example
from physics_demo.runner import inspect, prepare, simulate


def compare_refinement(baseline: dict, half_dt: dict) -> dict:
    """Compare fixed-topology runs without truncating unmatched frames or samples."""
    coarse_scene, fine_scene = copy.deepcopy(baseline["scene"]), copy.deepcopy(half_dt["scene"])
    coarse_dt, fine_dt = coarse_scene["world"]["dt"], fine_scene["world"]["dt"]
    if not math.isclose(coarse_dt, 2 * fine_dt, rel_tol=1e-12, abs_tol=0):
        raise ValueError("Refinement requires exactly dt/2.")
    fine_scene["world"]["dt"] = coarse_dt
    if coarse_scene != fine_scene:
        raise ValueError("Refinement scenes must differ only in world.dt; topology and all material/solver settings stay fixed.")
    coarse, fine = baseline["trajectory"], half_dt["trajectory"]
    a_frames, b_frames = coarse["frames"], fine["frames"]
    if not a_frames or len(a_frames) != len(b_frames):
        raise ValueError("Video frame counts do not match.")
    squared_sum, maximum, samples = 0.0, 0.0, 0
    vertex_count = sum(len(entity["mesh"]["vertices"]) for entity in coarse_scene["entities"])
    for index in range(len(a_frames)):
        a, b = a_frames[index], b_frames[index]
        if not math.isclose(a["t"], b["t"], rel_tol=0, abs_tol=1e-9):
            raise ValueError(f"Video timestamp mismatch at frame {index}.")
        if len(a["m"]) != vertex_count or len(b["m"]) != vertex_count:
            raise ValueError(f"Video vertex count mismatch at frame {index}.")
        for i in range(vertex_count):
            distance = math.dist(a["m"][i], b["m"][i])
            if not math.isfinite(distance):
                raise ValueError("Video vertices contain nonfinite coordinates.")
            squared_sum += distance * distance
            maximum = max(maximum, distance)
            samples += 1
    comparisons, common_count = {}, 0
    if coarse_scene.get("queries"):
        a, b = coarse["observations"], fine["observations"]
        times, refined_times = a["times"], b["times"]
        for values in (times, refined_times):
            if not values or any(not math.isfinite(t) for t in values) or any(x >= y for x, y in zip(values, values[1:])):
                raise ValueError("Observation times must be finite and strictly increasing.")
        if abs(times[0] - refined_times[0]) > 1e-9 or abs(times[-1] - refined_times[-1]) > 1e-9:
            raise ValueError("Observation start/end times do not match.")
        declared = {query["id"] for query in coarse_scene["queries"]}
        if set(a["columns"]) != declared or set(b["columns"]) != declared:
            raise ValueError("Declared scalar columns do not match.")
        matching, j = [], 0
        for t in times:
            while j < len(refined_times) and refined_times[j] < t - 1e-9:
                j += 1
            if j == len(refined_times) or abs(refined_times[j] - t) > 1e-9:
                raise ValueError(f"No matching dt/2 macro sample for baseline time {t:g}.")
            matching.append(j)
        common_count = len(matching)
        for key, values in a["columns"].items():
            refined_values = b["columns"][key]
            if len(values) != len(times) or len(refined_values) != len(refined_times):
                raise ValueError(f"Scalar column {key!r} does not match its own time array.")
            if any(not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x) for x in values + refined_values):
                raise ValueError(f"Scalar column {key!r} is not finite numeric data.")
            comparisons[key] = {"common_macro_samples": common_count,
                                "maximum_absolute_difference": max(abs(values[i] - refined_values[j]) for i, j in enumerate(matching)),
                                "baseline_final": values[-1], "half_dt_final": refined_values[-1]}
    return {"comparison_status": "matched", "baseline_dt_s": coarse_dt, "half_dt_s": fine_dt,
            "matched_video_frames": len(a_frames), "vertices_per_frame": vertex_count,
            "compared_vertex_samples": samples, "common_macro_samples": common_count,
            "dt_comparison_max_vertex_difference_m": maximum,
            "dt_comparison_rms_vertex_difference_m": math.sqrt(squared_sum / samples), "queries": comparisons,
            "interpretation": "Two time steps measure sensitivity, not a convergence certificate or physical calibration. Vertex errors use all float32 display vertices at matching video times; scalar errors use float64 observations at matching macro times."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--video", action="store_true")
    parser.add_argument("--refine", action="store_true", help="Also run dt/2 without changing topology or substeps")
    parser.add_argument("--scenes", nargs="*", default=sorted(k for k in EXAMPLE_CATALOG if k.startswith("mesh_")))
    args = parser.parse_args()
    root = Path(args.out).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    rows, comparisons = [], []
    for name in args.scenes:
        paired = {}
        source = example(name)
        if not source["ok"]:
            raise ValueError(source["errors"])
        for mode in (("baseline", "half_dt") if args.refine else ("baseline",)):
            scene = copy.deepcopy(source["scene"])
            if mode == "half_dt":
                scene["world"]["dt"] *= 0.5
            prepared = prepare(scene)
            started = time.monotonic()
            result = simulate(prepared["scene"], root / (name + "-" + mode), make_video=args.video) if prepared.get("ready_to_simulate") else prepared
            recovered = inspect(root / (name + "-" + mode)) if result.get("ok") else None
            artifact = result.get("artifacts", {}).get("result")
            saved = json.loads(Path(artifact).read_text()) if artifact else None
            diagnostics = saved["trajectory"]["diagnostics"] if saved else {}
            if result.get("ok") and recovered and recovered.get("ok"):
                paired[mode] = saved
            row = {"example": name, "variant": mode, "ok": result.get("ok", False),
                   "recovered_ok": recovered.get("ok") if recovered else False,
                   "physical_dt_s": scene["world"]["dt"], "measured_pipeline_s": time.monotonic() - started,
                   "total_runtime_s": result.get("total_runtime_s"),
                   "plan": prepared.get("agent_report"), "diagnostics": diagnostics,
                   "quality_gate": result.get("quality_gate"), "errors": result.get("errors", []),
                   "answers": result.get("measurements", {}).get("answers", []), "artifacts": result.get("artifacts", {})}
            rows.append(row)
            (root / "benchmark.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False) + "\n")
            print(json.dumps({k: row[k] for k in ("example", "variant", "ok", "recovered_ok", "total_runtime_s", "errors")}), flush=True)
        if args.refine:
            try:
                if set(paired) != {"baseline", "half_dt"}:
                    raise ValueError("Both baseline and dt/2 must simulate and inspect successfully before comparison.")
                comparison = compare_refinement(paired["baseline"], paired["half_dt"])
            except (KeyError, TypeError, ValueError, OverflowError) as error:
                comparison = {"comparison_status": "unavailable", "reason": str(error)}
            comparisons.append({"example": name, **comparison})
            (root / "refinement-comparison.json").write_text(json.dumps(comparisons, indent=2, ensure_ascii=False) + "\n")
    return 0 if all(r["ok"] and r["recovered_ok"] for r in rows) and all(c["comparison_status"] == "matched" for c in comparisons) else 2


if __name__ == "__main__":
    raise SystemExit(main())
