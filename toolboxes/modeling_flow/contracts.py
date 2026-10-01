"""Bounded JSON contracts, immutable artifacts and local checkpoint pins."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile


CONFIG_SCHEMA = "modeling-flow-config/1"
REQUEST_SCHEMA = "modeling-flow-request/1"
PREVIEW_REQUEST_SCHEMA = "modeling-flow-preview-request/1"
RESULT_SCHEMA = "modeling-flow-result/1"
RECEIPT_SCHEMA = "modeling-flow-receipt/1"
MODEL_SUBFOLDER = "hunyuan3d-dit-v2-mini"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 16 * 1024 * 1024
MAX_SIMULATION_VERTICES = 4096
MAX_SIMULATION_FACES = 8192


class FlowError(ValueError):
    """An actionable failure that may safely cross the process boundary."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        self.code = code
        self.details = details or {}
        super().__init__(message)


def canonical_bytes(value) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, OverflowError) as error:
        raise FlowError("invalid_json", "JSON contains unsupported or nonfinite values") from error


def digest(value) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FlowError("invalid_json", "Duplicate JSON key: " + key)
        result[key] = value
    return result


def read_json(path: str | Path) -> dict:
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise FlowError("invalid_json", "Input must be a bounded regular JSON file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(
                               FlowError("invalid_json", "Nonfinite JSON value")))
    except (UnicodeError, ValueError) as error:
        if isinstance(error, FlowError):
            raise
        raise FlowError("invalid_json", "Input is not valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise FlowError("invalid_json", "Input must be a JSON object")
    canonical_bytes(value)
    return value


def write_json(path: Path, value: dict) -> None:
    """Atomically create an artifact without replacing a previous one."""
    if path.exists() or path.is_symlink():
        raise FlowError("output_exists", "Output artifact already exists")
    data = canonical_bytes(value) + b"\n"
    descriptor, name = tempfile.mkstemp(prefix=".flow-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def output_directory(path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.is_symlink():
        raise FlowError("invalid_output", "Output must be an absolute, dedicated directory")
    resolved = candidate.resolve()
    if resolved in (Path(resolved.anchor), Path.home().resolve()):
        raise FlowError("invalid_output", "Output must be a dedicated run directory")
    if resolved.exists() and (not resolved.is_dir() or any(resolved.iterdir())):
        raise FlowError("output_exists", "Output must be new or empty")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _keys(value: dict, required: set[str], optional: set[str], kind: str):
    if not required.issubset(value):
        raise FlowError("invalid_" + kind, "Missing fields: " + ", ".join(sorted(required - value.keys())))
    unknown = value.keys() - required - optional
    if unknown:
        raise FlowError("invalid_" + kind, "Unknown fields: " + ", ".join(sorted(unknown)))


def _finite_positive(value, name: str, upper: float) -> float:
    if type(value) not in (int, float) or not 0 < value <= upper or not math.isfinite(value):
        raise FlowError("invalid_request", name + " must be finite, positive and bounded")
    return float(value)


def _integer(value, name: str, lower: int, upper: int) -> int:
    if type(value) is not int or not lower <= value <= upper:
        raise FlowError("invalid_request", name + " must be an integer within its supported range")
    return value


def _absolute_directory(value, name: str) -> Path:
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise FlowError("invalid_config", name + " must be an absolute directory path")
    path = Path(value)
    if path.is_symlink() or not path.is_dir():
        raise FlowError("runtime_not_installed", name + " must be a local directory")
    return path.resolve()


def load_config(source: str | Path | dict) -> dict:
    value = read_json(source) if not isinstance(source, dict) else dict(source)
    _keys(value, {"schema_version", "upstream_root", "upstream_commit", "weights_root",
                  "checkpoint_id", "checkpoint_revision", "weight_files"},
          {"backend", "device", "model_subfolder", "variant", "dtype",
           "background_removal", "foreground_model"}, "config")
    if value["schema_version"] != CONFIG_SCHEMA:
        raise FlowError("invalid_config", "Unsupported flow configuration schema")
    for name in ("upstream_commit", "checkpoint_revision"):
        if not isinstance(value[name], str) or not re.fullmatch(r"[0-9a-f]{40}", value[name]):
            raise FlowError("invalid_config", name + " must be a pinned 40-character commit SHA")
    if value["checkpoint_id"] != "tencent/Hunyuan3D-2mini":
        raise FlowError("invalid_config", "This adapter supports the official Hunyuan3D-2mini checkpoint")
    for name, expected in (("model_subfolder", MODEL_SUBFOLDER), ("variant", "fp16")):
        if value.get(name, expected) != expected:
            raise FlowError("invalid_config", name + " is unsupported by this adapter")
        value[name] = expected
    value["backend"] = value.get("backend", "hunyuan3d_torch")
    if value["backend"] not in ("hunyuan3d_torch", "hunyuan3d_mlx"):
        raise FlowError("invalid_config", "Unsupported local flow matching backend")
    value["device"] = value.get("device", "metal" if value["backend"] == "hunyuan3d_mlx" else "mps")
    if ((value["backend"] == "hunyuan3d_mlx" and value["device"] != "metal")
            or (value["backend"] == "hunyuan3d_torch" and value["device"] not in ("mps", "cpu"))):
        raise FlowError("invalid_config", "MLX requires metal; PyTorch requires mps or explicitly selected cpu")
    value["dtype"] = value.get("dtype", "float32")
    if value["dtype"] not in ("float16", "float32") or (value["device"] == "cpu" and value["dtype"] != "float32"):
        raise FlowError("invalid_config", "dtype must be explicit float16 or float32 on MPS; CPU requires float32")
    value["upstream_root"] = str(_absolute_directory(value["upstream_root"], "upstream_root"))
    value["weights_root"] = str(_absolute_directory(value["weights_root"], "weights_root"))
    value["background_removal"] = value.get("background_removal", "none")
    if value["background_removal"] not in ("none", "u2net"):
        raise FlowError("invalid_config", "background_removal must be none or explicitly installed u2net")
    foreground = value.get("foreground_model")
    if foreground is not None:
        if (not isinstance(foreground, dict) or set(foreground) != {"path", "sha256"}
                or foreground.get("path") != "segmentation/u2net.onnx"
                or not isinstance(foreground.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", foreground["sha256"])):
            raise FlowError("invalid_config", "foreground_model must pin segmentation/u2net.onnx and its SHA-256")
    if value["background_removal"] == "u2net" and foreground is None:
        raise FlowError("invalid_config", "u2net background removal requires an installed foreground_model pin")
    inventory = value["weight_files"]
    if not isinstance(inventory, list) or not 2 <= len(inventory) <= 64:
        raise FlowError("invalid_config", "weight_files must pin the local checkpoint inventory")
    names = set()
    for item in inventory:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise FlowError("invalid_config", "Each weight pin needs only path and sha256")
        path, checksum = item["path"], item["sha256"]
        if (not isinstance(path, str) or Path(path).is_absolute() or ".." in Path(path).parts
                or not path.startswith(MODEL_SUBFOLDER + "/") or path in names):
            raise FlowError("invalid_config", "Weight pins must be unique relative checkpoint paths")
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            raise FlowError("invalid_config", "Weight sha256 must be a lowercase SHA-256 digest")
        names.add(path)
    expected = {MODEL_SUBFOLDER + "/config.yaml", MODEL_SUBFOLDER + "/model.fp16.safetensors"}
    if not expected.issubset(names):
        raise FlowError("invalid_config", "Pin both config.yaml and model.fp16.safetensors")
    return value


def verify_weights(config: dict) -> list[dict]:
    root = Path(config["weights_root"])
    verified = []
    for item in config["weight_files"]:
        path = root / item["path"]
        if (not path.is_file() or not path.resolve().is_relative_to(root)
                or path.stat().st_size == 0):
            raise FlowError("weights_missing", "Missing or unsafe pinned checkpoint file: " + item["path"])
        actual = file_digest(path)
        if actual != item["sha256"]:
            raise FlowError("checkpoint_pin_mismatch", "Checkpoint digest mismatch: " + item["path"])
        verified.append({**item, "bytes": path.stat().st_size})
    return verified


def verify_foreground_model(config: dict) -> dict | None:
    if config["background_removal"] == "none":
        return None
    item = config["foreground_model"]
    root = Path(config["weights_root"])
    path = root / item["path"]
    if not path.resolve().is_relative_to(root):
        raise FlowError("foreground_model_missing", "Configured local U2-Net foreground model is unavailable")
    from .segmentation import verify_foreground_model as verify_graph
    return {"path": item["path"], **verify_graph(path, item["sha256"])}


def load_request(source: str | Path | dict) -> dict:
    value = read_json(source) if not isinstance(source, dict) else dict(source)
    preview = value.get("schema_version") == PREVIEW_REQUEST_SCHEMA
    required = {"schema_version", "image_path"} if preview else {
        "schema_version", "image_path", "physical_extent_m", "material"}
    optional = {"seed", "scale_axis", "num_inference_steps", "guidance_scale",
                "octree_resolution", "num_chunks", "allow_decimation"}
    if preview:
        optional.update({"physical_extent_m", "display_cleanup"})
    _keys(value, required, optional, "request")
    if value["schema_version"] not in (REQUEST_SCHEMA, PREVIEW_REQUEST_SCHEMA):
        raise FlowError("invalid_request", "Unsupported flow request schema")
    image = value["image_path"]
    if not isinstance(image, str) or not Path(image).is_absolute():
        raise FlowError("invalid_image", "image_path must be an absolute local file path")
    path = Path(image)
    if (path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_IMAGE_BYTES
            or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp")):
        raise FlowError("invalid_image", "Image must be a bounded regular PNG, JPEG or WebP file")
    value["image_path"] = str(path.resolve())
    if "physical_extent_m" in value:
        value["physical_extent_m"] = _finite_positive(value["physical_extent_m"], "physical_extent_m", 10000)
    value["scale_axis"] = value.get("scale_axis", "max")
    if value["scale_axis"] not in ("max", "x", "y", "z"):
        raise FlowError("invalid_request", "scale_axis must be max, x, y or z in the generated mesh coordinate frame")
    if not preview:
        material = value["material"]
        if not isinstance(material, dict):
            raise FlowError("invalid_request", "material must specify name and density_kg_m3")
        _keys(material, {"name", "density_kg_m3"}, set(), "request")
        if (not isinstance(material["name"], str) or not 1 <= len(material["name"]) <= 200
                or any(ord(char) < 32 for char in material["name"])):
            raise FlowError("invalid_request", "material.name must be a short plain string")
        value["material"] = {"name": material["name"], "density_kg_m3":
                             _finite_positive(material["density_kg_m3"], "density_kg_m3", 100000)}
    value["seed"] = _integer(value.get("seed", 0), "seed", 0, 2**32 - 1)
    value["num_inference_steps"] = _integer(value.get("num_inference_steps", 30), "num_inference_steps", 1, 100)
    value["octree_resolution"] = _integer(value.get("octree_resolution", 128), "octree_resolution", 16, 256)
    value["num_chunks"] = _integer(value.get("num_chunks", 2000), "num_chunks", 256, 8000)
    value["guidance_scale"] = _finite_positive(value.get("guidance_scale", 5.0), "guidance_scale", 20)
    value["allow_decimation"] = value.get("allow_decimation", not preview)
    if type(value["allow_decimation"]) is not bool:
        raise FlowError("invalid_request", "allow_decimation must be a boolean")
    if preview and value["allow_decimation"]:
        raise FlowError("invalid_request", "Display previews do not decimate generated geometry")
    if preview:
        value["display_cleanup"] = value.get("display_cleanup", "conservative")
        if value["display_cleanup"] not in ("conservative", "none"):
            raise FlowError("invalid_request", "display_cleanup must be conservative or none")
    return value


def artifact(path: Path, root: Path, role: str, media_type: str) -> dict:
    if path.is_symlink() or not path.is_file():
        raise FlowError("invalid_artifact", "Artifact must be a regular local file")
    return {"path": path.relative_to(root).as_posix(), "role": role, "media_type": media_type,
            "bytes": path.stat().st_size, "sha256": file_digest(path)}
