"""Deterministic, bounded data delivery without invoking a numerical solver.

CSV tables expose saved numerical observations separately from decimated display
states. The original result remains the source of truth; its byte digest is
checked again before any deliverable is published.
"""

from __future__ import annotations

import csv
import hashlib
import importlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Any, Iterable
import zipfile


MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_EXPORT_BYTES = 128 * 1024 * 1024


class PresentationError(ValueError):
    def __init__(self, message: str, code: str = "invalid_presentation_input"):
        super().__init__(message)
        self.code = code


def canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False,
                       sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unique_pairs(pairs: list) -> dict:
    value: dict = {}
    for key, item in pairs:
        if key in value:
            raise PresentationError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _bad_number(value: str) -> None:
    raise PresentationError(f"nonfinite JSON number: {value}")


def read_document(path: str | Path) -> tuple[Path, dict, str]:
    candidate = Path(path)
    try:
        if candidate.is_symlink() or not stat.S_ISREG(candidate.stat().st_mode):
            raise PresentationError("input must be a regular file, not a symlink")
        if candidate.stat().st_size > MAX_SOURCE_BYTES:
            raise PresentationError("input exceeds the saved-result byte limit")
        payload = candidate.read_bytes()
        if len(payload) > MAX_SOURCE_BYTES:
            raise PresentationError("input exceeds the saved-result byte limit")
        document = json.loads(payload, object_pairs_hook=_unique_pairs,
                              parse_constant=_bad_number)
        if not isinstance(document, dict):
            raise PresentationError("input must contain a JSON object")
        canonical(document)  # Reject overflow-to-infinity and invalid Unicode.
        return candidate.resolve(strict=True), document, digest(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise PresentationError(f"cannot read saved input: {exc}") from exc


def pcb_module(name: str):
    """Support the pinned standalone engine and the source checkout alike."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        return importlib.import_module("toolboxes.physics." + name)


def _stage_evidence(result_path: Path, model_path: Path, domain: str,
                    tracked: dict[Path, str]) -> dict | None:
    """Verify the upstream stage's immutable evidence before inheriting its gate."""
    candidate = result_path.parent / "simulation-manifest.json"
    if not candidate.exists() and not candidate.is_symlink():
        return None
    manifest_path, manifest, manifest_digest = read_document(candidate)
    if manifest.get("schema_version") != "pipeline-simulation/1" or manifest.get("domain") != domain:
        raise PresentationError("simulation manifest schema/domain is invalid", "invalid_simulation_manifest")
    gate = manifest.get("quality_gate")
    if (not isinstance(gate, dict) or type(gate.get("numerical_passed")) is not bool
            or type(gate.get("passed")) is not bool or type(manifest.get("data_usable")) is not bool
            or type(manifest.get("ok")) is not bool):
        raise PresentationError("simulation manifest quality gate is invalid", "invalid_simulation_manifest")
    data = manifest.get("data", {})
    if not isinstance(data, dict) or ("numerical_usable" in data and type(data["numerical_usable"]) is not bool):
        raise PresentationError("simulation manifest data quality is invalid", "invalid_simulation_manifest")
    refs = manifest.get("artifacts")
    if not isinstance(refs, list) or not 1 <= len(refs) <= 32:
        raise PresentationError("simulation manifest artifacts are invalid", "invalid_simulation_manifest")
    root = result_path.parent
    descriptors: dict[str, dict] = {}
    paths: dict[str, Path] = {}
    total = 0
    for ref in refs:
        if not isinstance(ref, dict):
            raise PresentationError("simulation artifact descriptor is invalid", "invalid_simulation_manifest")
        name = ref.get("path")
        if (not isinstance(name, str) or not name or "\\" in name or "\0" in name
                or PurePosixPath(name).is_absolute() or any(part in ("", ".", "..") for part in name.split("/"))
                or name in descriptors or name == "simulation-manifest.json"):
            raise PresentationError("simulation artifact path is unsafe or duplicated", "invalid_simulation_manifest")
        size, expected = ref.get("bytes"), ref.get("sha256")
        if (type(size) is not int or not 0 <= size <= MAX_SOURCE_BYTES
                or not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected)
                or not isinstance(ref.get("role"), str) or not isinstance(ref.get("media_type"), str)):
            raise PresentationError("simulation artifact digest/size is invalid", "invalid_simulation_manifest")
        path = root
        for part in name.split("/"):
            path = path / part
            if path.is_symlink():
                raise PresentationError("simulation artifact paths cannot use symlinks", "invalid_simulation_manifest")
        try:
            path = path.resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size != size:
                raise PresentationError("simulation artifact is missing or outside the run", "invalid_simulation_manifest")
            payload = path.read_bytes()
        except OSError as exc:
            raise PresentationError("simulation artifact cannot be read", "invalid_simulation_manifest") from exc
        if len(payload) != size or digest(payload) != expected:
            raise PresentationError("simulation artifact hash does not match its manifest", "invalid_simulation_manifest")
        total += size
        if total > MAX_EXPORT_BYTES:
            raise PresentationError("simulation evidence exceeds its byte limit", "invalid_simulation_manifest")
        if path in tracked and tracked[path] != expected:
            raise PresentationError("simulation manifest disagrees with the loaded source", "invalid_simulation_manifest")
        tracked[path] = expected
        descriptors[name], paths[name] = ref, path
    for key, expected_path, role in (("result", result_path, "canonical_result"),
                                     ("model", model_path, "canonical_model")):
        ref = manifest.get(key)
        if (not isinstance(ref, dict) or not isinstance(ref.get("path"), str)
                or descriptors.get(ref["path"]) != ref or ref.get("role") != role
                or ref.get("media_type") != "application/json" or paths.get(ref["path"]) != expected_path):
            raise PresentationError(f"simulation manifest {key} is not bound to the supplied source", "invalid_simulation_manifest")
    tracked[manifest_path] = manifest_digest
    return {"path": manifest_path, "document": manifest, "sha256": manifest_digest, "files": paths}


def _pcb_gate(result: dict, model: dict) -> dict:
    pcb_module("pcb_video")._validate_result(result)
    normalized = pcb_module("pcb_thermal").validate_pcb_spec(model)
    for key in ("board", "grid", "components", "mode"):
        if result.get(key) != normalized[key]:
            raise PresentationError(f"saved PCB result disagrees with model.{key}")
    mode = normalized["mode"]
    expected_duration = normalized.get("transient", {}).get("duration_s", 0.0)
    if result.get("duration_s") != expected_duration:
        raise PresentationError("saved PCB duration disagrees with model")
    final = result.get("temperature_c")
    if final != result["snapshots"][-1]["temperature_c"]:
        raise PresentationError("saved PCB final field disagrees with last snapshot")
    if result["snapshots"][-1]["time_s"] != expected_duration:
        raise PresentationError("saved PCB snapshot interval is incomplete")
    source_power = math.fsum(item["power_w"] for item in normalized["components"])
    deposited = math.fsum(value for row in result["power_w"] for value in row)
    balance = result.get("energy_balance", {})
    residual = balance.get("residual")
    heat, rejected, stored = (result.get(key) for key in (
        "heat_rejection_w", "rejected_energy_j", "stored_energy_j"))
    finite = all(type(value) in (float, int) and math.isfinite(value)
                 for value in (residual, heat, rejected, stored))
    recomputed = ((source_power - heat) if mode == "steady" else
                  source_power * expected_duration - rejected - stored) if finite else math.inf
    scale = max(abs(source_power * (expected_duration if mode == "transient" else 1)),
                abs(heat if mode == "steady" else rejected) if finite else 0,
                abs(stored) if finite else 0, 1e-12)
    tolerance = 1e-9 + 1e-7 * scale
    checks = {
        "component_power_conserved": abs(source_power - deposited) <= max(1e-9, abs(source_power) * 1e-8)
            and type(result.get("total_power_w")) in (int, float)
            and abs(result["total_power_w"] - source_power) <= max(1e-9, abs(source_power) * 1e-8),
        "finite_temperature": all(type(value) in (float, int) and math.isfinite(value) and value > -273.15
                                  for row in final for value in row),
        "energy_balance": finite and balance.get("passed") is True
            and abs(recomputed) <= tolerance and abs(residual - recomputed) <= tolerance,
    }
    return {"numerical_passed": all(checks.values()), "validation_mode": "strict",
            "numerical_checks": checks, "precision_warnings": [],
            "failed_checks": [key for key, passed in checks.items() if not passed]}


def load_source(result_path: str | Path, model_path: str | Path,
                domain: str = "physics") -> dict:
    if domain not in {"physics", "pcb_thermal"}:
        raise PresentationError("unsupported result domain", "unsupported_domain")
    path, result, source_digest = read_document(result_path)
    model_file, document, model_digest = read_document(model_path)
    model = document.get("model", document.get("scene", document))
    if not isinstance(model, dict):
        raise PresentationError("model must be a scene object or a prepared model bundle")
    if document.get("domain", domain) != domain:
        raise PresentationError("model bundle domain disagrees with requested domain")
    tracked = {path: source_digest, model_file: model_digest}
    stage = _stage_evidence(path, model_file, domain, tracked)
    measurements = None
    if domain == "physics":
        from physics_demo.analysis.results import _summary, verified_measurements
        if canonical(result.get("scene")) != canonical(model):
            raise PresentationError("saved result is not bound to the supplied normalized scene")
        try:
            summary = _summary(result, path.parent)
            gate = summary["quality_gate"]
            if model.get("queries"):
                measurements = verified_measurements(result, path.parent)
                measurement_file, stored, measurement_digest = read_document(result["artifacts"]["measurements"])
                if canonical(stored) != canonical(measurements):
                    raise PresentationError("measurements changed during verification")
                tracked[measurement_file] = measurement_digest
        except (ValueError, KeyError, TypeError) as exc:
            raise PresentationError(f"saved result failed integrity checks: {exc}", "invalid_saved_result") from exc
        frames = result["trajectory"]["frames"]
        sampling = {
            "display_frames": {"source": "result.trajectory.frames", "sample_count": len(frames),
                "quantitative_usable": False,
                "precision": "Recorded renderer values; coordinates may be rounded and particles decimated. See result.plan and trajectory.diagnostics.",
                "clock": "saved output frames; independent of solver observations"},
            "observations": None if measurements is None else {
                "source": "measurements.observations", "precision": "solver float64",
                "clock": "t=0 and completed solver macro steps", **measurements["sampling"]},
        }
    else:
        try:
            gate = _pcb_gate(result, model)
        except (ValueError, KeyError, TypeError) as exc:
            raise PresentationError(f"saved PCB result failed integrity checks: {exc}", "invalid_saved_result") from exc
        sampling = {
            "temperature_snapshots": {"source": "result.snapshots", "sample_count": len(result["snapshots"]),
                "precision": "saved solver float64 grid values", "clock": "saved thermal snapshots; can be decimated"},
            "time_series": {"source": "result.time_series", "sample_count": len(result.get("time_series", [])),
                "precision": "solver float64 scalar reductions", "clock": "completed thermal time steps; steady mode has one state"},
            "spatial_model": "Thickness-averaged 2D finite-volume board; cell centers in metres, row zero at bottom edge.",
        }
    if stage is not None:
        manifest = stage["document"]
        stage_gate = manifest["quality_gate"]
        stage_usable = (stage_gate["numerical_passed"] and stage_gate["passed"] and manifest["ok"]
                        and manifest["data_usable"]
                        and manifest.get("data", {}).get("numerical_usable", True))
        gate = {**gate, "numerical_passed": gate.get("numerical_passed") is True and stage_usable,
                "passed": gate.get("passed", True) is True and stage_gate["passed"] and manifest["ok"],
                "simulation_stage": {"manifest_sha256": stage["sha256"], "quality_gate": stage_gate,
                                     "data_usable": manifest["data_usable"]},
                "numerical_checks": {**gate.get("numerical_checks", {}), "simulation_stage_data_usable": stage_usable}}
        if not stage_usable:
            gate["failed_checks"] = list(dict.fromkeys([*gate.get("failed_checks", []), "simulation_stage_data_usable"]))
    warnings = list(result.get("warnings", []))
    if domain == "physics":
        warnings.append("Display frames are rounded/sampled presentation data and cannot replace solver observations.")
        if measurements is None:
            warnings.append("This run did not predeclare measurements. Full-resolution historical observables cannot be recovered from display frames.")
    if gate.get("numerical_passed") is not True:
        warnings.append("Numerical quality checks did not pass. Exported values are diagnostic data; do not claim quantitative conclusions.")
    return {"path": path, "result": result, "model": model, "tracked": tracked,
            "model_path": model_file, "source_result_sha256": source_digest,
            "source_model_sha256": model_digest, "domain": domain, "measurements": measurements,
            "quality_gate": gate, "sampling": sampling, "warnings": warnings, "stage": stage}


def verify_unchanged(source: dict) -> None:
    for path, expected in source["tracked"].items():
        if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != expected:
            raise PresentationError("saved source changed while producing presentation", "source_changed")


def output_directory(output_dir: str | Path, source: dict) -> Path:
    root = Path(output_dir)
    if root.is_symlink():
        raise PresentationError("output directory cannot be a symlink")
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()
    if any(root == path.parent for path in source["tracked"]):
        raise PresentationError("presentation output directory must differ from source directories")
    return root


def atomic_write(path: Path, payload: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".presentation-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _csv_cell(value: Any) -> Any:
    # Keep IDs inert when opened in spreadsheet applications; JSON keeps originals.
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _csv_bytes(header: list[str], rows: Iterable, limit: int) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow([_csv_cell(value) for value in row])
        if output.tell() > limit:
            raise PresentationError("CSV export exceeds the uncompressed byte limit", "export_too_large")
    payload = output.getvalue().encode("utf-8")
    if len(payload) > limit:
        raise PresentationError("CSV export exceeds the uncompressed byte limit", "export_too_large")
    return payload


def _observation_rows(source: dict):
    measurements = source["measurements"]
    if measurements is None:
        return
    observations = measurements["observations"]
    answers = {answer["id"]: answer for answer in measurements["answers"]}
    usable = source["quality_gate"]["numerical_passed"] is True
    for query_id, values in sorted(observations["columns"].items()):
        for stamp, value in zip(observations["times"], values):
            yield stamp, query_id, "value", value, answers[query_id]["unit"], usable, "solver_macro_steps"
    for query_id, channels in sorted(observations["nbody"].items()):
        for channel, values in sorted(channels.items()):
            unit = {"max_com_radius": "m", "min_pair_distance": "m", "energy": "J", "momentum": "kg*m/s"}[channel]
            for stamp, value in zip(observations["times"], values):
                if isinstance(value, list):
                    for axis, component in zip("xyz", value):
                        yield stamp, query_id, channel + "." + axis, component, unit, usable, "solver_macro_steps"
                else:
                    yield stamp, query_id, channel, value, unit, usable, "solver_macro_steps"


def _display_rows(result: dict):
    for index, frame in enumerate(result["trajectory"]["frames"]):
        for channel, value in sorted(frame.items()):
            if channel == "t":
                continue
            yield index, frame["t"], channel, json.dumps(value, separators=(",", ":"), allow_nan=False), False, "display_sampled"


def _pcb_rows(result: dict, usable: bool):
    nx, ny = result["grid"]["nx"], result["grid"]["ny"]
    dx, dy = result["board"]["width_m"] / nx, result["board"]["height_m"] / ny
    for snapshot in result["snapshots"]:
        for row in range(ny):
            for column in range(nx):
                yield (snapshot["time_s"], row, column, (column + .5) * dx, (row + .5) * dy,
                       snapshot["temperature_c"][row][column], result["power_w"][row][column], usable)


def _dictionary(source: dict) -> dict:
    common = {"schema": "pipeline-data-dictionary/1", "domain": source["domain"],
              "quality_gate": source["quality_gate"], "sampling": source["sampling"],
              "warnings": source["warnings"],
              "number_encoding": "JSON numbers and CSV round-trip Python float repr preserve saved binary64 values; no precision upgrade is implied.",
              "csv_strings": "Formula-leading string cells carry a leading apostrophe; canonical JSON preserves the original strings.",
              "files": {"result.json": "Byte-exact original complete numerical result, including its limitations and diagnostics. Original key order is retained for legacy engine compatibility.",
                        "model.json": "Byte-exact supplied model document or pipeline model bundle, preserving its engine provenance.",
                        "scene.normalized.json": "Exact normalized solver model bound to the result.",
                        "quality.json": "Numerical check status; false quantitative_usable means diagnostic-only data."}}
    if source["stage"] is not None:
        common["files"]["source-stage/"] = (
            "Byte-exact simulation-manifest.json and every artifact it declares, including saved plan, summary, "
            "completion and measurements when present. Manifest relative references resolve inside source-stage/. "
            "Absolute paths embedded in original result/summary files are historical run provenance, not portable "
            "ZIP paths; extraction does not create a restart checkpoint or automatically rebind legacy inspect paths.")
    if source["domain"] == "physics":
        common["files"].update({
            "display-frames.csv": "frame_index, time_s [s], channel, value_json [channel schema], quantitative_usable=false, source=display_sampled. Compact geometry channels retain their saved values; use result.trajectory and result.scene for identity/topology.",
            "observations.csv": "time_s [s], query_id, channel, value [unit column], unit, quantitative_usable, source=solver_macro_steps. Empty when measurements were not predeclared; never filled from video frames.",
            "measurements.json": "Verified predeclared query observations and reductions, or explicit unavailable status. Consult quality.json before quantitative interpretation.",
        })
    else:
        common["files"].update({
            "thermal-grid.csv": "time_s [s], row [0-based, bottom to top], column [0-based, left to right], x_m [m], y_m [m] at cell centers, temperature_c [degC], power_w [W/cell], quantitative_usable. Only saved snapshots are exported; interpolation is confined to the video.",
            "thermal-series.csv": "time_s [s], min_temperature_c, max_temperature_c [degC], quantitative_usable; saved per-step scalar extrema. The engine does not persist a per-step mean.",
            "measurements.json": "PCB sampling contract and saved time series; solver-scoped board averages do not resolve package/trace/via/through-thickness hotspots.",
        })
    return common


def export_data(result_path: str | Path, model_path: str | Path, output_dir: str | Path,
                domain: str = "physics", max_bytes: int = 16 * 1024 * 1024,
                modeling_assets: list | None = None) -> dict:
    """Export a reproducible ZIP with JSON, CSV, provenance and quality labels."""
    if type(max_bytes) is not int or not 1024 <= max_bytes <= MAX_EXPORT_BYTES:
        raise PresentationError("invalid data package byte budget")
    source = load_source(result_path, model_path, domain)
    output = output_directory(output_dir, source)
    usable = source["quality_gate"].get("numerical_passed") is True
    expanded_limit = min(MAX_EXPORT_BYTES, max_bytes * 16)
    quality = {"quantitative_usable": usable, "quality_gate": source["quality_gate"],
               "warnings": source["warnings"], "sampling": source["sampling"]}
    members = {"result.json": source["path"].read_bytes(),
               "model.json": source["model_path"].read_bytes(),
               "scene.normalized.json": canonical(source["model"]),
               "quality.json": canonical(quality),
               "data-dictionary.json": canonical(_dictionary(source))}
    modeling_sources = []
    if modeling_assets is not None:
        if not isinstance(modeling_assets, list) or len(modeling_assets) > 96:
            raise PresentationError('invalid modeling asset inventory')
        for item in modeling_assets:
            if not isinstance(item, dict) or set(item) != {'path', 'name', 'sha256'}:
                raise PresentationError('invalid modeling asset descriptor')
            name, path = item['name'], Path(item['path'])
            relative = Path(name)
            if (not name.startswith('modeling/') or relative.is_absolute() or '..' in relative.parts
                    or relative.suffix not in ('.json', '.txt', '.glb', '.obj') or name in members
                    or any(p.is_symlink() for p in (path, *path.parents))
                    or not path.is_file() or path.stat().st_size > max_bytes):
                raise PresentationError('invalid or oversized modeling asset')
            payload = path.read_bytes()
            if digest(payload) != item['sha256']:
                raise PresentationError('modeling asset hash changed')
            members[name] = payload
            modeling_sources.append((path, item['sha256']))
    stage = source["stage"]
    if stage is not None:
        members["source-stage/simulation-manifest.json"] = stage["path"].read_bytes()
        members.update({"source-stage/" + name: path.read_bytes() for name, path in stage["files"].items()})
    if domain == "physics":
        measurements = source["measurements"] or {"status": "unavailable", "reason": "measurements_not_predeclared", "observations": None}
        members["measurements.json"] = canonical(measurements)
        members["observations.csv"] = _csv_bytes(
            ["time_s", "query_id", "channel", "value", "unit", "quantitative_usable", "source"], _observation_rows(source), expanded_limit)
        members["display-frames.csv"] = _csv_bytes(
            ["frame_index", "time_s", "channel", "value_json", "quantitative_usable", "source"], _display_rows(source["result"]), expanded_limit)
    else:
        result = source["result"]
        members["measurements.json"] = canonical({"sampling": source["sampling"], "time_series": result["time_series"],
                                                   "quantitative_usable": usable})
        members["thermal-grid.csv"] = _csv_bytes(
            ["time_s", "row", "column", "x_m", "y_m", "temperature_c", "power_w", "quantitative_usable"],
            _pcb_rows(result, usable), expanded_limit)
        members["thermal-series.csv"] = _csv_bytes(
            ["time_s", "min_temperature_c", "max_temperature_c", "quantitative_usable"],
            ((row["time_s"], row["min_temperature_c"], row["max_temperature_c"], usable)
             for row in result["time_series"]), expanded_limit)
    if sum(len(value) for value in members.values()) > expanded_limit:
        raise PresentationError("data package exceeds the uncompressed byte budget", "export_too_large")
    manifest = {"schema": "pipeline-data/1", "domain": domain,
                "source_result_sha256": source["source_result_sha256"],
                "source_model_sha256": source["source_model_sha256"],
                "solver_rerun": False, "quantitative_usable": usable,
                "warnings": source["warnings"], "sampling": source["sampling"],
                "members": [{"path": name, "bytes": len(value), "sha256": digest(value)} for name, value in sorted(members.items())]}
    if stage is not None:
        manifest["source_stage"] = {"manifest_path": "source-stage/simulation-manifest.json",
                                    "manifest_sha256": stage["sha256"], "reference_root": "source-stage/",
                                    "all_declared_artifacts_included": True}
    members["manifest.json"] = canonical(manifest)
    def pack(compression: int) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
            for name, content in sorted(members.items()):
                entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, content, compress_type=compression,
                    compresslevel=6 if compression == zipfile.ZIP_DEFLATED else None)
        return buffer.getvalue()

    # Retain legacy small archive bytes. Large complete data may use lossless
    # DEFLATE; member hashes and expanded limits remain independent of packing.
    payload = pack(zipfile.ZIP_STORED)
    if len(payload) > max_bytes:
        payload = pack(zipfile.ZIP_DEFLATED)
    if len(payload) > max_bytes:
        raise PresentationError("complete data package exceeds the delivery byte budget", "export_too_large")
    verify_unchanged(source)
    if any(digest(path.read_bytes()) != checksum for path, checksum in modeling_sources):
        raise PresentationError('modeling assets changed during export')
    target = output / "data.zip"
    envelope = {**manifest, "path": str(target), "media_type": "application/zip", "bytes": len(payload), "sha256": digest(payload)}
    atomic_write(target, payload)
    atomic_write(output / "data-manifest.json", canonical(envelope))
    return {**envelope, "manifest_path": str(output / "data-manifest.json")}
