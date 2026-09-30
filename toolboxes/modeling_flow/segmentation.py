"""Pinned, offline U2-Net foreground masks using ONNX Runtime on CPU.

Algorithm references (audited 2026-10-01):
  U2-Net ac7e1c817ecab7c7dff5ce6b1abba61cd213ff29, Apache-2.0:
  https://github.com/xuebinqin/U-2-Net/blob/ac7e1c817ecab7c7dff5ce6b1abba61cd213ff29/data_loader.py
  rembg 202e42649a8492a7c49f808de36608a7d1cbbfe3, MIT:
  https://github.com/danielgatis/rembg/blob/202e42649a8492a7c49f808de36608a7d1cbbfe3/rembg/sessions/u2net.py
  https://github.com/danielgatis/rembg/blob/202e42649a8492a7c49f808de36608a7d1cbbfe3/rembg/sessions/base.py

This independent adapter implements the documented 320-square RGB/max-intensity
normalization and first-output saliency min/max normalization. No rembg import,
downloader, model-name registry, custom operator library or remote code is used.
The local model is read into bytes after pin verification, avoiding implicit
external-data file resolution by ONNX Runtime. A mask is an unverified estimate.
"""
from __future__ import annotations

import hashlib
import importlib
import os
from pathlib import Path
import re
import stat

from .contracts import FlowError, MAX_IMAGE_PIXELS


INPUT_EDGE = 320
MAX_MODEL_BYTES = 256 * 1024 * 1024
MODEL_SOURCE = 'https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2net.onnx'
MODEL_RELEASE_BYTES = 175997641
MODEL_RELEASE_MD5 = '60024c5c889badc19c04ad937298a77b'
PREPROCESSING_VERSION = 'u2net-320-rgb-max-imagenet-minmax-lanczos/1'


def _read_model(model_path: str | Path, checksum: str) -> tuple[bytes, dict]:
    if not isinstance(checksum, str) or re.fullmatch(r'[0-9a-f]{64}', checksum) is None:
        raise FlowError('foreground_model_invalid', 'Foreground model requires a lowercase SHA-256 pin')
    try:
        path = Path(model_path)
    except TypeError as error:
        raise FlowError('foreground_model_invalid', 'Foreground model must be a local absolute path') from error
    if not path.is_absolute() or path.suffix != '.onnx' or any(
            candidate.is_symlink() for candidate in (path, *path.parents)):
        raise FlowError('foreground_model_invalid', 'Foreground model must be an absolute ONNX path without symlinks')
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                             | getattr(os, 'O_NONBLOCK', 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_MODEL_BYTES:
            raise FlowError('foreground_model_invalid', 'Foreground model must be a bounded regular file')
        parts = []
        remaining = MAX_MODEL_BYTES + 1
        while remaining:
            block = os.read(descriptor, min(1024 * 1024, remaining))
            if not block:
                break
            parts.append(block)
            remaining -= len(block)
        content = b''.join(parts)
        if len(content) != info.st_size or len(content) > MAX_MODEL_BYTES:
            raise FlowError('foreground_model_invalid', 'Foreground model changed during its bounded read')
    except OSError as error:
        raise FlowError('foreground_model_missing', 'Cannot read the operator-installed foreground model') from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    actual = hashlib.sha256(content).hexdigest()
    if actual != checksum:
        raise FlowError('foreground_pin_mismatch', 'Foreground model does not match its SHA-256 pin')
    return content, {'model': 'u2net', 'sha256': actual, 'bytes': len(content),
                     'source': MODEL_SOURCE, 'source_license': 'Apache-2.0',
                     'conversion_source_license': 'MIT'}


def verify_foreground_model(model_path: str | Path, checksum: str) -> dict:
    """Read and verify the standalone graph without importing inference packages."""
    _, metadata = _read_model(model_path, checksum)
    return metadata


def _load_dependencies():
    try:
        return (importlib.import_module('numpy'), importlib.import_module('PIL.Image'),
                importlib.import_module('onnxruntime'))
    except (ImportError, OSError) as error:
        raise FlowError('foreground_dependency_missing',
                        'Offline U2-Net needs compatible numpy, Pillow and CPU onnxruntime') from error


def _input_tensor(image, np, Image):
    # Match rembg's exported U2-Net preprocessing rather than substituting /255:
    # its scale is the maximum intensity of this resized RGB image.
    resized = image.convert('RGB').resize((INPUT_EDGE, INPUT_EDGE), Image.Resampling.LANCZOS)
    pixels = np.asarray(resized, dtype=np.float32)
    pixels = pixels / max(float(pixels.max()), 1e-6)
    mean = np.asarray((0.485, 0.456, 0.406), dtype=np.float32)
    scale = np.asarray((0.229, 0.224, 0.225), dtype=np.float32)
    normalized = (pixels - mean) / scale
    return np.ascontiguousarray(normalized.transpose(2, 0, 1)[None], dtype=np.float32)


def _infer_mask(content: bytes, tensor, np, ort):
    try:
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        session = ort.InferenceSession(content, sess_options=options,
                                       providers=['CPUExecutionProvider'])
        if session.get_providers() != ['CPUExecutionProvider']:
            raise FlowError('foreground_provider_mismatch', 'Foreground removal must execute solely on the CPU')
        inputs, outputs = session.get_inputs(), session.get_outputs()
        if (len(inputs) != 1 or inputs[0].type != 'tensor(float)'
                or list(inputs[0].shape) != [1, 3, INPUT_EDGE, INPUT_EDGE] or not outputs):
            raise FlowError('foreground_model_invalid', 'Expected the fixed 1x3x320x320 U2-Net float input')
        output = outputs[0]
        if output.type != 'tensor(float)' or list(output.shape) != [1, 1, INPUT_EDGE, INPUT_EDGE]:
            raise FlowError('foreground_model_invalid', 'Expected U2-Net first-output 1x1x320x320 saliency')
        predictions = session.run([output.name], {inputs[0].name: tensor})
        if not isinstance(predictions, (list, tuple)) or len(predictions) != 1:
            raise FlowError('foreground_mask_invalid', 'Foreground model did not produce one requested saliency output')
        values = np.asarray(predictions[0])
        if (values.shape != (1, 1, INPUT_EDGE, INPUT_EDGE)
                or not np.issubdtype(values.dtype, np.floating) or not np.isfinite(values).all()):
            raise FlowError('foreground_mask_invalid', 'Foreground saliency has invalid shape, type or finite range')
        mask = values[0, 0].astype(np.float32)
        lower, upper = float(mask.min()), float(mask.max())
        if lower < -1e-6 or upper > 1 + 1e-6 or upper - lower <= 1e-6:
            raise FlowError('foreground_mask_invalid', 'Foreground saliency must be a nonconstant bounded probability mask')
        return np.clip((mask - lower) / (upper - lower), 0, 1)
    except FlowError:
        raise
    except Exception as error:
        # ONNX Runtime exposes several pybind exception classes. Normalize them
        # here so invalid/incompatible graphs never trigger a full-image fallback.
        raise FlowError('foreground_inference_failed', 'Offline CPU U2-Net inference failed: ' + str(error)) from error


def remove_foreground(image, model_path: str | Path, checksum: str):
    """Return (RGBA PIL image, receipt metadata); preserve existing alpha.

    Transparent input bypasses model loading entirely. For opaque input the
    verified local ONNX graph produces a soft mask; multiplication can only
    reduce alpha, never restore pixels from an existing transparent input.
    No image or weight is transmitted or downloaded by this function.
    """
    try:
        width, height = image.size
        if min(width, height) < 2 or width * height > MAX_IMAGE_PIXELS:
            raise FlowError('invalid_image', 'Foreground image dimensions exceed the local modeling budget')
        rgba = image.convert('RGBA')
        alpha = rgba.getchannel('A')
        lower, upper = alpha.getextrema()
    except FlowError:
        raise
    except (AttributeError, TypeError, ValueError) as error:
        raise FlowError('invalid_image', 'Foreground removal requires a decoded bounded PIL image') from error
    if upper == 0:
        raise FlowError('foreground_mask_invalid', 'Supplied image foreground is fully transparent')
    if lower < 255:
        return rgba, {'background_removal': 'skipped_provided_alpha', 'foreground_mask': 'provided_alpha',
                      'foreground_model': None, 'foreground_mask_verified': False,
                      'foreground_assumptions': ['Supplied alpha is preserved; its object boundary is unverified.']}
    content, model = _read_model(model_path, checksum)
    np, Image, ort = _load_dependencies()
    tensor = _input_tensor(rgba, np, Image)
    normalized = _infer_mask(content, tensor, np, ort)
    # The official ONNX adapter quantizes the normalized map before Lanczos
    # resampling. Retain soft boundaries; do not use arbitrary binary thresholds.
    mask = Image.fromarray((normalized * 255).astype(np.uint8)).resize(
        rgba.size, Image.Resampling.LANCZOS)
    source_alpha = np.asarray(alpha, dtype=np.uint16)
    generated_alpha = np.asarray(mask, dtype=np.uint16)
    combined = (source_alpha * generated_alpha // 255).astype(np.uint8)
    final_alpha = Image.fromarray(combined)
    bbox = final_alpha.getbbox()
    if bbox is None or bbox[2] - bbox[0] < 2 or bbox[3] - bbox[1] < 2:
        raise FlowError('foreground_mask_invalid', 'Estimated foreground is empty or too small for shape conditioning')
    result = rgba.copy()
    result.putalpha(final_alpha)
    return result, {'background_removal': 'u2net_cpu', 'foreground_mask': 'estimated_u2net_saliency',
                    'foreground_model': model, 'foreground_provider': 'CPUExecutionProvider',
                    'foreground_preprocessing': PREPROCESSING_VERSION,
                    'foreground_input_shape': [1, 3, INPUT_EDGE, INPUT_EDGE],
                    'foreground_alpha_bbox': list(bbox),
                    'foreground_occupancy_fraction': float(np.count_nonzero(combined) / combined.size),
                    'foreground_mask_verified': False,
                    'foreground_assumptions': [
                        'A salient-object mask is inferred locally; fine edges and selected object identity are unverified.',
                        'The mask estimates visible foreground only; it does not establish geometry, dimensions or material.',
                    ]}
