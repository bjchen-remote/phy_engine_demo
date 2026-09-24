"""Encode a QQ-playable video from the verified presentation frames."""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import time
from pathlib import Path

from physics_demo.io.video import VideoEncodingError


def _tool(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise VideoEncodingError(f"{name} is required for QQ H.264 video delivery")
    return executable


def _run(command: list[str], deadline: float) -> str:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise VideoEncodingError("QQ video delivery exceeded its wall-clock budget")
    try:
        completed = subprocess.run(command, stdin=subprocess.PIPE,
                                   capture_output=True, text=True,
                                   timeout=remaining, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise VideoEncodingError(f"QQ video delivery could not complete: {type(error).__name__}") from error
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise VideoEncodingError(f"QQ video delivery failed: {detail[-600:]}")
    return completed.stdout


def _probe(path: Path, deadline: float) -> dict:
    output = _run([_tool("ffprobe"), "-v", "error", "-count_frames",
                   "-select_streams", "v:0", "-show_entries",
                   "stream=codec_name,codec_tag_string,pix_fmt,color_range,"
                   "nb_read_frames,duration,width,height", "-of", "json", str(path)], deadline)
    try:
        streams = json.loads(output)["streams"]
        if len(streams) != 1:
            raise ValueError("expected exactly one video stream")
        stream = streams[0]
        stream["nb_read_frames"] = int(stream["nb_read_frames"])
        stream["duration"] = float(stream["duration"])
        if (stream["nb_read_frames"] <= 0 or not math.isfinite(stream["duration"])
                or stream["duration"] <= 0):
            raise ValueError("invalid video timing")
        return stream
    except (KeyError, TypeError, ValueError, IndexError, json.JSONDecodeError) as error:
        raise VideoEncodingError("QQ video track metadata is incomplete") from error


def _validate(path: Path, original: dict, deadline: float) -> dict:
    stream = _probe(path, deadline)
    if (stream["codec_name"] != "h264" or stream["codec_tag_string"] != "avc1"
            or stream.get("pix_fmt") != "yuv420p"):
        raise VideoEncodingError("QQ video must be H.264 avc1 with yuv420p pixels")
    if (stream["nb_read_frames"] != original["sample_count"]
            or abs(stream["duration"] - original["duration_s"]) > 0.002):
        raise VideoEncodingError("QQ video changed the frame count or playback duration")
    _run([_tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
          "-xerror", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"], deadline)
    return stream


def publish_video(artifacts: Path, destination: Path, summary: dict,
                  max_bytes: int, timeout_seconds: float) -> dict:
    if type(max_bytes) is not int or max_bytes < 1024:
        raise VideoEncodingError("invalid host video byte limit")
    if (not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise VideoEncodingError("invalid QQ video delivery time limit")
    deadline = time.monotonic() + timeout_seconds
    source = artifacts / "simulation.mp4"
    original = summary["artifacts"]["video"]
    physical_duration = original.get("physical_duration_s", original["duration_s"])
    source_stream = _probe(source, deadline)
    if (source_stream["nb_read_frames"] != original["sample_count"]
            or abs(source_stream["duration"] - original["duration_s"]) > 0.002):
        raise VideoEncodingError("Source video disagrees with its verified presentation")

    temporary = destination.with_name(".delivery-video.tmp.mp4")
    temporary.unlink(missing_ok=True)
    try:
        already_compatible = (source_stream["codec_name"] == "h264"
                              and source_stream["codec_tag_string"] == "avc1"
                              and source_stream.get("pix_fmt") == "yuv420p")
        if already_compatible and source.stat().st_size <= max_bytes:
            shutil.copyfile(source, temporary)
            stream = _validate(temporary, original, deadline)
            compressed = False
        else:
            compressed = True
            width = int(source_stream["width"])
            for target_width, crf in ((width, 18), (width, 24),
                                      (min(width, 720), 28), (min(width, 480), 32)):
                temporary.unlink(missing_ok=True)
                filter_graph = f"scale={target_width}:-2:out_range=tv,format=yuv420p"
                _run([_tool("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
                      "-y", "-i", str(source), "-map", "0:v:0", "-an", "-sn", "-dn",
                      "-vf", filter_graph, "-c:v", "libx264", "-preset", "veryfast",
                      "-crf", str(crf), "-profile:v", "main", "-level", "4.0",
                      "-pix_fmt", "yuv420p", "-color_range", "tv",
                      "-fps_mode", "passthrough", "-movflags", "+faststart",
                      str(temporary)], deadline)
                if temporary.stat().st_size <= max_bytes:
                    stream = _validate(temporary, original, deadline)
                    break
            else:
                raise VideoEncodingError("H.264 video cannot fit the host byte limit")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "max_bytes": max_bytes,
        "bytes": destination.stat().st_size,
        "source_bytes": source.stat().st_size,
        "compressed": compressed,
        "codec": "h264",
        "codec_tag": "avc1",
        "width": stream["width"],
        "height": stream["height"],
        "all_frames_preserved": True,
        "solver_rerun": False,
        "sample_count": stream["nb_read_frames"],
        "duration_s": stream["duration"],
        "physical_duration_s": physical_duration,
        "time_scale_to_physical": physical_duration / stream["duration"],
    }
