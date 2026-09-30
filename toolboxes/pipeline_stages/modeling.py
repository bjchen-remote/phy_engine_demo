"""Build validated canonical models without running or rendering a simulation."""
from __future__ import annotations

import copy
import math
from pathlib import Path

from .common import (artifact, canonical_bytes, check_budget, digest, engine_identity,
                     engine_module, failure, new_output_directory, write_json)


def capabilities() -> dict:
    return {"protocol": "stage-module/1", "stage": "modeling",
            "operations": ["modeling.prepare", "modeling.build"],
            "domains": ["physics", "pcb_thermal"], "qualities": ["standard"],
            "output_schema": "pipeline-model/1",
            "limitations": ["Geometry and physical parameters come from explicit structured input.",
                            "No image reconstruction or tolerance-certified CAD refinement.",
                            "No silent change from a requested high modeling quality."]}


def build_model(domain: str, model: dict, output_dir: str | Path,
                budget_seconds: float | None = None, unlimited: bool = False,
                quality: str = "standard") -> dict:
    """Validate an explicit model and publish a content-addressed handoff bundle."""
    check_budget(budget_seconds, unlimited)
    if quality != "standard":
        return failure("unsupported_modeling_quality",
                       "This modeling adapter supports standard structured geometry only.",
                       requested_quality=quality, supported_qualities=["standard"])
    if domain not in ("physics", "pcb_thermal"):
        return failure("unsupported_domain", "Unsupported modeling domain", domain=domain)
    if not isinstance(model, dict):
        return failure("invalid_model", "The model must be a structured object")
    canonical_bytes(model)
    source = copy.deepcopy(model)
    if domain == "physics":
        prepared = engine_module("physics_demo.runner").prepare(
            source, budget_seconds, unlimited=unlimited)
        if not prepared.get("ok") or not prepared.get("ready_to_simulate"):
            return {**prepared, "ok": False, "code": "model_not_ready"}
        normalized = prepared["scene"]
        preparation = {key: value for key, value in prepared.items()
                       if key not in ("scene", "scene_json")}
        assumptions = prepared.get("assumptions", [])
    else:
        try:
            normalized = engine_module("pcb_thermal").validate_pcb_spec(source)
        except (ValueError, TypeError, KeyError) as error:
            return failure("invalid_pcb_model", str(error))
        steps = (1 if normalized["mode"] == "steady" else
                 math.ceil(normalized["transient"]["duration_s"] /
                           normalized["transient"]["time_step_s"]))
        cells = normalized["grid"]["nx"] * normalized["grid"]["ny"]
        # Preserve the existing conservative prepare estimate. The host owns the
        # hard subprocess timeout; the PCB solver has no interruptible deadline.
        estimate = max(25.0, min(300.0, 15.0 + cells * steps / 4000.0))
        if budget_seconds is not None and estimate > budget_seconds:
            return failure("insufficient_time_budget", "PCB estimate exceeds the task budget",
                           estimated_seconds=estimate, ready_to_simulate=False)
        preparation = {"ok": True, "ready_to_simulate": True,
                       "estimated_seconds": estimate, "grid_cells": cells, "time_steps": steps}
        assumptions = ["2D effective thermal sheet with prescribed component powers; "
                       "no resolved traces, vias, or package internals."]
    output = new_output_directory(output_dir)
    bundle = {"schema_version": "pipeline-model/1", "domain": domain,
              "quality": "standard", "source_engine": engine_identity(),
              "model": normalized, "model_sha256": digest(normalized),
              "preparation": preparation, "assumptions": assumptions,
              "execution_limits": {"budget_seconds": budget_seconds, "unlimited": unlimited}}
    path = output / "model.json"
    write_json(path, bundle)
    return {"ok": True, "ready_to_simulate": True, "domain": domain,
            "bundle_path": str(path), "model_path": str(path),
            "model_sha256": bundle["model_sha256"], "preparation": preparation,
            "artifacts": [artifact(path, output, "canonical_model")]}
