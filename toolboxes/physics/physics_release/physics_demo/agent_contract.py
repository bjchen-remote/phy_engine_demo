"""Small, uniform response state machine for tool-using agents."""

from __future__ import annotations

from typing import Any


PROTOCOL_VERSION = "agent-physics/1.0"


def failure(stage: str, code: str, path: str, message: str, *,
            retryable: bool = False, suggestion: str | None = None,
            **context: Any) -> dict[str, Any]:
    """Build the shared error envelope without changing public error details."""
    error = {"code": code, "path": path, "message": message, "retryable": retryable}
    if suggestion is not None:
        error["suggestion"] = suggestion
    return {"ok": False, "stage": stage, "errors": [error], **context}


def tool_action(
    tool: str,
    reason: str,
    *,
    when: str | None = None,
    preconditions: list[str] | None = None,
) -> dict[str, Any]:
    action: dict[str, Any] = {"kind": "call_tool", "tool": tool, "reason": reason}
    if when:
        action["when"] = when
    if preconditions:
        action["preconditions"] = preconditions
    return action


def respond_action(reason: str) -> dict[str, Any]:
    return {"kind": "respond_to_user", "reason": reason}


def choose_action(decision: str, options: list[dict[str, Any]]) -> dict[str, Any]:
    return {"kind": "choose", "decision": decision, "options": options}


def guided(payload: dict[str, Any], status: str, next_action: dict[str, Any]) -> dict[str, Any]:
    """Attach fields that let a modest agent advance without prose inference."""
    payload["protocol_version"] = PROTOCOL_VERSION
    payload["status"] = status
    payload["next_action"] = next_action
    return payload


def guide_tool_result(tool: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Apply one deterministic next-step contract to any public tool result."""
    if "protocol_version" in payload:
        return payload
    if not payload.get("ok"):
        errors = payload.get("errors", [])
        stage = payload.get("stage", "unknown")
        retryable = any(bool(error.get("retryable")) for error in errors if isinstance(error, dict))
        if stage in {"validate", "patch"} and errors:
            retryable = not any(
                error.get("retryable") is False for error in errors if isinstance(error, dict)
            )
        status = {
            "validate": "needs_scene_fix",
            "patch": "needs_scene_fix",
            "estimate": "over_budget",
            "arguments": "needs_argument_fix",
            "environment": "environment_unavailable",
            "output": "needs_output_directory",
            "simulate": "simulation_failed",
            "video": "video_failed",
            "quality": "failed_quality_gate",
            "inspect": "inspection_failed",
        }.get(stage, "request_failed")
        if not retryable and stage == "arguments":
            status = "input_rejected"
        if tool == "physics_inspect" and isinstance(payload.get("quality_gate"), dict):
            return guided(
                payload,
                "failed_quality_gate",
                respond_action("Report the failed checks and do not claim that an MP4 was verified."),
            )
        if retryable:
            if stage in {"validate", "patch", "estimate", "simulate", "quality"}:
                return guided(payload, status, tool_action(
                    "physics_patch",
                    "Apply the structured scene correction, then call physics_prepare before one new simulation; stop after two failed corrections.",
                    preconditions=[
                        "Start from the exact scene used for the failed run.",
                        "Do not rerun the unchanged failed scene.",
                    ],
                ))
            return guided(
                payload,
                status,
                tool_action(tool, "Correct the argument, output directory, or video environment once, then retry; stop after two failed corrections."),
            )
        return guided(payload, status, respond_action("Explain the unsupported or non-retryable boundary without inventing a result."))

    if tool == "physics_capabilities":
        return guided(payload, "capabilities_ready", choose_action(
            "Obtain or author a complete scene_json first. Resolve a named liquid only after the target fluid entity ID is known.",
            [
                tool_action("physics_system", "Build a pendulum or double pendulum from physical parameters."),
                tool_action("physics_example", "Load the closest supported starting scene."),
                tool_action(
                    "physics_liquid",
                    "Resolve a named liquid after obtaining the complete scene that will receive its patch operations.",
                    when="A complete scene_json already exists and the target fluid entity ID is known.",
                    preconditions=[
                        "Do not call this as a scene-construction step.",
                        "The scene contains the target fluid entity ID.",
                    ],
                ),
                tool_action(
                    "physics_prepare",
                    "Validate and plan a complete custom scene after applying every named-liquid preset and explicit edit.",
                    when="A complete custom scene_json already exists and no requested preset or edit remains to apply.",
                ),
            ],
        ))
    if tool == "physics_liquid":
        return guided(payload, "liquid_preset_ready", choose_action(
            "This result contains patch operations, not scene_json. If no complete scene exists, first load one with physics_example, build an applicable named system with physics_system, or author scene-v1; then call physics_liquid again with a fluid ID from that scene. Choose physics_patch only while holding that complete scene_json.",
            [
                tool_action(
                    "physics_patch",
                    "Apply patch_arguments.operations to the complete scene_json, then prepare the patched result.",
                    when="A complete scene_json containing the target fluid entity is already available.",
                    preconditions=[
                        "Pass that scene_json separately to physics_patch.",
                        "Do not call physics_patch with patch operations alone.",
                    ],
                ),
                tool_action(
                    "physics_example",
                    "Load the closest catalog scene first, then call physics_liquid again with its fluid entity ID.",
                    when="No complete scene_json exists and a catalog scene matches the requested event.",
                ),
                tool_action(
                    "physics_system",
                    "Build the requested named system first; call physics_liquid again only if the resulting scene contains the target fluid entity.",
                    when="No complete scene_json exists and the request is supported by a named system factory.",
                ),
            ],
        ))
    if tool == "physics_example":
        return guided(payload, "example_loaded", choose_action(
            "Compare every explicit user value with the example.",
            [
                tool_action("physics_liquid", "Resolve the requested named liquid for a fluid entity in this scene before patching it.", when="The prompt names water, honey, glue, or molten lead."),
                tool_action("physics_patch", "Patch all explicit differences atomically.", when="The prompt changes any example value."),
                tool_action("physics_prepare", "Validate and plan the unchanged example.", when="The example already matches the prompt."),
            ],
        ))
    if tool == "physics_mesh":
        return guided(payload, "mesh_ready", tool_action(
            "physics_patch", "Use mesh_json as value_json for /entities/@ID/mesh on a mesh example; preserve provenance and then prepare the complete scene."))
    if tool == "physics_system":
        return guided(payload, "system_built", tool_action(
            "physics_prepare", "Pass scene_json unchanged to prepare; system construction does not run or approve a simulation."))
    if tool == "physics_patch":
        return guided(payload, "scene_patched", tool_action("physics_prepare", "Validate, normalize, and plan the patched scene."))
    if tool == "physics_validate":
        return guided(payload, "scene_valid", tool_action("physics_estimate", "Estimate cost for this exact normalized scene."))
    if tool == "physics_estimate":
        fits = bool(payload.get("plan", {}).get("timing_estimate", {}).get("fits_budget"))
        if fits:
            return guided(payload, "ready_to_simulate", tool_action("physics_simulate", "Run the exact scene after reporting its physical duration and p50–p90 estimate."))
        return guided(payload, "over_budget", tool_action("physics_patch", "Propose and apply an allowed simplification; do not silently alter an explicit requirement."))
    if tool == "physics_prepare":
        return guided(payload, "ready_to_simulate", tool_action(
            "physics_simulate",
            "Use returned scene_json unchanged and a new dedicated output directory.",
            preconditions=[
                "Every accepted explicit user requirement is represented in the prepared scene.",
                "No requested feature was omitted or replaced without prior authorization.",
                "Every plan adjustment is compatible with the user's stated fidelity requirements.",
            ],
        ))
    if tool == "physics_query":
        return guided(payload, "query_completed", respond_action("Report each answer status, units, observation window, and time bracket. not_observed means no qualifying sampled event in this window; never claim eternal stability from a finite simulation."))
    if tool == "physics_simulate":
        passed = bool(payload.get("quality_gate", {}).get("passed"))
        if passed:
            if payload.get("measurements"):
                return guided(payload, "completed", respond_action("Return the MP4 and the declared quantitative answers with status, units, observation window, and sampling bracket. Use physics_query for a selected declared series; preserve model and finite-window limitations."))
            return guided(payload, "completed", respond_action("Return the MP4 path, actual runtime and diagnostics, assumptions, and fidelity limits."))
        return guided(payload, "failed_quality_gate", tool_action("physics_inspect", "Inspect the saved result before deciding whether one corrected rerun is appropriate."))
    if tool == "physics_inspect":
        passed = bool(payload.get("quality_gate", {}).get("passed"))
        if passed and payload.get("quality_gate", {}).get("requires_video") is False:
            return guided(
                payload,
                "debug_result_no_video",
                tool_action("physics_simulate", "Run the prepared scene through the normal video-producing path before delivery."),
            )
        status = "completed" if passed else "failed_quality_gate"
        reason = "Return the verified MP4 and measured diagnostics." if passed else "Explain the failed quality gate and its structured error."
        return guided(payload, status, respond_action(reason))
    return guided(payload, "completed", respond_action("Use the successful result."))
