"""JSON tool dispatcher suitable for a Claude-style client tool bridge."""

from __future__ import annotations

import json
from typing import Any

from .agent_contract import failure, guide_tool_result
from .catalog import example as load_example
from .jsonio import StrictJSONSemanticError, loads as strict_json_loads
from .limits import MAX_PATCH_OPERATIONS
from .core.liquids import liquid_preset
from .runner import capabilities, estimate, inspect, patch_scene, prepare, query, simulate, validate


MAX_SCENE_JSON_BYTES = 1_000_000


def _scene(arguments: dict[str, Any], field: str = "scene_json") -> Any:
    try:
        encoded = arguments[field]
        if not isinstance(encoded, str):
            raise ValueError(f"{field} must be a string")
        if len(encoded.encode("utf-8")) > MAX_SCENE_JSON_BYTES:
            raise ValueError(f"{field} exceeds the {MAX_SCENE_JSON_BYTES}-byte input limit")
        return strict_json_loads(encoded)
    except StrictJSONSemanticError:
        raise
    except (KeyError, json.JSONDecodeError, RecursionError, UnicodeError, ValueError) as error:
        raise ValueError(f"{field} is not valid JSON: {error}") from error


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        if name == "physics_capabilities":
            result = capabilities()
        elif name == "physics_liquid":
            if not isinstance(arguments, dict) or set(arguments) != {"preset", "entity_id"}:
                raise ValueError("physics_liquid accepts exactly preset and entity_id")
            entity_id = arguments["entity_id"]
            if not isinstance(entity_id, str) or not entity_id.strip() or len(entity_id) > 128:
                raise ValueError("entity_id must be a non-empty string of at most 128 characters")
            selector = entity_id.replace("~", "~0").replace("/", "~1")
            preset = liquid_preset(arguments["preset"])
            fields = {"preset": preset["name"], "properties": preset["properties"]}
            operations = [
                {"op": "add", "path": f"/entities/@{selector}/preset",
                 "value_json": json.dumps(preset["name"])},
                {"op": "add", "path": f"/entities/@{selector}/properties",
                 "value_json": json.dumps(preset["properties"], separators=(",", ":"))},
            ]
            result = {
                "ok": True,
                "preset": preset,
                "entity_id": entity_id,
                "entity_fields": fields,
                "patch_arguments": {"operations": operations},
                "model_scope": preset["scope"],
            }
        elif name == "physics_example":
            result = load_example(arguments["name"])
        elif name == "physics_system":
            from .systems import build_system, BURST_SCOPE
            if not isinstance(arguments, dict) or set(arguments) != {"spec_json"}:
                raise ValueError("physics_system accepts exactly one argument: spec_json")
            spec = _scene(arguments, "spec_json")
            scene = build_system(spec)
            result = {"ok": True, "scene": scene,
                      "scene_json": json.dumps(scene, separators=(",", ":"), ensure_ascii=False),
                      "model_scope": BURST_SCOPE if spec["type"] == "ballistic_burst" else "Ideal planar point masses, massless rigid rods, fixed anchor and uniform gravity. No collision or long-term chaos/stability guarantee."}
        elif name == "physics_mesh":
            from .meshes import build_mesh
            if not isinstance(arguments, dict) or set(arguments) != {"spec_json"}:
                raise ValueError("physics_mesh accepts exactly one argument: spec_json")
            mesh = build_mesh(_scene(arguments, "spec_json"))
            result = {"ok": True, "mesh_json": json.dumps(mesh, separators=(",", ":"), ensure_ascii=False),
                      "audit": mesh["metadata"],
                      "model_scope": "Agent-authored geometry; image depth and unseen surfaces are assumptions, not measured reconstruction."}
        elif name == "physics_validate":
            result = validate(_scene(arguments))
        elif name == "physics_estimate":
            result = estimate(_scene(arguments), arguments.get("budget_seconds"))
        elif name == "physics_prepare":
            result = prepare(_scene(arguments), arguments.get("budget_seconds"))
        elif name == "physics_simulate":
            result = simulate(
                _scene(arguments),
                arguments["output_dir"],
                arguments.get("budget_seconds"),
                make_video=True,
            )
        elif name == "physics_inspect":
            result = inspect(arguments["result_path"])
        elif name == "physics_query":
            result = query(arguments["result_path"], arguments.get("query_id"))
        elif name == "physics_patch":
            raw_operations = arguments["operations"]
            if not isinstance(raw_operations, list):
                raise ValueError("operations must be an array")
            if len(raw_operations) > MAX_PATCH_OPERATIONS:
                raise ValueError(f"operations may contain at most {MAX_PATCH_OPERATIONS} items")
            operations = []
            for operation in raw_operations:
                if not isinstance(operation, dict):
                    raise ValueError("every patch operation must be an object")
                converted = {"op": operation["op"], "path": operation["path"]}
                if not isinstance(converted["path"], str) or len(converted["path"]) > 1024:
                    raise ValueError("patch path must be a string of at most 1024 characters")
                if operation["op"] != "remove":
                    encoded_value = operation["value_json"]
                    if not isinstance(encoded_value, str) or len(encoded_value.encode("utf-8")) > MAX_SCENE_JSON_BYTES:
                        raise ValueError(f"value_json must be a string of at most {MAX_SCENE_JSON_BYTES} bytes")
                    converted["value"] = strict_json_loads(encoded_value)
                operations.append(converted)
            result = patch_scene(_scene(arguments), operations)
        else:
            result = failure(
                'dispatch', 'unknown_tool', 'name',
                f'Unknown tool {name!r}.',
            )
    except StrictJSONSemanticError as error:
        result = failure(
            'arguments', 'ambiguous_json', 'arguments',
            str(error),
            suggestion='Reject the ambiguous value; do not guess, clamp, or silently choose between duplicate keys.',
        )
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as error:
        result = failure(
            'arguments', 'invalid_arguments', 'arguments',
            str(error),
            retryable=True,
            suggestion='Call physics_capabilities, correct the named argument, and retry at most twice.',
        )
    except OSError as error:
        result = failure(
            'output', 'tool_io_failed', 'output_dir',
            str(error),
            retryable=True,
            suggestion='Choose a readable/writable dedicated path and retry the same operation once.',
        )
    return guide_tool_result(name, result)
