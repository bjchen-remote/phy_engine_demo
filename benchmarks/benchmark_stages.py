#!/usr/bin/env python3
"""Time the public simulation pipeline without changing its saved results.

Run from any directory, for example::

    python3 benchmarks/benchmark_stages.py examples/three_body.json
    python3 benchmarks/benchmark_stages.py examples/droplet_ground.json --video

Each sample uses a fresh temporary run directory. Native calls are timed at the
ctypes boundary. The time after that call includes frame conversion and native
diagnostic assembly; the engine does not expose a narrower frame-only boundary.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Callable
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from physics_demo import runner  # noqa: E402
from physics_demo.core import coupled_solver, connections_solver, mesh_solver, native_backend  # noqa: E402


class _NativeCallProxy:
    def __init__(self, library: Any, symbol: str, calls: list[dict[str, float | str]]):
        self._library = library
        self._symbol = symbol
        self._calls = calls

    def __getattr__(self, name: str) -> Any:
        function = getattr(self._library, name)
        if name != self._symbol:
            return function

        def timed(*args: Any) -> Any:
            started = time.perf_counter()
            try:
                return function(*args)
            finally:
                ended = time.perf_counter()
                self._calls.append({"symbol": name, "start": started, "end": ended})

        return timed


def _measure(scene: dict[str, Any], *, budget: float | None, video: bool) -> dict[str, Any]:
    phase: dict[str, float] = {}
    writes: list[dict[str, Any]] = []
    loads: list[dict[str, Any]] = []
    calls: list[dict[str, float | str]] = []

    def timed(name: str, function: Callable[..., Any]) -> Callable[..., Any]:
        def invoke(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                ended = time.perf_counter()
                phase[name] = phase.get(name, 0.0) + ended - started
                if name == "backend":
                    phase["backend_start"] = started
                    phase["backend_end"] = ended
        return invoke

    def timed_write(path: Path, value: Any) -> Any:
        started = time.perf_counter()
        try:
            return original_write(path, value)
        finally:
            elapsed = time.perf_counter() - started
            writes.append({"file": Path(path).name, "seconds": elapsed})

    def loader_wrapper(loader: Callable[..., Any], symbol: str) -> Callable[..., Any]:
        def invoke(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            library, compile_seconds, build = loader(*args, **kwargs)
            loads.append({
                "symbol": symbol,
                "seconds": time.perf_counter() - started,
                "reported_compile_seconds": compile_seconds,
            })
            return _NativeCallProxy(library, symbol, calls), compile_seconds, build
        return invoke

    original_write = runner._write_json
    loader_symbols = (
        (native_backend, "phy_simulate"),
        (mesh_solver, "mesh_simulate"),
        (connections_solver, "connections_simulate"),
        (coupled_solver, "coupled_simulate"),
    )
    with tempfile.TemporaryDirectory(prefix="physics-stage-benchmark-") as directory:
        with ExitStack() as stack:
            for module, symbol in loader_symbols:
                stack.enter_context(patch.object(
                    module, "_load_library", loader_wrapper(module._load_library, symbol)
                ))
            for attribute, label in (
                ("_validated_scene", "validation"),
                ("make_plan", "planning"),
                ("run_scene", "backend"),
                ("_summary", "quality_summary"),
                ("encode_mp4", "video_encode"),
            ):
                stack.enter_context(patch.object(runner, attribute, timed(label, getattr(runner, attribute))))
            stack.enter_context(patch.object(runner, "_write_json", timed_write))
            started = time.perf_counter()
            result = runner.simulate(scene, directory, budget_seconds=budget, make_video=video)
            elapsed = time.perf_counter() - started

    backend_start = phase.get("backend_start")
    backend_end = phase.get("backend_end")
    native_call = calls[0] if len(calls) == 1 else None
    native_before = native_after = native_seconds = None
    diagnostics = result.get("diagnostics") or {}
    plan = result.get("plan") or {}
    if (native_call is not None and backend_start is not None and backend_end is not None
            and backend_start <= float(native_call["start"]) <= float(native_call["end"]) <= backend_end):
        native_before = max(0.0, float(native_call["start"]) - backend_start)
        native_seconds = float(native_call["end"]) - float(native_call["start"])
        native_after = max(0.0, backend_end - float(native_call["end"]))
    write_seconds = sum(item["seconds"] for item in writes)
    classified = sum(phase.get(key, 0.0) for key in (
        "validation", "planning", "backend", "quality_summary", "video_encode"
    )) + write_seconds
    return {
        "ok": bool(result.get("ok")),
        "stage": result.get("stage"),
        "backend": diagnostics.get("backend"),
        "particle_count": diagnostics.get("particle_count"),
        "planned_frame_count": plan.get("output_frames"),
        "solver_reported_seconds": diagnostics.get("runtime_s"),
        "seconds": {
            "total": elapsed,
            "validation": phase.get("validation", 0.0),
            "planning": phase.get("planning", 0.0),
            "backend_total": phase.get("backend", 0.0),
            "before_native_call": native_before,
            "native_call": native_seconds,
            "after_native_call": native_after,
            "native_library_load": sum(item["seconds"] for item in loads),
            "json_persistence": write_seconds,
            "quality_summary": phase.get("quality_summary", 0.0),
            "video_encode": phase.get("video_encode", 0.0),
            "other": max(0.0, elapsed - classified),
        },
        "native_loads": loads,
        "json_writes": writes,
        "errors": result.get("errors", []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", type=Path, help="scene-v1 JSON file")
    parser.add_argument("--budget", type=float, help="override the scene wall-time budget")
    parser.add_argument("--video", action="store_true", help="include MP4 generation")
    parser.add_argument("--repeat", type=int, default=1, help="number of fresh-directory runs (1-20)")
    args = parser.parse_args()
    if not 1 <= args.repeat <= 20:
        parser.error("--repeat must be between 1 and 20")
    scene = runner.load_scene(args.scene)
    samples = [_measure(scene, budget=args.budget, video=args.video) for _ in range(args.repeat)]
    print(json.dumps({
        "scene": str(args.scene.resolve()),
        "video": args.video,
        "samples": samples,
        "notes": [
            "Validation and planning are the initial simulate calls; result verification can repeat them inside quality_summary.",
            "native_call times the outer ctypes solve call and includes its call overhead.",
            "before_native_call includes routing, buffer packing, compilation and library loading.",
        "after_native_call includes frame expansion, diagnostic assembly and return routing; it is not frame-only time.",
        "If a native attempt falls back to Python, after_native_call also includes the fallback solve.",
            "json_persistence sums all artifact JSON writes, including both result.json writes when present.",
        "video_encode includes renderer work and decode verification; the renderer reads result.json separately.",
        "Top-level phase values are wall-clock segments; native subsegments and native_library_load are nested in backend_total.",
        "Scene file reading, Python imports, and benchmark process startup are outside total.",
        ],
    }, ensure_ascii=False, indent=2))
    if not all(sample["ok"] for sample in samples):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
