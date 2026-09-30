#!/usr/bin/env python3
"""Exercise a composed pipeline in the QQ macOS sandbox, without sending messages."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
import zipfile

from build_pipeline import build
from registry import manifest, publish, read_json, sha256, verify, write_json
from smoke import execute
from dev_host import HostSourceUnavailable, activate_host_source, resolve_host_source


def call(package: Path, job: Path, operation: str, arguments: dict) -> dict:
    write_json(job / "work/toolbox-call.json", {"schema_version": 1,
               "operation": operation, "arguments": arguments})
    execute(package, job, "api", 15)
    return read_json(job / "work/toolbox-response.json")["result"]


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def exercise(package: Path, destination: Path, domain: str, backend: str = "native",
             *, validate_delivery) -> dict:
    job = destination / domain
    (job / "work/tmp").mkdir(parents=True)
    (job / "artifacts").mkdir()
    task = {"schema_version": 1, "task_id": "pipeline-" + domain,
        "request": {"text": "模拟并交付视频和可提取数据", "plan_required": True,
                    "trust": "untrusted_external_input"},
        "limits": {"wall_time_seconds": 60, "max_output_bytes": 16 * 1024 * 1024, "network": False},
        "delivery": {"data_attachments": True, "max_data_bytes": 16 * 1024 * 1024},
        "output_dir": "artifacts", "work_dir": "work"}
    write_json(job / "task.json", task)
    found = call(package, job, "pipeline_capabilities", {})
    require(found["engine"]["version"] == manifest(package / "engine")["version"], "wrong engine")
    require(not call(package, job, "pipeline_configure", {"rendering_quality": "deep"})["ok"],
            "uninstalled deep renderer must be explicit")
    configured = call(package, job, "pipeline_configure", {"simulation_quality": "strict"})
    require(configured["ok"], "configure failed")
    if domain == "physics":
        model = json.loads((package / "engine/physics_release/scenes/three_body.json").read_text())
        model["world"].update(duration=1.0, dt=0.005, output_fps=30)
        model["budget"].update(backend=backend, validation="strict", wall_time_s=60)
        model["queries"] = [{"id": "body_a_x", "type": "series",
            "metric": {"type": "centroid", "entity": "body-a", "axis": "x"}}]
        operation, argument = "physics_prepare", "scene_json"
    else:
        model = json.loads((package / "engine/pcb_thermal_demo.json").read_text())
        model["grid"] = {"nx": 24, "ny": 20}
        model["transient"] = {"duration_s": 20, "time_step_s": 0.5,
                              "initial_c": 25, "snapshot_count": 10}
        operation, argument = "pcb_prepare", "spec_json"
    prepared = call(package, job, operation, {argument: json.dumps(model)})
    require(prepared.get("ok") and prepared.get("ready_to_simulate"), "prepare failed: " + str(prepared))
    ready = call(package, job, "physics_simulate", {})
    require(ready.get("ready_to_run"), "execution handoff failed: " + str(ready))
    execute(package, job, "probe", 15)
    require(read_json(job / "capability.json")["supported"], "probe rejected")
    started = time.monotonic()
    execute(package, job, "run", 60)
    # Validate with the actual updated QQ host, including its inert ZIP checks.
    delivery = validate_delivery(job, task["limits"]["max_output_bytes"])
    require("data_attachment" in delivery, "data missing")
    state = call(package, job, "pipeline_status", {})["state"]
    source = Path(state["simulation"]["result_path"])
    before = (sha256(source), source.stat().st_mtime_ns)
    solver_response = job / "work/pipeline/calls/simulation-run-result.json"
    solver_before = (sha256(solver_response), solver_response.stat().st_mtime_ns)
    if domain == "physics":
        queried = call(package, job, "physics_query", {"query_id": "body_a_x"})
    else:
        queried = call(package, job, "pcb_query", {"x_m": 0.01, "y_m": 0.01})
    require(queried.get("ok"), "legacy query no longer works: " + str(queried))
    rerender = call(package, job, "pipeline_render_prepare", {"rendering_quality": "preview"})
    require(rerender.get("ok") and rerender["solver_rerun"] is False, "rerender unavailable")
    require(call(package, job, "physics_simulate", {}).get("ready_to_run"), "rerender handoff failed")
    execute(package, job, "run", 60)
    validate_delivery(job, task["limits"]["max_output_bytes"])
    require(before == (sha256(source), source.stat().st_mtime_ns), "rerender changed canonical result")
    require(solver_before == (sha256(solver_response), solver_response.stat().st_mtime_ns), "rerender called solver")
    with zipfile.ZipFile(job / "artifacts/data.zip") as archive:
        members = archive.namelist()
        require(any(name.endswith(".csv") for name in members), "CSV missing")
        require(archive.read("result.json") == source.read_bytes(), "ZIP result differs from canonical source")
    result = {"domain": domain, "backend": backend if domain == "physics" else "pcb_finite_volume",
        "passed": True, "elapsed_s": round(time.monotonic() - started, 3),
        "engine": found["engine"], "video": str(job / "artifacts/simulation.mp4"),
        "data": str(job / "artifacts/data.zip"), "members": members,
        "legacy_query_passed": True, "rerender_preserved_solver_and_source": True,
        "simulation_sha256": before[0], "host_delivery_validation": True,
        "live_qq_delivery": False}
    write_json(job / "acceptance.json", {"schema_version": 1, **result})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", choices=("native", "python"), default="native")
    parser.add_argument("--host-source", type=Path,
        help="Private QQ project root or src; defaults to QQ_SIMULATOR_HOST_SOURCE or a sibling checkout")
    args = parser.parse_args()
    try:
        host = resolve_host_source(args.host_source)
        activate_host_source(host)
    except HostSourceUnavailable as error:
        parser.error(str(error))
    from qq_simulator_agent.toolbox import validate_result
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    lock = build(args.engine, output / "source")
    pointer = publish(output / "source", output / "registry", True)
    package = verify(output / "registry", pointer)
    results = [exercise(package, output, domain, args.backend, validate_delivery=validate_result) for domain in ("physics", "pcb_thermal")]
    report = {"schema_version": 1, "ok": True, "bundle": lock, "pointer": pointer,
              "sandbox": "QQ macOS deny-network profile", "results": results}
    write_json(output / "acceptance.json", report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
