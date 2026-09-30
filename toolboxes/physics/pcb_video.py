"""Render PCB temperature and dissipated power as a verified H.264 MP4.

The rasterizer uses Python's standard library. The release environment must
provide ffmpeg with libx264; the packaged probe decodes every output sample.
The thermal solver's arrays are never modified.
"""

from __future__ import annotations

from bisect import bisect_right
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


class PcbVideoError(RuntimeError):
    """The requested thermal video could not be rendered and verified."""


_FONT = {
    " ": (0, 0, 0, 0, 0, 0, 0),
    "A": (14, 17, 17, 31, 17, 17, 17), "B": (30, 17, 17, 30, 17, 17, 30),
    "C": (14, 17, 16, 16, 16, 17, 14), "D": (30, 17, 17, 17, 17, 17, 30),
    "E": (31, 16, 16, 30, 16, 16, 31), "F": (31, 16, 16, 30, 16, 16, 16),
    "G": (14, 17, 16, 23, 17, 17, 14), "H": (17, 17, 17, 31, 17, 17, 17),
    "I": (31, 4, 4, 4, 4, 4, 31), "J": (7, 2, 2, 2, 18, 18, 12),
    "K": (17, 18, 20, 24, 20, 18, 17), "L": (16, 16, 16, 16, 16, 16, 31),
    "M": (17, 27, 21, 21, 17, 17, 17), "N": (17, 25, 21, 19, 17, 17, 17),
    "O": (14, 17, 17, 17, 17, 17, 14), "P": (30, 17, 17, 30, 16, 16, 16),
    "Q": (14, 17, 17, 17, 21, 18, 13), "R": (30, 17, 17, 30, 20, 18, 17),
    "S": (15, 16, 16, 14, 1, 1, 30), "T": (31, 4, 4, 4, 4, 4, 4),
    "U": (17, 17, 17, 17, 17, 17, 14), "V": (17, 17, 17, 17, 17, 10, 4),
    "W": (17, 17, 17, 21, 21, 21, 10), "X": (17, 17, 10, 4, 10, 17, 17),
    "Y": (17, 17, 10, 4, 4, 4, 4), "Z": (31, 1, 2, 4, 8, 16, 31),
    "0": (14, 17, 19, 21, 25, 17, 14), "1": (4, 12, 4, 4, 4, 4, 14),
    "2": (14, 17, 1, 2, 4, 8, 31), "3": (30, 1, 1, 14, 1, 1, 30),
    "4": (2, 6, 10, 18, 31, 2, 2), "5": (31, 16, 30, 1, 1, 17, 14),
    "6": (6, 8, 16, 30, 17, 17, 14), "7": (31, 1, 2, 4, 8, 8, 8),
    "8": (14, 17, 17, 14, 17, 17, 14), "9": (14, 17, 17, 15, 1, 2, 12),
    ".": (0, 0, 0, 0, 0, 12, 12), ":": (0, 12, 12, 0, 12, 12, 0),
    "-": (0, 0, 0, 31, 0, 0, 0), "+": (0, 4, 4, 31, 4, 4, 0),
    "/": (1, 1, 2, 4, 8, 16, 16), "?": (14, 17, 1, 2, 4, 0, 4),
}


def _point(pixels: bytearray, width: int, height: int,
           x: int, y: int, color: tuple[int, int, int]) -> None:
    if 0 <= x < width and 0 <= y < height:
        offset = (y * width + x) * 3
        pixels[offset:offset + 3] = bytes(color)


def _rect(pixels: bytearray, width: int, height: int,
          x0: int, y0: int, x1: int, y1: int,
          color: tuple[int, int, int], *, outline: bool = False) -> None:
    x0, x1 = sorted((max(0, min(width, x0)), max(0, min(width, x1))))
    y0, y1 = sorted((max(0, min(height, y0)), max(0, min(height, y1))))
    if outline:
        for x in range(x0, x1):
            _point(pixels, width, height, x, y0, color)
            _point(pixels, width, height, x, y1 - 1, color)
        for y in range(y0, y1):
            _point(pixels, width, height, x0, y, color)
            _point(pixels, width, height, x1 - 1, y, color)
    else:
        span = bytes(color) * (x1 - x0)
        for y in range(y0, y1):
            offset = (y * width + x0) * 3
            pixels[offset:offset + len(span)] = span


def _text(pixels: bytearray, width: int, height: int, x: int, y: int,
          value: str, color: tuple[int, int, int], scale: int = 2) -> None:
    for char in value.upper()[:60]:
        rows = _FONT.get(char, _FONT["?"])
        for dy, bits in enumerate(rows):
            for dx in range(5):
                if bits & (1 << (4 - dx)):
                    _rect(pixels, width, height, x + dx * scale, y + dy * scale,
                          x + (dx + 1) * scale, y + (dy + 1) * scale, color)
        x += 6 * scale


def _gradient(fraction: float, *, power: bool = False) -> tuple[int, int, int]:
    fraction = max(0.0, min(1.0, fraction))
    stops = ((18, 31, 58), (16, 116, 160), (61, 210, 189),
             (246, 211, 82), (232, 77, 60)) if not power else (
             (21, 31, 46), (25, 93, 108), (62, 186, 145),
             (245, 212, 87), (242, 105, 56))
    position = fraction * (len(stops) - 1)
    index = min(len(stops) - 2, int(position))
    blend = position - index
    return tuple(round(stops[index][axis] * (1 - blend)
                       + stops[index + 1][axis] * blend) for axis in range(3))


def _grid(result: dict, key: str, nx: int, ny: int) -> list[list[float]]:
    matrix = result.get(key)
    if not isinstance(matrix, list) or len(matrix) != ny:
        raise PcbVideoError(f"{key} has the wrong number of rows")
    for row in matrix:
        if not isinstance(row, list) or len(row) != nx or any(
                type(value) not in (int, float) or not math.isfinite(value)
                for value in row):
            raise PcbVideoError(f"{key} contains an invalid cell")
    return matrix


def _panel(pixels: bytearray, width: int, height: int, panel: tuple[int, int, int, int],
           matrix: list[list[float]], low: float, high: float, *, power: bool) -> None:
    left, top, right, bottom = panel
    ny = len(matrix)
    nx = len(matrix[0])
    panel_width, panel_height = right - left, bottom - top
    inverse = 1 / max(high - low, 1e-12)
    palette = [_gradient(index / 255, power=power) for index in range(256)]
    for y in range(top, bottom):
        row = matrix[min(ny - 1, (bottom - 1 - y) * ny // panel_height)]
        colors = bytearray()
        for x in range(panel_width):
            value = row[min(nx - 1, x * nx // panel_width)]
            color = palette[max(0, min(255, round((value - low) * inverse * 255)))]
            colors.extend(color)
        offset = (y * width + left) * 3
        pixels[offset:offset + len(colors)] = colors
    _rect(pixels, width, height, left - 1, top - 1, right + 1, bottom + 1,
          (200, 217, 223), outline=True)


def _render_frame(result: dict, temperature: list[list[float]], power_map: list[list[float]],
                  minimum: float, maximum: float, max_power: float, time_s: float) -> tuple[int, int, bytearray]:
    width, height = 640, 400
    pixels = bytearray(bytes((9, 16, 31)) * width * height)
    board = result["board"]
    ratio = board["width_m"] / board["height_m"]
    panel_w = min(268, round(266 * ratio)) if ratio < 1 else 268
    panel_h = min(262, round(262 / ratio)) if ratio > 1 else 262
    panel_w, panel_h = max(30, panel_w), max(30, panel_h)
    top = 70 + (262 - panel_h) // 2
    left_panel = (28 + (268 - panel_w) // 2, top,
                  28 + (268 + panel_w) // 2, top + panel_h)
    right_panel = (344 + (268 - panel_w) // 2, top,
                   344 + (268 + panel_w) // 2, top + panel_h)
    _text(pixels, width, height, 26, 17, "PCB THERMAL", (230, 239, 245), 2)
    _text(pixels, width, height, 28, 46, "TEMPERATURE C", (116, 213, 225), 1)
    _text(pixels, width, height, 344, 46, "POWER W/CELL", (120, 219, 173), 1)
    _panel(pixels, width, height, left_panel, temperature, minimum, maximum, power=False)
    _panel(pixels, width, height, right_panel, power_map, 0.0, max_power, power=True)
    for component in result.get("components", []):
        for panel in (left_panel, right_panel):
            left, top, right, bottom = panel
            board_w, board_h = board["width_m"], board["height_m"]
            x0 = left + round(component["x_m"] / board_w * (right - left))
            x1 = left + round((component["x_m"] + component["width_m"]) / board_w * (right - left))
            y0 = bottom - round((component["y_m"] + component["height_m"]) / board_h * (bottom - top))
            y1 = bottom - round(component["y_m"] / board_h * (bottom - top))
            _rect(pixels, width, height, x0, y0, x1, y1,
                  (240, 248, 249), outline=True)
            label = str(component.get("id", ""))
            if label and x1 - x0 >= 8 * len(label) and y1 - y0 >= 10:
                _text(pixels, width, height, x0 + 3, y0 + 2,
                      label[:8], (250, 250, 250), 1)
    _text(pixels, width, height, 28, 346,
          f"MIN {minimum:.1f} C  MAX {maximum:.1f} C", (224, 234, 239), 1)
    _text(pixels, width, height, 344, 346,
          f"TOTAL {result['total_power_w']:.2f} W", (224, 234, 239), 1)
    stamp = "STEADY STATE" if len(result["snapshots"]) == 1 else f"PHYSICAL TIME {time_s:.2f} S"
    _text(pixels, width, height, 28, 369, stamp, (149, 167, 184), 1)
    return width, height, pixels


def _interpolate(snapshots: list[dict], time_s: float) -> list[list[float]]:
    if len(snapshots) == 1 or time_s <= snapshots[0]["time_s"]:
        return snapshots[0]["temperature_c"]
    times = [snapshot["time_s"] for snapshot in snapshots]
    index = min(len(snapshots) - 2, bisect_right(times, time_s) - 1)
    before, after = snapshots[index:index + 2]
    if time_s >= times[-1]:
        return snapshots[-1]["temperature_c"]
    fraction = (time_s - before["time_s"]) / (after["time_s"] - before["time_s"])
    return [[a + (b - a) * fraction for a, b in zip(left, right)]
            for left, right in zip(before["temperature_c"], after["temperature_c"])]


def _validate_result(result: dict) -> tuple[list[dict], list[list[float]]]:
    try:
        board, grid = result["board"], result["grid"]
        nx, ny = grid["nx"], grid["ny"]
        if type(nx) is not int or type(ny) is not int or not 1 <= nx <= 512 or not 1 <= ny <= 512:
            raise PcbVideoError("invalid PCB grid dimensions")
        if any(type(board[key]) not in (int, float) or not math.isfinite(board[key])
               or board[key] <= 0 for key in ("width_m", "height_m")):
            raise PcbVideoError("invalid PCB dimensions")
        power = _grid(result, "power_w", nx, ny)
        if any(value < 0 for row in power for value in row):
            raise PcbVideoError("negative dissipated power")
        snapshots = result["snapshots"]
        if not isinstance(snapshots, list) or not 1 <= len(snapshots) <= 240:
            raise PcbVideoError("invalid thermal snapshot count")
        previous = -math.inf
        for snapshot in snapshots:
            stamp = snapshot["time_s"]
            if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0 or stamp <= previous:
                raise PcbVideoError("thermal snapshot times must increase")
            _grid(snapshot, "temperature_c", nx, ny)
            previous = stamp
        if not isinstance(result.get("components", []), list):
            raise PcbVideoError("invalid PCB components")
        for component in result.get("components", []):
            for key in ("x_m", "y_m", "width_m", "height_m"):
                value = component[key]
                if type(value) not in (int, float) or not math.isfinite(value):
                    raise PcbVideoError("invalid component geometry")
            if component["width_m"] <= 0 or component["height_m"] <= 0:
                raise PcbVideoError("invalid component size")
        if type(result["total_power_w"]) not in (int, float) or not math.isfinite(result["total_power_w"]):
            raise PcbVideoError("invalid total power")
        return snapshots, power
    except (KeyError, TypeError, IndexError) as exc:
        raise PcbVideoError("incomplete PCB numerical result") from exc


def _ffmpeg_executable() -> str:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    fallback = Path("/opt/homebrew/bin/ffmpeg")
    if fallback.is_file():
        return str(fallback)
    raise PcbVideoError("ffmpeg is required for PCB video encoding")


def _encode_mp4(raw_frames: Path, destination: Path, width: int, height: int,
                fps: int, quality: int, timeout: float | None) -> None:
    if timeout is not None and timeout <= 0:
        raise PcbVideoError("PCB video rendering exceeded its deadline")
    command = [
        _ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", f"{width}x{height}",
        "-framerate", str(fps), "-i", str(raw_frames), "-an", "-c:v", "libx264",
        "-preset", "medium", "-crf", str(quality), "-pix_fmt", "yuv420p",
        "-threads", "1", "-movflags", "+faststart", "-map_metadata", "-1",
        "-metadata", "creation_time=1970-01-01T00:00:00Z", str(destination),
    ]
    try:
        completed = subprocess.run(command, input='',
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PcbVideoError(f"ffmpeg could not encode the PCB video: {type(exc).__name__}: {exc}") from exc
    if completed.returncode or not destination.is_file():
        raise PcbVideoError("ffmpeg could not encode the PCB video: "
                            + completed.stderr.strip()[-400:])


def _decode_probe(video: Path, timeout: float | None) -> dict:
    # ffmpeg's software decoder checks every frame; ffprobe supplies the
    # decoded frame count and stream metadata. The AVFoundation probe can fail
    # in restricted hosts that do not expose a hardware decoder.
    ffmpeg = _ffmpeg_executable()
    ffprobe = shutil.which("ffprobe") or str(Path(ffmpeg).with_name("ffprobe"))
    try:
        decoded = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-xerror", "-i",
             str(video), "-f", "null", "-"],
            input='', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=None if timeout is None else max(0.1, timeout * 0.5), check=False)
        metadata = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries", "stream=codec_name,width,height,duration,nb_read_frames",
             "-of", "json", str(video)],
            input='', stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=None if timeout is None else max(0.1, timeout * 0.5), check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PcbVideoError("video decode validation failed") from exc
    if decoded.returncode or metadata.returncode:
        raise PcbVideoError("video decode validation failed: "
                            + (decoded.stderr or metadata.stderr).strip()[-400:])
    try:
        stream, = json.loads(metadata.stdout)["streams"]
        if stream["codec_name"] != "h264":
            raise ValueError("wrong codec")
        return {"width": int(stream["width"]), "height": int(stream["height"]),
                "duration_s": float(stream["duration"]), "codec": "avc1",
                "sample_count": int(stream["nb_read_frames"])}
    except (ValueError, KeyError, TypeError) as exc:
        raise PcbVideoError("video decoder reported invalid metadata") from exc


def render_pcb_video(result: dict, spec: dict, destination: Path, *,
                     max_bytes: int, timeout_seconds: float | None) -> dict:
    """Render, size-check, and fully decode a PCB thermal video.

    Rows in ``temperature_c`` and ``power_w`` increase from the board's bottom
    edge. Intermediate displayed states use linear interpolation of saved
    numerical snapshots; the numerical solution itself is not changed.
    """
    if type(max_bytes) is not int or max_bytes < 1024:
        raise PcbVideoError("invalid video byte budget")
    if timeout_seconds is not None and (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise PcbVideoError("invalid video deadline")
    if not isinstance(spec, dict) or not isinstance(result, dict):
        raise PcbVideoError("invalid PCB video input")
    snapshots, power = _validate_result(result)
    temperatures = [value for snapshot in snapshots for row in snapshot["temperature_c"] for value in row]
    minimum, maximum = min(temperatures), max(temperatures)
    max_power = max(value for row in power for value in row)
    frame_count = min(60, max(24, len(snapshots)))
    fps = 8
    start_time = snapshots[0]["time_s"]
    end_time = snapshots[-1]["time_s"]
    destination = Path(destination)
    if destination.suffix.lower() != ".mp4" or not destination.parent.is_dir():
        raise PcbVideoError("PCB video destination must be an MP4 in an existing directory")
    started = time.monotonic()
    destination.unlink(missing_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix=".pcb-video-", dir=destination.parent) as temp:
            temporary = Path(temp)
            raw_frames = temporary / "frames.rgb"
            with raw_frames.open("wb") as handle:
                for index in range(frame_count):
                    if timeout_seconds is not None and time.monotonic() - started >= timeout_seconds:
                        raise PcbVideoError("PCB video rendering exceeded its deadline")
                    stamp = (start_time + (end_time - start_time) * index / (frame_count - 1)
                             if len(snapshots) > 1 else start_time)
                    temperature = _interpolate(snapshots, stamp)
                    width, height, pixels = _render_frame(
                        result, temperature, power, minimum, maximum, max_power, stamp)
                    handle.write(pixels)
            candidate = temporary / "simulation.mp4"
            for quality in (20, 28, 36):
                _encode_mp4(raw_frames, candidate, width, height, fps, quality,
                            None if timeout_seconds is None else timeout_seconds - (time.monotonic() - started))
                if candidate.stat().st_size <= max_bytes:
                    break
            else:
                raise PcbVideoError("PCB video exceeds the host byte budget")
            probe = _decode_probe(candidate, None if timeout_seconds is None else timeout_seconds - (time.monotonic() - started))
            if (probe["width"], probe["height"], probe["sample_count"]) != (width, height, frame_count):
                raise PcbVideoError("decoded PCB video disagrees with its frames")
            if abs(probe["duration_s"] - frame_count / fps) > 0.01:
                raise PcbVideoError("decoded PCB video duration is inconsistent")
            if timeout_seconds is not None and time.monotonic() - started > timeout_seconds:
                raise PcbVideoError("PCB video rendering exceeded its deadline")
            os.replace(candidate, destination)
            return {"decode_verified": True, "frame_count": frame_count,
                    "fps": fps, "bytes": destination.stat().st_size,
                    "duration_s": probe["duration_s"], "physical_duration_s": end_time,
                    "codec": "H.264", "width": width, "height": height,
                    "all_frames_decoded": True,
                    "interpolated_display_frames": len(snapshots) > 1}
    except Exception:
        destination.unlink(missing_ok=True)
        raise
