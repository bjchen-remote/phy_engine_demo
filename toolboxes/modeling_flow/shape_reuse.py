"""Report the unavailable shape-reuse route without loading a model or files.

The pinned MLX source exposes decoder components, but this adapter has no
GPU-verified latent extraction/cache/redecode implementation. Recognizing
configuration pins must never promote that prospective route to a capability.
"""
from __future__ import annotations

from .contracts import FlowError, MODEL_SUBFOLDER


SHAPE_REUSE_CAPABILITY_SCHEMA = "modeling-flow-shape-reuse-capability/1"
_KNOWN_RUNTIME_PINS = {
    "backend": "hunyuan3d_mlx",
    "upstream_commit": "292331f4d26ddb80b9dcea6bcb5629ff82f12b82",
    "checkpoint_id": "tencent/Hunyuan3D-2mini",
    "checkpoint_revision": "f90a0f7df7d5e6f71109cf333f6a95a0ae3194a6",
    "model_subfolder": MODEL_SUBFOLDER,
}


def shape_reuse_capability(config: dict) -> dict:
    """Return an unsupported status from in-memory config values only.

    This is not a runtime health check: it does not inspect source, checkpoint
    files, devices or installed dependencies. Callers with an existing validated
    config may report this alongside health. Config filenames are not accepted.
    """
    if type(config) is not dict:
        raise FlowError("invalid_config", "Shape reuse requires an in-memory configuration object; paths are not accepted")
    recognized = all(
        type(config.get(key, MODEL_SUBFOLDER if key == "model_subfolder" else None)) is str
        and config.get(key, MODEL_SUBFOLDER if key == "model_subfolder" else None) == expected
        for key, expected in _KNOWN_RUNTIME_PINS.items()
    )
    if recognized:
        code = "shape_reuse_unverified"
        reason = ("Shape reuse is unavailable: latent extraction, cache and redecoding "
                  "have not been implemented and verified on the pinned MLX GPU runtime.")
    else:
        code = "shape_reuse_unsupported_runtime"
        reason = "Shape reuse is unavailable for these backend, source and checkpoint pins."
    return {"schema_version": SHAPE_REUSE_CAPABILITY_SCHEMA,
            "supported": False, "status": "unsupported", "code": code,
            "reason": reason, "inference_verified": False,
            "runtime_pins_recognized": recognized}


def reuse_shape(config: dict, request=None, output=None):
    """Fail before dependency loading, request/path access or artifact creation.

    ``request`` and ``output`` reserve the worker call shape only. They are never
    evaluated or treated as paths. No latent-cache or optimization contract is
    supported, including when a caller supplies a claimed verification flag.
    """
    capability = shape_reuse_capability(config)
    raise FlowError(capability["code"], capability["reason"],
                    {"shape_reuse": capability})
