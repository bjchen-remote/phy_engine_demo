"""Declarative external-render contract; this module makes no network calls.

Commercial diffusion providers may create an illustration alongside the
scientific video. Their receipt never authorizes quantitative measurements,
and the requesting host must enforce upload consent and a spending cap.
"""

from __future__ import annotations

import re

from .artifacts import PresentationError, canonical, digest


def provider_request(*, provider: str, source_result_sha256: str,
                     reference_video_sha256: str, prompt: str,
                     upload_allowed: bool, max_cost_usd: float) -> dict:
    import math
    if not isinstance(provider, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", provider):
        raise PresentationError("provider must be a configured provider identifier")
    if any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
           for value in (source_result_sha256, reference_video_sha256)):
        raise PresentationError("provider references require SHA-256 digests")
    if not isinstance(prompt, str) or not 1 <= len(prompt) <= 4000:
        raise PresentationError("provider prompt must contain 1 to 4000 characters")
    if upload_allowed is not True:
        raise PresentationError("external asset upload has not been authorized", "external_upload_disabled")
    if type(max_cost_usd) not in (int, float) or not math.isfinite(max_cost_usd) or max_cost_usd <= 0:
        raise PresentationError("external rendering requires a positive explicit spending cap")
    request = {"schema": "illustrative-render-request/1", "provider": provider,
               "source_result_sha256": source_result_sha256, "reference_video_sha256": reference_video_sha256,
               "prompt": prompt, "max_cost_usd": max_cost_usd, "upload_allowed": True,
               "asset_scope": "reference_video_only", "measurement_source": False,
               "required_label": "AI-stylized illustration; use the scientific video and data package for physical interpretation."}
    return {**request, "request_sha256": digest(canonical(request))}


def validate_provider_receipt(request: dict, receipt: dict) -> dict:
    import math
    binding = {key: value for key, value in request.items() if key != "request_sha256"}
    if request.get("request_sha256") != digest(canonical(binding)):
        raise PresentationError("external rendering request digest is invalid")
    if receipt.get("provider") != request["provider"] or receipt.get("request_sha256") != request["request_sha256"]:
        raise PresentationError("external receipt is not bound to this provider request")
    cost = receipt.get("cost_usd")
    if type(cost) not in (int, float) or not math.isfinite(cost) or not 0 <= cost <= request["max_cost_usd"]:
        raise PresentationError("external receipt has invalid or over-budget cost")
    artifact_digest = receipt.get("sha256")
    if not isinstance(artifact_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", artifact_digest):
        raise PresentationError("external receipt must identify the downloaded artifact digest")
    if type(receipt.get("bytes")) is not int or receipt["bytes"] <= 0 or receipt.get("media_type") != "video/mp4":
        raise PresentationError("external receipt must describe a nonempty MP4")
    return {"schema": "illustrative-render-receipt/1", "provider": request["provider"],
            "request_sha256": request["request_sha256"], "source_result_sha256": request["source_result_sha256"],
            "sha256": artifact_digest, "bytes": receipt["bytes"], "media_type": "video/mp4", "cost_usd": cost,
            "measurement_source": False, "generative": True, "fidelity": "illustrative_only",
            "required_label": request["required_label"]}
