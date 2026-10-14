"""Scientific video from immutable saved results, with independent budgets."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from .artifacts import (PresentationError, atomic_write, canonical, digest,
                        load_source, output_directory, pcb_module, verify_unchanged)


def _remaining(started: float, budget: float | None) -> float | None:
    remaining = None if budget is None else budget - (time.monotonic() - started)
    if remaining is not None and remaining <= 0:
        raise PresentationError("rendering exceeded its independent deadline", "render_timeout")
    return remaining


def _h264_copy(source: Path, destination: Path, max_bytes: int,
               started: float, budget: float | None, quality: str) -> dict:
    """Keep every encoded frame, using software H.264 for QQ compatibility."""
    video = pcb_module("pcb_video")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None and Path("/opt/homebrew/bin/ffmpeg").is_file():
        ffmpeg = "/opt/homebrew/bin/ffmpeg"
    if ffmpeg is None:
        raise PresentationError("ffmpeg is required for QQ-compatible H.264 presentation", "renderer_unavailable")
    for crf in (20, 27, 34):
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                   "-i", str(source), "-map", "0:v:0", "-an", "-c:v", "libx264",
                   "-preset", "medium", "-crf", str(crf), "-pix_fmt", "yuv420p",
                   "-threads", "1", "-movflags", "+faststart", "-map_metadata", "-1"]
        if quality == "preview":
            command.extend(["-vf", "scale=640:-2"])
        command.append(str(destination))
        try:
            process = subprocess.run(command, input="", text=True, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, check=False,
                                     timeout=_remaining(started, budget))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PresentationError("H.264 encoder failed or exceeded the rendering deadline", "render_failed") from exc
        if process.returncode or not destination.is_file():
            raise PresentationError("H.264 encoder failed: " + process.stderr[-400:], "render_failed")
        if destination.stat().st_size <= max_bytes:
            probe = video._decode_probe(destination, _remaining(started, budget))
            return {"codec": "H.264", "all_frames_decoded": True, "decode_verified": True,
                    "frame_count": probe["sample_count"], "duration_s": probe["duration_s"],
                    "width": probe["width"], "height": probe["height"], "crf": crf}
    raise PresentationError("video cannot fit the rendering byte budget", "render_too_large")


def render_simulation(result_path: str | Path, model_path: str | Path,
                      output_dir: str | Path, budget_seconds: float | None = None,
                      max_output_bytes: int = 16 * 1024 * 1024,
                      quality: str = "standard", domain: str = "physics") -> dict:
    """Render a saved result; requesting an unconfigured deep provider fails closed.

    Standard presentation uses the simulator's shaded, auto-fit scientific
    renderer. Short mechanical trajectories play for three seconds using the
    existing recorded-state interpolator. Physical timestamps remain attached
    to frames, and neither data nor numerical precision changes.
    """
    import math
    if quality not in {"preview", "standard"}:
        raise PresentationError("deep rendering requires a separately configured provider; local standard rendering is available",
                                "unsupported_render_quality")
    if type(max_output_bytes) is not int or not 1024 <= max_output_bytes <= 128 * 1024 * 1024:
        raise PresentationError("invalid render byte budget")
    if budget_seconds is not None and (type(budget_seconds) not in (int, float)
            or not math.isfinite(budget_seconds) or budget_seconds <= 0):
        raise PresentationError("invalid render wall-time budget")
    started = time.monotonic()
    source = load_source(result_path, model_path, domain)
    output = output_directory(output_dir, source)
    presentation = {"solver_result_unchanged": True, "temporal_interpolation": "none"}
    with tempfile.TemporaryDirectory(prefix=".pipeline-render-", dir=output) as temporary:
        folder = Path(temporary)
        intermediate = folder / "recorded-frames.mp4"
        candidate = folder / "simulation.mp4"
        if domain == "pcb_thermal":
            media = pcb_module("pcb_video").render_pcb_video(
                source["result"], source["model"], candidate,
                max_bytes=max_output_bytes, timeout_seconds=_remaining(started, budget_seconds))
            presentation["temporal_interpolation"] = ("linear saved thermal snapshots" if media["interpolated_display_frames"] else "none")
            presentation["physical_duration_s"] = source["result"]["duration_s"]
        else:
            from physics_demo.io.video import encode_bounded_mp4, encode_watchable_mp4
            frames = source["result"]["trajectory"]["frames"]
            start, end = float(frames[0]["t"]), float(frames[-1]["t"])
            physical_duration = end - start
            # The intermediate stays bounded independently of final H.264 size.
            intermediate_cap = min(128 * 1024 * 1024, max(4 * 1024 * 1024, max_output_bytes * 4))
            if physical_duration > 0:
                playback = min(30.0, max(3.0, physical_duration))
                raw_media = encode_watchable_mp4(
                    source["path"], intermediate, fps=15 if quality == "preview" else 30,
                    segments=[{"physical_start_s": start, "physical_end_s": end,
                               "playback_duration_s": playback}],
                    max_bytes=intermediate_cap, timeout_seconds=_remaining(started, budget_seconds))
                presentation = raw_media["presentation"]
                presentation["temporal_interpolation"] = "linear recorded display states; rotations use the engine interpolator"
            else:
                raw_media = encode_bounded_mp4(
                    source["path"], intermediate,
                    min(60, max(1, round(source["model"]["world"]["output_fps"]))),
                    intermediate_cap, _remaining(started, budget_seconds))
            media = _h264_copy(intermediate, candidate, max_output_bytes, started, budget_seconds, quality)
            if media["frame_count"] != raw_media["sample_count"]:
                raise PresentationError("H.264 transcode changed recorded presentation frame count", "render_verification_failed")
            if abs(media["duration_s"] - raw_media["duration_s"]) > .05:
                raise PresentationError("H.264 transcode changed presentation duration", "render_verification_failed")
            presentation["physical_duration_s"] = physical_duration
        _remaining(started, budget_seconds)
        verify_unchanged(source)
        target = output / "simulation.mp4"
        payload = candidate.read_bytes()
        manifest = {"schema": "pipeline-render/1", "domain": domain,
                    "path": str(target), "media_type": "video/mp4", "bytes": len(payload), "sha256": digest(payload),
                    "source_result_sha256": source["source_result_sha256"],
                    "source_model_sha256": source["source_model_sha256"],
                    "provider": "local-scientific", "quality": quality,
                    "solver_rerun": False, "measurement_source": False,
                    "numerical_usable": source["quality_gate"].get("numerical_passed") is True,
                    "quality_gate": source["quality_gate"], "sampling": source["sampling"],
                    "warnings": source["warnings"], "presentation": presentation, "media": media,
                    "fidelity": "recorded_state_interpolation", "generative": False}
        os.replace(candidate, target)
        atomic_write(output / "render-manifest.json", canonical(manifest))
        return {**manifest, "manifest_path": str(output / "render-manifest.json")}
