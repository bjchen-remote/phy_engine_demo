"""Task-scoped admission contracts for the existing QQ v1 operations.

This module registers no tools, reads no task files and performs no computation.
The composing host must call it explicitly and retain its existing path, pin,
checkpoint, numerical, cleanup-proof and delivery validators. Descriptor checks
do not prove that geometry or bytes are authentic. The adjacent JSON contains
the supported JSON Schema subset; runtime validation needs only the stdlib.
"""
from __future__ import annotations

import copy
from functools import lru_cache
import json
import math
from pathlib import Path
import re


_PURPOSES = ("model_preview", "physical_simulation")
_MESH_ROLES = ("raw_geometry", "display_geometry", "simulation_geometry")


def recovery_for(code: str, *, stage: str | None = None,
                 preserved_simulation: bool = False) -> dict:
    """Return guidance, never retry or infer that an unverified checkpoint exists."""
    value = {"automatic_retry": False, "solver_rerun": False, "resend": False,
             "action": "stop", "next_action": None, "preserve_artifacts": True,
             "requires_verified_checkpoint": False}
    if code in ("delivery_unknown", "unknown_receipt", "sending_unknown"):
        value["action"] = "reconcile_receipt"
    elif code in ("stage_incomplete", "image_modeling_incomplete", "model_cleanup_incomplete"):
        value["action"] = "retain_for_explicit_recovery"
    elif code in ("artifact_hash_mismatch", "topology_mismatch", "checkpoint_invalid",
                  "asset_scope_mismatch", "model_dependency_mismatch"):
        value["action"] = "inspect_integrity"
    elif (preserved_simulation is True and stage in ("rendering", "data_export", "presentation")
          and code in ("pipeline_stage_failed", "stage_failed", "render_failed",
                       "render_verification_failed", "data_export_failed")):
        value.update(action="render_only", requires_verified_checkpoint=True,
                     next_action={"tool": "pipeline_render_prepare", "arguments": {}})
    elif code in ("invalid_arguments", "unsupported_quality", "purpose_mismatch", "units_mismatch",
                  "plan_changed", "plan_expired", "missing_simulation"):
        value["action"] = "correct_input_then_prepare"
    return value


class OperationContractError(ValueError):
    def __init__(self, code: str, path: str, message: str, *, stage: str | None = None):
        self.code, self.path, self.message, self.stage = code, path, message, stage
        super().__init__(message)

    def as_result(self) -> dict:
        result = {"ok": False, "code": self.code, "path": self.path,
                  "message": self.message, "retryable": False,
                  "recovery": recovery_for(self.code, stage=self.stage)}
        if self.stage is not None:
            result["stage"] = self.stage
        return result


def _fail(code: str, path: str, message: str) -> None:
    raise OperationContractError(code, path, message)


def _strict_pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _check_supported_schema(schema: dict) -> None:
    supported = {"$ref", "$schema", "type", "const", "enum", "oneOf", "anyOf", "allOf", "not",
                 "properties", "required", "additionalProperties", "items", "minItems", "maxItems",
                 "uniqueItems", "minLength", "maxLength", "pattern", "minimum", "maximum",
                 "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "description", "title",
                 "default", "examples", "x-error-code"}
    if set(schema) - supported:
        raise ValueError("operation catalogue uses an unsupported schema keyword")
    if "additionalProperties" in schema and type(schema["additionalProperties"]) is not bool:
        raise ValueError("operation catalogue requires boolean additionalProperties")
    for child in schema.get("properties", {}).values():
        _check_supported_schema(child)
    for keyword in ("items", "not"):
        if keyword in schema:
            _check_supported_schema(schema[keyword])
    for keyword in ("oneOf", "anyOf", "allOf"):
        for child in schema.get(keyword, []):
            _check_supported_schema(child)


@lru_cache(maxsize=1)
def _document() -> dict:
    path = Path(__file__).with_suffix(".json")
    document = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    if document.get("schema_version") != "pipeline-operation-contracts/1":
        raise ValueError("unsupported operation contract document")
    for schema in document["$defs"].values():
        _check_supported_schema(schema)
    for operation in document["operations"].values():
        for name in ("input_schema", "output_schema"):
            _check_supported_schema(operation[name])
    return document


def operation_descriptors(available_operations=None) -> dict:
    """Return copies filtered by a verified manifest; never advertise draft APIs.

    With no filter this is the known v1 catalogue, not installation availability.
    A composing host should always pass its pinned manifest's operation names.
    """
    known = _document()["operations"]
    selected = known if available_operations is None else set(available_operations)
    def inline(value):
        if isinstance(value, dict):
            if "$ref" in value:
                definition = value["$ref"].removeprefix("#/$defs/")
                return inline({**_document()["$defs"][definition],
                               **{key: item for key, item in value.items() if key != "$ref"}})
            return {key: inline(item) for key, item in value.items()}
        if isinstance(value, list):
            return [inline(item) for item in value]
        return copy.deepcopy(value)
    # Each advertised input/output schema is independently usable by a host;
    # local catalogue $refs must not become dangling capability-response refs.
    return {name: inline(item) for name, item in known.items() if name in selected}


def _same(left, right) -> bool:
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return left == right


def _type(value, kind: str) -> bool:
    return {"object": lambda: isinstance(value, dict), "array": lambda: isinstance(value, list),
            "string": lambda: isinstance(value, str), "boolean": lambda: type(value) is bool,
            "integer": lambda: type(value) is int,
            "number": lambda: _finite_number(value),
            "null": lambda: value is None}[kind]()


def _finite_number(value) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _validate(value, schema: dict, path: str, code: str) -> None:
    if "$ref" in schema:
        prefix = "#/$defs/"
        if not schema["$ref"].startswith(prefix):
            raise ValueError("external operation schema references are not supported")
        _validate(value, _document()["$defs"][schema["$ref"][len(prefix):]], path, code)
    kind = schema.get("type")
    if kind is not None and not any(_type(value, item) for item in ([kind] if isinstance(kind, str) else kind)):
        _fail(code, path, "Value has the wrong JSON type.")
    if "const" in schema and not _same(value, schema["const"]):
        _fail(code, path, "Value differs from the fixed contract value.")
    if "enum" in schema and not any(_same(value, item) for item in schema["enum"]):
        _fail(schema.get("x-error-code", code), path, "Value is not an advertised option.")
    for keyword in ("oneOf", "anyOf", "allOf"):
        if keyword in schema:
            matches = 0
            for alternative in schema[keyword]:
                try:
                    _validate(value, alternative, path, code)
                    matches += 1
                except OperationContractError:
                    pass
            if ((keyword == "oneOf" and matches != 1) or (keyword == "anyOf" and not matches)
                    or (keyword == "allOf" and matches != len(schema[keyword]))):
                _fail(code, path, "Value does not match the required contract branch.")
    if "not" in schema:
        try:
            _validate(value, schema["not"], path, code)
        except OperationContractError:
            pass
        else:
            _fail(code, path, "This field combination is forbidden.")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                _fail(code, path + "/" + key, "Required field is missing.")
        for key, item in value.items():
            pointer = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key in properties:
                _validate(item, properties[key], pointer, code)
            elif schema.get("additionalProperties") is False:
                _fail(code, pointer, "Unknown field is not allowed.")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", math.inf):
            _fail(code, path, "Array length is outside the contract bounds.")
        if schema.get("uniqueItems") and any(value[i] in value[:i] for i in range(len(value))):
            _fail(code, path, "Array entries must be unique.")
        if "items" in schema:
            for index, item in enumerate(value):
                _validate(item, schema["items"], path + "/" + str(index), code)
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", math.inf):
            _fail(code, path, "String length is outside the contract bounds.")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            _fail(code, path, "String does not match the contract format.")
    if type(value) in (int, float):
        if not _finite_number(value):
            _fail(code, path, "Numbers must be finite.")
        if (value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf)
                or "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]
                or "exclusiveMaximum" in schema and value >= schema["exclusiveMaximum"]
                or "multipleOf" in schema and value % schema["multipleOf"] != 0):
            _fail(code, path, "Number is outside the contract bounds.")


def _json_value(value, path: str, code: str) -> None:
    if type(value) not in (dict, list, str, bool, int, float, type(None)):
        _fail(code, path, "Value must use JSON object, array and scalar types.")
    try:
        json.dumps(value, allow_nan=False)
    except (ValueError, TypeError, OverflowError, RecursionError):
        _fail(code, path, "Value must be finite JSON data.")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            _fail(code, path, "Object keys must be strings.")
        for key, item in value.items():
            _json_value(item, path + "/" + key, code)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _json_value(item, path + "/" + str(index), code)


def validate_operation(operation: str, arguments: dict, *, available_operations=None,
                       purpose: str | None = None) -> dict:
    contracts = _document()["operations"]
    if not isinstance(operation, str) or operation not in contracts:
        _fail("unknown_operation", "/operation", "Operation is not in the current v1 contract catalogue.")
    if available_operations is not None and operation not in available_operations:
        _fail("operation_unavailable", "/operation", "Operation is absent from this task's pinned manifest.")
    contract = contracts[operation]
    if purpose is not None and purpose not in contract["purposes"]:
        _fail("purpose_mismatch", "/purpose", "Operation does not support this task purpose.")
    _json_value(arguments, "/arguments", "invalid_arguments")
    _validate(arguments, contract["input_schema"], "/arguments", "invalid_arguments")
    # These strings are legacy serialized values, not code or paths. The pinned
    # engine still validates their scene, recipe and physical-domain semantics.
    def serialized(value, path):
        if isinstance(value, list):
            for index, item in enumerate(value):
                serialized(item, path + "/" + str(index))
        elif isinstance(value, dict):
            for name, item in value.items():
                pointer = path + "/" + name
                if name.endswith("_json") and isinstance(item, str):
                    try:
                        parsed = json.loads(item, object_pairs_hook=_strict_pairs,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
                        _json_value(parsed, pointer, "invalid_arguments")
                    except (ValueError, TypeError, RecursionError):
                        _fail("invalid_arguments", pointer, "Serialized input must be strict finite JSON.")
                else:
                    serialized(item, pointer)
    serialized(arguments, "/arguments")
    return copy.deepcopy(arguments)


def validate_operation_result(operation: str, result: dict, *, purpose: str | None = None) -> dict:
    if operation not in _document()["operations"]:
        _fail("unknown_operation", "/operation", "Unknown operation result.")
    contract = _document()["operations"][operation]
    if purpose is not None and purpose not in contract["purposes"]:
        _fail("purpose_mismatch", "/purpose", "Result purpose differs from the operation contract.")
    _json_value(result, "/result", "invalid_result")
    _validate(result, contract["output_schema"], "/result", "invalid_result")
    if operation.startswith("modeling_preview_"):
        for name in ("ready_to_simulate", "simulation_performed", "numerical_usable", "numerical_passed"):
            if name in result and result[name] is not False:
                _fail("invalid_result", "/result/" + name, "Model presentation cannot claim physical or numerical success.")
        if result.get("ok") is True and operation in ("modeling_preview_from_image", "modeling_preview_cleanup"):
            for name, expected in (("ready_to_preview", True), ("ready_to_simulate", False), ("simulation_performed", False)):
                if result.get(name) is not expected:
                    _fail("invalid_result", "/result/" + name, "Display-only readiness must be explicit.")
        verification = result.get("verification", {})
        if isinstance(verification, dict):
            for name in ("simulation_performed", "ready_to_simulate", "numerical_usable", "numerical_passed"):
                if name in verification and verification[name] is not False:
                    _fail("invalid_result", "/result/verification/" + name,
                          "Presentation verification cannot certify physics or numerics.")
    return copy.deepcopy(result)


def validate_asset_descriptor(asset: dict, *, task_id: str, expected_role: str | None = None,
                              expected_sha256: str | None = None, expected_units: str | None = None,
                              expected_topology_sha256: str | None = None) -> dict:
    _json_value(asset, "/asset", "invalid_asset")
    _validate(asset, _document()["$defs"]["AssetDescriptor"], "/asset", "invalid_asset")
    if asset["task_scope"] != task_id:
        _fail("asset_scope_mismatch", "/asset/task_scope", "Asset belongs to a different task scope.")
    for field, expected, code in (("role", expected_role, "invalid_asset"),
                                  ("sha256", expected_sha256, "artifact_hash_mismatch"),
                                  ("units", expected_units, "units_mismatch"),
                                  ("topology_sha256", expected_topology_sha256, "topology_mismatch")):
        if expected is not None and asset.get(field) != expected:
            _fail(code, "/asset/" + field, "Asset differs from the expected " + field + ".")
    if asset["role"] in _MESH_ROLES and "topology_sha256" not in asset:
        _fail("topology_mismatch", "/asset/topology_sha256", "Mesh assets require a topology hash.")
    if asset["role"] in (*_MESH_ROLES, "model"):
        if asset["units"] == "not_applicable":
            _fail("units_mismatch", "/asset/units", "Geometry must declare model units or metre units.")
        if asset["coordinate_frame"] == "not_applicable":
            _fail("invalid_asset", "/asset/coordinate_frame", "Geometry must declare its coordinate frame.")
    if (asset["purpose"] == "model_preview" and asset["role"] in
            ("simulation_geometry", "simulation_result", "sampled_state", "solver_observations")):
        _fail("purpose_mismatch", "/asset/role", "Shape-only presentation cannot claim simulation assets.")
    if asset["purpose"] == "physical_simulation" and asset["role"] in (*_MESH_ROLES, "model", "simulation_result", "sampled_state"):
        if asset["units"] != "m":
            _fail("units_mismatch", "/asset/units", "Physical geometry and spatial state require explicit metre units.")
    refs = [item["artifact_ref"] for item in asset["dependencies"]]
    if len(set(refs)) != len(refs) or asset["artifact_ref"] in refs:
        _fail("invalid_asset", "/asset/dependencies", "Dependencies must be unique and must not reference self.")
    return copy.deepcopy(asset)


def validate_render_inputs(model: dict, simulation: dict | None, *, task_id: str, purpose: str) -> dict:
    if purpose not in _PURPOSES:
        _fail("purpose_mismatch", "/purpose", "Unknown render purpose.")
    checked_model = validate_asset_descriptor(model, task_id=task_id)
    allowed_roles = ("model", "display_geometry") if purpose == "model_preview" else ("model",)
    if checked_model["purpose"] != purpose or checked_model["role"] not in allowed_roles:
        _fail("purpose_mismatch", "/model", "Render model role or purpose is inconsistent.")
    if purpose == "model_preview":
        if simulation is not None:
            _fail("purpose_mismatch", "/simulation", "Shape-only presentation has no simulation dependency.")
        return {"purpose": purpose, "model": checked_model, "simulation": None}
    if simulation is None:
        _fail("missing_simulation", "/simulation", "Physical rendering requires saved simulation data.")
    checked_simulation = validate_asset_descriptor(simulation, task_id=task_id, expected_role="simulation_result")
    if checked_simulation["purpose"] != purpose:
        _fail("purpose_mismatch", "/simulation/purpose", "Simulation purpose differs from render purpose.")
    expected = {key: checked_model[key] for key in ("artifact_ref", "schema", "sha256")}
    if expected not in checked_simulation["dependencies"]:
        _fail("model_dependency_mismatch", "/simulation/dependencies", "Simulation must reference this exact model and hash.")
    if checked_model["units"] != checked_simulation["units"]:
        _fail("units_mismatch", "/simulation/units", "Model and simulation coordinate units differ.")
    if checked_model["coordinate_frame"] != checked_simulation["coordinate_frame"]:
        _fail("invalid_asset", "/simulation/coordinate_frame", "Model and simulation frames differ; an audited transform is required.")
    return {"purpose": purpose, "model": checked_model, "simulation": checked_simulation}


def validate_geometry_binding(binding: dict, source: dict, target: dict, *, task_id: str) -> dict:
    """Check dependency identity; retain the existing independent geometry proof."""
    left = validate_asset_descriptor(source, task_id=task_id)
    right = validate_asset_descriptor(target, task_id=task_id)
    _json_value(binding, "/binding", "invalid_binding")
    _validate(binding, _document()["$defs"]["GeometryBinding"], "/binding", "invalid_binding")
    if left["purpose"] != right["purpose"]:
        _fail("purpose_mismatch", "/binding", "Binding cannot change asset purpose.")
    for prefix, asset in (("source", left), ("target", right)):
        for suffix, field, code in (("ref", "artifact_ref", "invalid_binding"),
                                    ("sha256", "sha256", "artifact_hash_mismatch"),
                                    ("topology_sha256", "topology_sha256", "topology_mismatch")):
            if binding[prefix + "_" + suffix] != asset.get(field):
                _fail(code, "/binding/" + prefix + "_" + suffix, "Binding differs from its exact asset dependency.")
    mode = binding["mode"]
    if mode == "particle_visual_surface":
        if (left["role"], right["role"], left["purpose"]) != ("sampled_state", "video", "physical_simulation"):
            _fail("invalid_binding", "/binding/mode", "Particle surface presentation requires sampled state and a video.")
        if any(binding[key] is not None for key in ("source_topology_sha256", "target_topology_sha256")):
            _fail("topology_mismatch", "/binding", "Particle visual surfaces do not declare a simulated triangle topology.")
        expected_units, expected_frame = left["units"], left["coordinate_frame"]
    else:
        permitted = {"identity": None, "raw_display_subset": ("raw_geometry", "display_geometry"),
                     "bounded_surface": ("raw_geometry", "display_geometry"),
                     "simulation_vertices": ("simulation_geometry", "display_geometry")}
        if permitted[mode] is not None and (left["role"], right["role"]) != permitted[mode]:
            _fail("invalid_binding", "/binding/mode", "Binding mode does not match asset roles.")
        if mode in ("raw_display_subset", "bounded_surface") and left["purpose"] != "model_preview":
            _fail("purpose_mismatch", "/binding/mode", "Display cleanup is not a physical deformation binding.")
        if mode == "simulation_vertices" and left["purpose"] != "physical_simulation":
            _fail("purpose_mismatch", "/binding/mode", "Simulated vertices require a physical result.")
        if left["units"] != right["units"]:
            _fail("units_mismatch", "/binding/units", "Binding cannot silently change units.")
        if left["coordinate_frame"] != right["coordinate_frame"]:
            _fail("invalid_binding", "/binding/coordinate_frame", "Binding cannot silently change coordinate frames.")
        if mode in ("identity", "simulation_vertices") and left.get("topology_sha256") != right.get("topology_sha256"):
            _fail("topology_mismatch", "/binding", "Direct vertex presentation requires the same topology.")
        if mode == "identity" and left["sha256"] != right["sha256"]:
            _fail("artifact_hash_mismatch", "/binding", "Identity requires the same immutable geometry bytes.")
        expected_units, expected_frame = left["units"], left["coordinate_frame"]
    if binding["units"] != expected_units:
        _fail("units_mismatch", "/binding/units", "Binding units differ from the source geometry.")
    if binding["coordinate_frame"] != expected_frame:
        _fail("invalid_binding", "/binding/coordinate_frame", "Binding frame differs from the source geometry.")
    return copy.deepcopy(binding)
