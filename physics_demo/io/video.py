"""Encode trajectory frames as MP4 using only macOS system frameworks.

AVFoundation H.264 is preferred.  Some sandboxed and virtual macOS hosts have
the framework but no usable hardware or software H.264 encoder; those hosts
fall back to ImageIO Motion JPEG in a directly muxed ISO BMFF container.
"""

from __future__ import annotations

import bisect
import copy
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Optional


class VideoEncodingError(RuntimeError):
    pass


class _NotMotionJPEG(VideoEncodingError):
    pass


def _be16(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(data):
        raise VideoEncodingError("Motion JPEG metadata is truncated.")
    return int.from_bytes(data[offset : offset + 2], "big")


def _be32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise VideoEncodingError("Motion JPEG metadata is truncated.")
    return int.from_bytes(data[offset : offset + 4], "big")


def _be64(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 8 > len(data):
        raise VideoEncodingError("Motion JPEG metadata is truncated.")
    return int.from_bytes(data[offset : offset + 8], "big")


def _boxes(data: bytes, start: int, end: int):
    offset = start
    while offset < end:
        if offset + 8 > end:
            raise VideoEncodingError("MP4 box header is truncated.")
        size = _be32(data, offset)
        kind = data[offset + 4 : offset + 8]
        header = 8
        if size == 1:
            if offset + 16 > end:
                raise VideoEncodingError("MP4 extended box header is truncated.")
            size = _be64(data, offset + 8)
            header = 16
        elif size == 0:
            size = end - offset
        if size < header or offset + size > end:
            raise VideoEncodingError("MP4 box size is invalid.")
        yield kind, offset + header, offset + size
        offset += size
    if offset != end:
        raise VideoEncodingError("MP4 boxes do not fill their parent.")


def _child(data: bytes, start: int, end: int, kind: bytes):
    found = [box for box in _boxes(data, start, end) if box[0] == kind]
    if len(found) != 1:
        raise VideoEncodingError(
            f"MP4 requires exactly one {kind.decode('ascii', errors='replace')} box."
        )
    return found[0][1], found[0][2]


def _jpeg_dimensions(sample: bytes) -> tuple[int, int]:
    if len(sample) < 12 or sample[:2] != b"\xff\xd8" or sample[-2:] != b"\xff\xd9":
        raise VideoEncodingError("Motion JPEG sample has invalid SOI/EOI markers.")
    offset = 2
    dimensions = None
    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    while offset + 4 <= len(sample) - 2:
        if sample[offset] != 0xFF:
            raise VideoEncodingError("Motion JPEG header contains malformed marker data.")
        while offset < len(sample) and sample[offset] == 0xFF:
            offset += 1
        if offset >= len(sample):
            raise VideoEncodingError("Motion JPEG marker is truncated.")
        marker = sample[offset]
        offset += 1
        if marker == 0xDA:
            break
        if marker == 0xD9:
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(sample):
            raise VideoEncodingError("Motion JPEG segment length is truncated.")
        length = _be16(sample, offset)
        if length < 2 or offset + length > len(sample):
            raise VideoEncodingError("Motion JPEG segment length is invalid.")
        if marker in sof_markers:
            if length < 7:
                raise VideoEncodingError("Motion JPEG SOF segment is too short.")
            height = _be16(sample, offset + 3)
            width = _be16(sample, offset + 5)
            if width <= 0 or height <= 0:
                raise VideoEncodingError("Motion JPEG dimensions are invalid.")
            dimensions = (width, height)
        offset += length
    if dimensions is None:
        raise VideoEncodingError("Motion JPEG sample has no supported SOF marker.")
    return dimensions


def _validate_mjpeg_mp4(output_path: Path) -> dict[str, Any]:
    size = output_path.stat().st_size
    if size <= 32 or size > 512 * 1024 * 1024:
        raise VideoEncodingError("Motion JPEG MP4 size is outside the validation limit.")
    data = output_path.read_bytes()
    top = list(_boxes(data, 0, len(data)))
    kinds = [box[0] for box in top]
    if kinds != [b"ftyp", b"mdat", b"moov"]:
        raise VideoEncodingError("Motion JPEG MP4 must contain ftyp, mdat, moov in order.")
    mdat_start, mdat_end = top[1][1], top[1][2]
    moov_start, moov_end = top[2][1], top[2][2]
    trak_start, trak_end = _child(data, moov_start, moov_end, b"trak")
    mdia_start, mdia_end = _child(data, trak_start, trak_end, b"mdia")
    mdhd_start, mdhd_end = _child(data, mdia_start, mdia_end, b"mdhd")
    if mdhd_end - mdhd_start < 20 or data[mdhd_start] != 0:
        raise VideoEncodingError("Motion JPEG mdhd format is unsupported.")
    timescale = _be32(data, mdhd_start + 12)
    duration = _be32(data, mdhd_start + 16)
    if timescale <= 0 or duration <= 0:
        raise VideoEncodingError("Motion JPEG timing metadata is invalid.")

    minf_start, minf_end = _child(data, mdia_start, mdia_end, b"minf")
    stbl_start, stbl_end = _child(data, minf_start, minf_end, b"stbl")
    stsd_start, stsd_end = _child(data, stbl_start, stbl_end, b"stsd")
    if stsd_end - stsd_start < 16 or _be32(data, stsd_start + 4) != 1:
        raise VideoEncodingError("Motion JPEG sample description is invalid.")
    entries = list(_boxes(data, stsd_start + 8, stsd_end))
    if len(entries) != 1 or entries[0][0] != b"jpeg":
        raise _NotMotionJPEG("MP4 video codec is not Motion JPEG.")
    jpeg_start, jpeg_end = entries[0][1], entries[0][2]
    if jpeg_end - jpeg_start < 28:
        raise VideoEncodingError("Motion JPEG visual sample entry is truncated.")
    expected_dimensions = (
        _be16(data, jpeg_start + 24),
        _be16(data, jpeg_start + 26),
    )

    stsz_start, stsz_end = _child(data, stbl_start, stbl_end, b"stsz")
    if stsz_end - stsz_start < 12 or _be32(data, stsz_start + 4) != 0:
        raise VideoEncodingError("Motion JPEG sample-size table is unsupported.")
    sample_count = _be32(data, stsz_start + 8)
    if sample_count <= 0 or stsz_end - stsz_start != 12 + 4 * sample_count:
        raise VideoEncodingError("Motion JPEG sample-size table is inconsistent.")
    sample_sizes = [
        _be32(data, stsz_start + 12 + 4 * index)
        for index in range(sample_count)
    ]
    if any(value <= 0 for value in sample_sizes):
        raise VideoEncodingError("Motion JPEG contains an empty sample.")

    co64_start, co64_end = _child(data, stbl_start, stbl_end, b"co64")
    if co64_end - co64_start != 16 or _be32(data, co64_start + 4) != 1:
        raise VideoEncodingError("Motion JPEG chunk-offset table is invalid.")
    sample_offset = _be64(data, co64_start + 8)
    if sample_offset != mdat_start or sample_offset + sum(sample_sizes) != mdat_end:
        raise VideoEncodingError("Motion JPEG media extent does not match its sample table.")

    cursor = sample_offset
    first_dimensions = None
    for sample_size in sample_sizes:
        dimensions = _jpeg_dimensions(data[cursor : cursor + sample_size])
        if first_dimensions is None:
            first_dimensions = dimensions
        elif dimensions != first_dimensions:
            raise VideoEncodingError("Motion JPEG frame dimensions are inconsistent.")
        cursor += sample_size
    if first_dimensions != expected_dimensions:
        raise VideoEncodingError("Motion JPEG frames disagree with the sample description.")
    return {
        "method": "software-isobmff-mjpeg-structure-v1",
        "status": "software_all_mjpeg_samples_validated",
        "first_frame_width": first_dimensions[0],
        "first_frame_height": first_dimensions[1],
        "track_duration_s": duration / timescale,
        "codec_tag": "jpeg",
        "sample_count": sample_count,
    }


def _prebuilt_renderer(cache_name: str) -> Optional[Path]:
    configured = os.environ.get("PHYSICS_DEMO_PREBUILT_VIDEO_DIR")
    if not configured:
        return None
    candidate = (Path(configured).resolve() / cache_name).resolve()
    if not candidate.is_file():
        raise VideoEncodingError(
            f"Configured prebuilt video executable is missing: {candidate}"
        )
    return candidate


def _renderer_compiler() -> str:
    clang = shutil.which("clang")
    if clang:
        return clang
    if os.environ.get("PHYSICS_DEMO_PREBUILT_VIDEO_DIR"):
        return "prebuilt"
    raise VideoEncodingError(
        "The MP4 renderer requires prebuilt release binaries or macOS clang."
    )


def _compile_renderer(
    clang: str,
    *,
    sources: list[Path],
    frameworks: tuple[str, ...],
    cache_name: str,
    timeout_seconds: float,
) -> Path:
    prebuilt = _prebuilt_renderer(cache_name)
    if prebuilt is not None:
        return prebuilt
    compile_flags = (
        "-fobjc-arc",
        "-fblocks",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-Wconversion",
        "-Wshadow",
    )
    recipe = "\0".join(("objc-renderer-v4", clang, *compile_flags, *frameworks)).encode()
    source_payloads = (
        source.name.encode() + b"\0" + source.read_bytes() for source in sources
    )
    digest_input = b"\0".join((recipe, *source_payloads))
    digest = hashlib.sha256(digest_input).hexdigest()[:12]
    cache_root = Path(tempfile.gettempdir()) / "physics-agent-demo-video"
    cache_root.mkdir(parents=True, exist_ok=True)
    renderer = cache_root / f"{cache_name}-{digest}"
    module_cache = cache_root / "clang-modules"
    module_cache.mkdir(parents=True, exist_ok=True)
    if not renderer.exists():
        compile_command = [
            clang,
            *compile_flags[:2],
            f"-fmodules-cache-path={module_cache}",
            *compile_flags[2:],
        ]
        for framework in frameworks:
            compile_command.extend(("-framework", framework))
        compile_command.extend((str(source) for source in sources if source.suffix != ".h"))
        compile_command.extend(("-o", str(renderer)))
        try:
            compiled = subprocess.run(
                compile_command,
                capture_output=True,
                text=True,
                timeout=max(0.5, min(20.0, timeout_seconds)),
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise VideoEncodingError("MP4 renderer compilation exceeded the wall-clock budget.") from error
        if compiled.returncode != 0:
            detail = (compiled.stderr or compiled.stdout or "unknown compiler error").strip()
            raise VideoEncodingError(f"MP4 renderer compilation failed: {detail[-2000:]}")
    return renderer


def _run_renderer(
    renderer: Path,
    result_path: Path,
    output_path: Path,
    fps: int,
    timeout_seconds: float,
    extra_arguments: tuple[str, ...] = (),
) -> tuple[bool, str]:
    command = [str(renderer), str(result_path), str(output_path), str(fps), *extra_arguments]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise VideoEncodingError("MP4 encoding exceeded the wall-clock budget.") from error
    if completed.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 0:
        return True, ""
    detail = (completed.stderr or completed.stdout or "unknown encoder error").strip()
    return False, detail[-1200:]


def _validate_first_frame(
    clang: str,
    source: Path,
    output_path: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    if timeout_seconds <= 0.25:
        raise VideoEncodingError(
            "The wall-clock budget was exhausted before full-track video validation."
        )
    probe = _compile_renderer(
        clang,
        sources=[source],
        frameworks=(
            "Foundation",
            "AVFoundation",
            "CoreMedia",
            "CoreVideo",
            "CoreGraphics",
            "ImageIO",
        ),
        cache_name="decode-probe",
        timeout_seconds=min(10.0, max(0.25, timeout_seconds * 0.5)),
    )
    try:
        completed = subprocess.run(
            [str(probe), str(output_path)],
            capture_output=True,
            text=True,
            timeout=max(0.25, timeout_seconds),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise VideoEncodingError(
            "Full-track video validation timed out."
        ) from error
    detail = (completed.stderr or completed.stdout or "frame decode failed").strip()
    if completed.returncode not in (0, 77):
        raise VideoEncodingError(
            f"The video track could not be completely decoded: {detail[-1200:]}"
        )
    fields = completed.stdout.strip().split()
    dimensions = fields[0].split("x", 1) if fields else []
    if (
        len(fields) != 4
        or len(dimensions) != 2
        or not all(part.isdigit() for part in dimensions)
        or not fields[3].isdigit()
    ):
        raise VideoEncodingError(
            "AVAssetImageGenerator returned invalid video track metadata."
        )
    width, height = (int(part) for part in dimensions)
    try:
        duration = float(fields[1])
    except ValueError as error:
        raise VideoEncodingError(
            "AVAssetImageGenerator returned an invalid video duration."
        ) from error
    codec_tag = fields[2]
    sample_count = int(fields[3])
    if (
        width <= 0
        or height <= 0
        or duration <= 0
        or len(codec_tag) != 4
        or sample_count <= 0
    ):
        raise VideoEncodingError(
            "AVAssetImageGenerator returned invalid video track values."
        )
    validation = {
        "method": "AVAssetReader+AVAssetImageGenerator+ImageIO",
        "first_frame_width": width,
        "first_frame_height": height,
        "track_duration_s": duration,
        "codec_tag": codec_tag,
        "sample_count": sample_count,
    }
    if completed.returncode == 77:
        if codec_tag == "jpeg":
            validation.update({
                "status": "imageio_all_samples_validated_avasset_sandbox_unavailable",
                "detail": "track metadata and every Motion JPEG sample passed ImageIO decoding; AVAssetImageGenerator pixel output is blocked by the current process sandbox",
            })
        else:
            raise VideoEncodingError(
                "AVAssetImageGenerator could not pixel-decode the first H.264 frame in this process sandbox."
            )
    else:
        validation["status"] = "passed"
    return validation


def probe_mp4(output_path: Path, timeout_seconds: float = 30.0) -> dict[str, Any]:
    """Parse the video track and validate every compressed and decoded sample.

    A constrained macOS process can deny VideoToolbox's hypervisor capability
    query.  In that one case the probe still returns parsed track metadata with
    an explicit unavailable status; malformed containers never receive it.
    """
    try:
        return _validate_mjpeg_mp4(output_path)
    except _NotMotionJPEG:
        clang = _renderer_compiler()
        return _validate_first_frame(
            clang,
            Path(__file__).with_name("video_decode_probe.m"),
            output_path,
            timeout_seconds,
        )


def _metadata(
    output_path: Path,
    fps: int,
    codec: str,
    encoder: str,
    fallback: bool,
    decode_validation: dict[str, Any],
) -> dict[str, Any]:
    return {
        "path": str(output_path.resolve()),
        "bytes": output_path.stat().st_size,
        "codec": codec,
        "codec_tag": "jpeg" if fallback else "avc1",
        "container": "MP4",
        "fps": fps,
        "duration_s": decode_validation["track_duration_s"],
        "sample_count": decode_validation["sample_count"],
        "encoder": encoder,
        "fallback": fallback,
        "decode_validation": decode_validation,
        "renderer": {
            "name": "coregraphics-continuous-liquid",
            "version": 4,
            "camera": "scale-adaptive-auto-fit",
            "trajectory_preserving": True,
        },
    }


def encode_mp4(result_path: Path, output_path: Path, fps: int, timeout_seconds: float = 30.0) -> dict[str, Any]:
    started = time.monotonic()
    clang = _renderer_compiler()
    source_root = Path(__file__).parent
    render_core = source_root / "video_render_core.m"
    render_header = source_root / "video_render_core.h"
    decode_probe = source_root / "video_decode_probe.m"
    failures: list[str] = []

    encoders = (
        ("H.264", "avfoundation-h264", "video_renderer.m", "renderer-h264",
         ("Foundation", "AVFoundation", "CoreMedia", "CoreVideo", "CoreGraphics")),
        ("Motion JPEG", "imageio-jpeg-isobmff", "video_mjpeg_renderer.m", "renderer-mjpeg",
         ("Foundation", "CoreGraphics", "ImageIO")),
    )
    for codec, encoder, source, cache_name, frameworks in encoders:
        fallback = codec == "Motion JPEG"
        remaining = timeout_seconds - (time.monotonic() - started)
        if remaining <= 0.5:
            if not fallback:
                continue
            detail = "; ".join(failures) or "no encoder attempt could start"
            raise VideoEncodingError(f"MP4 encoding exhausted the wall-clock budget ({detail[-1200:]}).")
        try:
            renderer = _compile_renderer(
                clang,
                sources=[source_root / source, render_core, render_header],
                frameworks=frameworks,
                cache_name=cache_name,
                timeout_seconds=min(20.0, max(0.5, remaining * 0.6)),
            )
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0.25:
                raise VideoEncodingError(f"The wall-clock budget was exhausted before {codec} encoding could start.")
            encoded, detail = _run_renderer(renderer, result_path, output_path, fps, remaining)
            if encoded:
                decode_validation = (
                    _validate_mjpeg_mp4(output_path)
                    if fallback
                    else _validate_first_frame(
                        clang,
                        decode_probe,
                        output_path,
                        timeout_seconds - (time.monotonic() - started),
                    )
                )
                return _metadata(output_path, fps, codec, encoder, fallback, decode_validation)
            failures.append(f"{codec}: {detail}")
        except VideoEncodingError as error:
            failures.append(f"{codec}: {error}")
    detail = "; ".join(failures) or "unknown encoder error"
    raise VideoEncodingError(f"MP4 encoding failed: {detail[-2400:]}")


def encode_bounded_mp4(result_path: Path, output_path: Path, fps: int,
                       max_bytes: int, timeout_seconds: float = 30.0) -> dict[str, Any]:
    """Fit every saved frame into a host delivery budget, without rerunning physics."""
    if type(max_bytes) is not int or max_bytes < 1024:
        raise VideoEncodingError("Video byte budget must be an integer of at least 1024.")
    if type(fps) is not int or not 1 <= fps <= 60:
        raise VideoEncodingError("Video FPS must be an integer in [1, 60].")
    started = time.monotonic()
    source = Path(__file__).parent
    renderer = _compile_renderer(_renderer_compiler(),
        sources=[source/'video_mjpeg_renderer.m', source/'video_render_core.m', source/'video_render_core.h'],
        frameworks=("Foundation", "CoreGraphics", "ImageIO"), cache_name="renderer-mjpeg",
        timeout_seconds=max(.001, min(20.0, timeout_seconds)))
    for width in (960, 720, 480):
        remaining = timeout_seconds - (time.monotonic() - started)
        if remaining <= .25:
            raise VideoEncodingError("Video compression exceeded its remaining wall-clock budget.")
        encoded, detail = _run_renderer(renderer, result_path, output_path, fps, remaining,
                                        (str(max_bytes), str(width)))
        if not encoded:
            if "video_byte_budget_exceeded" in detail:
                continue
            raise VideoEncodingError("Video compression failed: " + detail)
        probe = _validate_mjpeg_mp4(output_path)
        if output_path.stat().st_size > max_bytes:
            raise VideoEncodingError("Video encoder exceeded its byte budget.")
        metadata = _metadata(output_path, fps, "Motion JPEG", "imageio-jpeg-isobmff", True, probe)
        metadata['delivery'] = {'max_bytes':max_bytes, 'width':width, 'height':width*9//16,
                                'all_frames_preserved':True, 'solver_rerun':False}
        return metadata
    raise VideoEncodingError("Video cannot fit the byte budget at the minimum supported presentation quality.")


def _interpolate_display_value(left: Any, right: Any, amount: float, *, quaternion: bool = False) -> Any:
    """Interpolate recorded display channels without modifying solver output."""
    if isinstance(left, bool) or isinstance(right, bool):
        return copy.deepcopy(left if amount < 0.5 else right)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        value = float(left) + amount * (float(right) - float(left))
        if not math.isfinite(value):
            raise VideoEncodingError("Presentation interpolation produced a non-finite value.")
        return value
    if isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
        values = [
            _interpolate_display_value(a, b, amount)
            for a, b in zip(left, right)
        ]
        if quaternion and len(values) == 4 and all(isinstance(value, float) for value in values):
            dot = sum(float(a) * float(b) for a, b in zip(left, right))
            if dot < 0.0:
                values = [
                    float(a) + amount * (-float(b) - float(a))
                    for a, b in zip(left, right)
                ]
            length = math.sqrt(sum(value * value for value in values))
            if length <= 1.0e-15 or not math.isfinite(length):
                raise VideoEncodingError("Presentation quaternion interpolation is degenerate.")
            values = [value / length for value in values]
        return values
    if type(left) is not type(right):
        raise VideoEncodingError("Presentation frames change value types.")
    return copy.deepcopy(left if amount < 0.5 else right)


def _interpolate_display_frame(left: dict[str, Any], right: dict[str, Any], amount: float) -> dict[str, Any]:
    if set(left) != set(right):
        raise VideoEncodingError("Presentation frames do not share the same channels.")
    frame: dict[str, Any] = {}
    for key in left:
        if key == "t":
            frame[key] = float(left[key]) + amount * (float(right[key]) - float(left[key]))
        elif key == "q":
            if not isinstance(left[key], list) or not isinstance(right[key], list) or len(left[key]) != len(right[key]):
                raise VideoEncodingError("Presentation quaternion channels are inconsistent.")
            frame[key] = [
                _interpolate_display_value(a, b, amount, quaternion=True)
                for a, b in zip(left[key], right[key])
            ]
        else:
            frame[key] = _interpolate_display_value(left[key], right[key], amount)
    return frame


def _retime_result_document(
    document: dict[str, Any],
    *,
    fps: int,
    segments: list[dict[str, float]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build a display-only, piecewise-linear slow-motion trajectory.

    Each segment maps a source physical-time interval to a requested playback
    duration. Equal start/end times create a hold. The persisted solver result
    is never changed and the generated document is intended only for encoding.
    """
    if not isinstance(fps, int) or isinstance(fps, bool) or not 1 <= fps <= 120:
        raise VideoEncodingError("Presentation fps must be an integer in [1, 120].")
    trajectory = document.get("trajectory")
    frames = trajectory.get("frames") if isinstance(trajectory, dict) else None
    if not isinstance(frames, list) or len(frames) < 2 or not all(isinstance(frame, dict) for frame in frames):
        raise VideoEncodingError("Presentation retiming requires at least two trajectory frames.")
    try:
        source_times = [float(frame["t"]) for frame in frames]
    except (KeyError, TypeError, ValueError) as error:
        raise VideoEncodingError("Presentation source frames require numeric timestamps.") from error
    if any(not math.isfinite(value) for value in source_times) or any(
        later <= earlier for earlier, later in zip(source_times, source_times[1:])
    ):
        raise VideoEncodingError("Presentation source timestamps must be finite and strictly increasing.")
    if not isinstance(segments, list) or not segments:
        raise VideoEncodingError("Presentation retiming requires at least one segment.")

    normalized_segments: list[dict[str, float]] = []
    total_duration = 0.0
    previous_end = source_times[0]
    for raw in segments:
        if not isinstance(raw, dict) or set(raw) != {
            "physical_start_s", "physical_end_s", "playback_duration_s"
        }:
            raise VideoEncodingError("Presentation segments require start, end, and playback duration.")
        start = float(raw["physical_start_s"])
        end = float(raw["physical_end_s"])
        duration = float(raw["playback_duration_s"])
        if not all(math.isfinite(value) for value in (start, end, duration)):
            raise VideoEncodingError("Presentation segment values must be finite.")
        if duration <= 0.0 or start < source_times[0] or end > source_times[-1] or end < start:
            raise VideoEncodingError("Presentation segment range or duration is invalid.")
        if abs(start - previous_end) > 1.0e-9:
            raise VideoEncodingError("Presentation physical segments must be contiguous.")
        normalized_segments.append({
            "physical_start_s": start,
            "physical_end_s": end,
            "playback_duration_s": duration,
        })
        total_duration += duration
        previous_end = end
    if abs(normalized_segments[0]["physical_start_s"] - source_times[0]) > 1.0e-9 or abs(previous_end - source_times[-1]) > 1.0e-9:
        raise VideoEncodingError("Presentation segments must cover the complete physical trajectory.")
    sample_count = max(2, int(round(total_duration * fps)))
    if sample_count > 3_600:
        raise VideoEncodingError("Presentation exceeds the 3600-frame display limit.")

    cumulative: list[float] = []
    running = 0.0
    for segment in normalized_segments:
        running += segment["playback_duration_s"]
        cumulative.append(running)

    display_frames: list[dict[str, Any]] = []
    for index in range(sample_count):
        playback_time = total_duration * index / (sample_count - 1)
        segment_index = min(bisect.bisect_left(cumulative, playback_time), len(normalized_segments) - 1)
        segment = normalized_segments[segment_index]
        playback_start = 0.0 if segment_index == 0 else cumulative[segment_index - 1]
        local = (playback_time - playback_start) / segment["playback_duration_s"]
        local = min(1.0, max(0.0, local))
        physical_time = segment["physical_start_s"] + local * (
            segment["physical_end_s"] - segment["physical_start_s"]
        )
        right_index = bisect.bisect_left(source_times, physical_time)
        if right_index <= 0:
            display_frames.append(copy.deepcopy(frames[0]))
            continue
        if right_index >= len(frames):
            display_frames.append(copy.deepcopy(frames[-1]))
            continue
        left_index = right_index - 1
        amount = (physical_time - source_times[left_index]) / (
            source_times[right_index] - source_times[left_index]
        )
        display_frames.append(_interpolate_display_frame(frames[left_index], frames[right_index], amount))

    retimed = copy.deepcopy(document)
    retimed["trajectory"]["frames"] = display_frames
    presentation = {
        "mode": "piecewise-linear-display-retiming-v1",
        "fps": fps,
        "sample_count": sample_count,
        "playback_duration_s": sample_count / fps,
        "source_physical_duration_s": source_times[-1] - source_times[0],
        "time_scale_to_physical": (source_times[-1] - source_times[0]) / (sample_count / fps),
        "segments": normalized_segments,
        "solver_result_unchanged": True,
    }
    retimed["presentation"] = presentation
    return retimed, presentation


def encode_watchable_mp4(
    result_path: Path,
    output_path: Path,
    *,
    fps: int,
    segments: list[dict[str, float]],
    timeout_seconds: float = 30.0,
    max_bytes: int | None = None,
    camera_zoom: float = 1.0,
    camera_focus_quantile: float | None = None,
    water_renderer: str = "continuous",
) -> dict[str, Any]:
    """Encode a smooth, slowed presentation while preserving the source run."""
    if (not isinstance(camera_zoom, (int, float)) or isinstance(camera_zoom, bool)
            or not math.isfinite(camera_zoom) or not 1.0 <= camera_zoom <= 3.0):
        raise VideoEncodingError("Presentation camera zoom must be between 1 and 3.")
    if camera_focus_quantile is not None and (
        not isinstance(camera_focus_quantile, (int, float))
        or isinstance(camera_focus_quantile, bool)
        or not math.isfinite(camera_focus_quantile)
        or not 0.9 <= camera_focus_quantile < 1.0
    ):
        raise VideoEncodingError("Presentation camera focus quantile must be in [0.9, 1).")
    if water_renderer not in ("continuous", "cohesive_spray", "legacy_v2", "mesh_hybrid"):
        raise VideoEncodingError("Unknown presentation water renderer.")
    try:
        document = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise VideoEncodingError(f"Cannot read presentation source result: {error}") from error
    if not isinstance(document, dict):
        raise VideoEncodingError("Presentation source result must be a JSON object.")
    if water_renderer != "continuous":
        scene = document.get("scene", {})
        trajectory = document.get("trajectory", {})
        frames = trajectory.get("frames", []) if isinstance(trajectory, dict) else []
        if (
            not isinstance(scene, dict)
            or not isinstance(trajectory, dict)
            or not isinstance(frames, list)
            or any(not isinstance(frame, dict) for frame in frames)
            or not trajectory.get("particle_materials")
            or any(material != "water" for material in trajectory["particle_materials"])
        ):
            raise VideoEncodingError("Special water presentation requires recorded water samples.")
        has_mesh = bool(trajectory.get("mesh_objects")) and any(frame.get("m") for frame in frames)
        connections = scene.get("connections") or []
        mixed = (scene.get("coupling") is not None
                 or any(frame.get("q") is not None for frame in frames)
                 or any(link.get("endpoints") or link.get("solid")
                        for link in connections if isinstance(link, dict)))
        if water_renderer in ("legacy_v2", "cohesive_spray") and (has_mesh or mixed):
            raise VideoEncodingError("This water presentation requires uncoupled water without meshes.")
        if water_renderer == "mesh_hybrid" and not (has_mesh and mixed):
            raise VideoEncodingError("Hybrid water presentation requires a coupled water-mesh scene.")
    retimed, presentation = _retime_result_document(document, fps=fps, segments=segments)
    if water_renderer != "continuous":
        retimed["scene"]["__presentation_water_renderer"] = water_renderer
    presentation["water_renderer"] = water_renderer
    if camera_focus_quantile is not None:
        positions = [point for frame in document["trajectory"]["frames"]
                     for point in frame.get("p", [])]
        if positions:
            lower = (1.0 - camera_focus_quantile) / 2.0
            minimum, maximum = [], []
            for axis in range(3):
                values = sorted(float(point[axis]) for point in positions)
                minimum.append(values[int(lower * (len(values) - 1))])
                maximum.append(values[int((1.0 - lower) * (len(values) - 1))])
            retimed["scene"]["__presentation_camera_corners"] = [
                [x, y, z]
                for x in (minimum[0], maximum[0])
                for y in (minimum[1], maximum[1])
                for z in (minimum[2], maximum[2])
            ]
            presentation["camera_focus_quantile"] = float(camera_focus_quantile)
    if camera_zoom != 1.0:
        retimed["scene"]["__presentation_camera_zoom"] = float(camera_zoom)
        presentation["camera_zoom"] = float(camera_zoom)
    with tempfile.TemporaryDirectory(prefix="physics-video-presentation-") as directory:
        temporary_result = Path(directory) / "result.json"
        temporary_result.write_text(
            json.dumps(retimed, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        encode = encode_mp4 if max_bytes is None else encode_bounded_mp4
        metadata = encode(temporary_result, output_path, fps,
                          timeout_seconds=timeout_seconds,
                          **({"max_bytes":max_bytes} if max_bytes is not None else {}))
    metadata["presentation"] = presentation
    if water_renderer == "legacy_v2":
        metadata["renderer"]["name"] = "coregraphics-legacy-water-v2"
    elif water_renderer == "cohesive_spray":
        metadata["renderer"]["name"] = "coregraphics-cohesive-water-spray"
    elif water_renderer == "mesh_hybrid":
        metadata["renderer"]["name"] = "coregraphics-mesh-liquid-hybrid"
    metadata["renderer"]["temporal_interpolation"] = "piecewise-linear recorded-state interpolation"
    return metadata
