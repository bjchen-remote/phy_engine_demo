"""Run the pinned solver once and publish data independently of any video."""
from __future__ import annotations

import csv
from pathlib import Path
import time

from .common import (artifact, check_budget, digest, engine_identity, engine_module,
                     failure, file_digest, new_output_directory, read_json, write_json)


def capabilities() -> dict:
    return {"protocol": "stage-module/1", "stage": "simulation",
            "operations": ["simulation.run"], "domains": ["physics", "pcb_thermal"],
            "input_schema": "pipeline-model/1", "output_schema": "pipeline-simulation/1",
            "video_required": False, "full_state": False,
            "data": ["sampled_state", "predeclared_solver_observations", "pcb_field_samples"],
            "limitations": ["Mechanical queries must be declared before modeling.prepare.",
                            "Saved display frames are sampled state, not restart checkpoints.",
                            "Quantitative usability requires the numerical quality gate."]}


def _spreadsheet_text(value: str) -> str:
    # Identifiers are user data. Spreadsheet programs must not interpret them
    # as formulas; numeric values retain their numeric signs unchanged.
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r", "\n") else value


def _mechanical_csv(path: Path, measurements: dict) -> None:
    observations = measurements["observations"]
    units = {answer["id"]: answer.get("unit", "") for answer in measurements["answers"]}
    with path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["query_id", "time_s", "value", "unit", "source"])
        for ident, values in observations["columns"].items():
            for stamp, value in zip(observations["times"], values):
                writer.writerow([_spreadsheet_text(ident), stamp, value, units[ident],
                                 "solver_macro_steps"])


def _pcb_csv(output: Path, result: dict) -> list[dict]:
    board, grid = result["board"], result["grid"]
    dx, dy = board["width_m"] / grid["nx"], board["height_m"] / grid["ny"]
    field_path = output / "temperature-field.csv"
    with field_path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cell_x", "cell_y", "x_m", "y_m", "time_s", "temperature_c", "power_w"])
        for j, row in enumerate(result["temperature_c"]):
            for i, value in enumerate(row):
                writer.writerow([i, j, (i + 0.5) * dx, (j + 0.5) * dy,
                                 result["duration_s"], value, result["power_w"][j][i]])
    series_path = output / "temperature-series.csv"
    with series_path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time_s", "min_temperature_c", "max_temperature_c"])
        for row in result["time_series"]:
            writer.writerow([row["time_s"], row["min_temperature_c"], row["max_temperature_c"]])
    return [artifact(field_path, output, "numerical_field", "text/csv"),
            artifact(series_path, output, "numerical_series", "text/csv")]


def run_simulation(model_bundle_path: str | Path, output_dir: str | Path,
                   budget_seconds: float | None = None, unlimited: bool = False) -> dict:
    """Consume one immutable model, run its engine, and freeze result references."""
    check_budget(budget_seconds, unlimited)
    bundle_path = Path(model_bundle_path)
    bundle = read_json(bundle_path)
    if bundle.get("schema_version") != "pipeline-model/1":
        return failure("unsupported_model_bundle", "Expected pipeline-model/1")
    model = bundle.get("model")
    if not isinstance(model, dict) or bundle.get("model_sha256") != digest(model):
        return failure("model_digest_mismatch", "The prepared model was changed")
    if bundle.get("source_engine") != engine_identity():
        return failure("engine_pin_mismatch", "Model and simulation must use the same pinned physics release")
    if bundle.get("quality") != "standard":
        return failure("unsupported_modeling_quality", "Unsupported prepared modeling quality")
    if bundle.get("preparation", {}).get("ready_to_simulate") is not True:
        return failure("model_not_ready", "The bundle does not contain a successful preparation")
    limits = bundle.get("execution_limits")
    if limits != {"budget_seconds": budget_seconds, "unlimited": unlimited}:
        return failure("execution_limits_mismatch",
                       "Execution limits must match the prepared model; prepare again to change them")
    domain = bundle.get("domain")
    if domain not in ("physics", "pcb_thermal"):
        return failure("unsupported_domain", "Unsupported simulation domain")
    output = new_output_directory(output_dir)
    copied_model = output / "model.json"
    artifacts = []
    started = time.monotonic()
    if domain == "physics":
        runner = engine_module("physics_demo.runner")
        # Keep result.json at the legacy query path. Do not add stage metadata
        # until the runner has checked its dedicated-directory file whitelist.
        raw_output = output
        summary = runner.simulate(model, raw_output, budget_seconds,
                                  make_video=False, unlimited=unlimited)
        result_path = raw_output / "result.json"
        if not result_path.is_file():
            return {**summary, "ok": False, "code": "simulation_failed",
                    "model_path": str(bundle_path.resolve())}
        # Reuse the simulator's persisted-result checks, including measurements
        # bound to model+observations, instead of trusting an adapter boolean.
        inspected = runner.inspect(result_path)
        returned_gate = summary.get("quality_gate", {})
        inspected_gate = inspected.get("quality_gate", {})
        succeeded = bool(summary.get("ok") and returned_gate.get("passed")
                         and inspected.get("ok") and inspected_gate.get("passed"))
        numerical_passed = bool(returned_gate.get("numerical_passed")
                                and inspected_gate.get("numerical_passed"))
        quality_gate = {**inspected_gate, "passed": succeeded,
                        "numerical_passed": numerical_passed}
        if not inspected.get("ok"):
            summary = inspected
        data_usable = bool(succeeded and numerical_passed)
        raw = read_json(result_path)
        frames = raw.get("trajectory", {}).get("frames", [])
        data = {"capability": "sampled_state", "full_state": False,
                "restart_checkpoint": False, "numerical_usable": data_usable,
                "sample_count": len(frames), "sampling_source": "solver_presentation_samples",
                "observations": {"declared": bool(model.get("queries")),
                                 "sampling_source": "solver_macro_steps" if model.get("queries") else None},
                "limitations": ["Display states can be decimated; no arbitrary full-state query or restart.",
                                "Only predeclared measurements have full macro-step observations."]}
        roles = {"result.json": "canonical_result", "scene.normalized.json": "solver_model",
                 "plan.json": "solver_plan", "summary.json": "verification",
                 "completion.json": "completion", "measurements.json": "solver_observations"}
        for name, role in roles.items():
            candidate = raw_output / name
            if candidate.is_file():
                artifacts.append(artifact(candidate, output, role))
        measurements_path = raw_output / "measurements.json"
        if data_usable and measurements_path.is_file():
            measurements = read_json(measurements_path)
            data["observations"]["sample_count"] = len(measurements["observations"]["times"])
            if measurements["observations"]["columns"]:
                csv_path = output / "measurements.csv"
                _mechanical_csv(csv_path, measurements)
                artifacts.append(artifact(csv_path, output, "numerical_series", "text/csv"))
        claims = summary.get("fidelity", raw.get("physics_claims", {}))
    else:
        try:
            raw = engine_module("pcb_thermal").simulate_pcb_thermal(model)
        except (ValueError, TypeError, KeyError, ArithmeticError) as error:
            return failure("pcb_simulation_failed", str(error), model_path=str(bundle_path.resolve()))
        result_path = output / "result.json"
        write_json(result_path, raw)
        runtime = time.monotonic() - started
        numerical_passed = raw.get("energy_balance", {}).get("passed") is True
        runtime_passed = budget_seconds is None or runtime <= budget_seconds
        succeeded = numerical_passed and runtime_passed
        data_usable = succeeded
        quality_gate = {"passed": succeeded, "numerical_passed": numerical_passed,
                        "requires_video": False, "validation_mode": "strict",
                        "numerical_checks": {"finite_temperature_and_energy_balance": numerical_passed},
                        "runtime_checks": {"wall_time_within_budget": runtime_passed}}
        summary = {"ok": succeeded, "quality_gate": quality_gate, "total_runtime_s": runtime,
                   "energy_balance": raw["energy_balance"], "solver": raw["solver"],
                   "warnings": raw["warnings"]}
        write_json(output / "summary.json", summary)
        artifacts.extend([artifact(result_path, output, "canonical_result"),
                          artifact(output / "summary.json", output, "verification")])
        if data_usable:
            artifacts.extend(_pcb_csv(output, raw))
        data = {"capability": "sampled_state", "full_state": False,
                "restart_checkpoint": False, "numerical_usable": data_usable,
                "sampling_source": "finite_volume_cells_and_solver_time_steps",
                "sample_count": len(raw["snapshots"]),
                "field": {"units": "degC", "grid": raw["grid"], "sampling": "cell_centers"},
                "observations": {"declared": True, "sample_count": len(raw["time_series"]),
                                 "sampling_source": "solver_time_steps"},
                "limitations": ["Thermal snapshots are sampled fields, not all transient solver states.",
                                "A passed energy balance is not a calibrated engineering error bound."]}
        claims = {"pcb_thermal": "2D effective in-plane finite-volume thermal sheet; prescribed powers, "
                  "convection and calibrated effective material coefficients are assumptions."}
    write_json(copied_model, bundle)
    artifacts.insert(0, artifact(copied_model, output, "canonical_model"))
    result_ref = artifact(result_path, output, "canonical_result")
    manifest = {"schema_version": "pipeline-simulation/1", "ok": succeeded, "domain": domain,
                "source_engine": engine_identity(), "model_bundle_sha256": file_digest(bundle_path),
                "model_sha256": bundle["model_sha256"], "model": artifacts[0],
                "result": result_ref, "quality_gate": quality_gate,
                "data_usable": data_usable, "data": data, "physics_claims": claims,
                "artifacts": artifacts}
    manifest_path = output / "simulation-manifest.json"
    write_json(manifest_path, manifest)
    return {"ok": succeeded, "domain": domain, "result_path": str(result_path),
            "model_path": str(copied_model), "manifest_path": str(manifest_path),
            "quality_gate": quality_gate, "summary": summary,
            "data_usable": data_usable, "data": data, "artifacts": artifacts}
