#!/usr/bin/env python3
"""Deterministic, networkless simulator release entrypoint.

The QQ message is routing data only.  It can select a shipped scene but cannot
provide Python, shell commands, filesystem paths, or arbitrary scene JSON.
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Tuple


RELEASE_ROOT = Path(__file__).resolve().parent
ENGINE_ROOT = RELEASE_ROOT / "physics_release"
SCENE_ROOT = ENGINE_ROOT / "scenes"
sys.path.insert(0, str(ENGINE_ROOT))


class UnsupportedRequest(ValueError):
    pass


def _read_request(path: Path) -> Dict[str, Any]:
    if path.stat().st_size > 64 * 1024:
        raise ValueError("request exceeds 64 KiB")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported request schema")
    message = payload.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("text"), str):
        raise ValueError("request.message.text must be a string")
    if len(message["text"]) > 1200:
        raise ValueError("request message exceeds 1200 characters")
    simulation_text = message.get("simulation_text")
    if simulation_text is not None and (
        not isinstance(simulation_text, str)
        or not simulation_text.strip()
        or len(simulation_text) > 1200
    ):
        raise ValueError(
            "request.message.simulation_text must be a non-empty string"
        )
    return payload


def _routing_text(request: Dict[str, Any]) -> str:
    message = request["message"]
    return str(message.get("simulation_text") or message["text"])


def _load_scene(name: str) -> Dict[str, Any]:
    path = (SCENE_ROOT / name).resolve()
    try:
        path.relative_to(SCENE_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("scene path escaped release catalog") from exc
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("release scene must be a JSON object")
    return payload


def route(text: str, *, seed: int = 0) -> Tuple[str, Dict[str, Any]]:
    compact = "".join(text.lower().split())
    has_drop = any(word in compact for word in (
        "水滴", "水珠", "一滴水", "waterdrop", "droplet",
    ))
    if (has_drop
            and any(word in compact for word in ("圆锥", "锥体", "cone"))):
        return "water_droplet_cone", _load_scene("droplet_cone.json")
    if any(word in compact for word in ('三体', 'threebody', 'three-body')):
        scene = _load_scene('three_body.json')
        if any(word in compact for word in ('随机', 'random')):
            rng = random.Random(seed)
            scene['name'] = 'three-body-randomized-near-figure-eight'
            for body in scene['entities']:
                for field in ('position', 'velocity'):
                    body[field] = [value + rng.uniform(-0.02, 0.02) if axis < 2 else value
                                   for axis, value in enumerate(body[field])]
            # Equal masses: remove bulk translation and drift without changing relative motion.
            for field in ('position', 'velocity'):
                center = [sum(body[field][axis] for body in scene['entities']) / 3 for axis in range(3)]
                for body in scene['entities']:
                    body[field] = [value - center[axis] for axis, value in enumerate(body[field])]
            scene['name'] += '-' + str(seed)
        return 'three_body', scene
    chinese_drop = (
        has_drop
        and any(word in compact for word in ("落到", "落在", "掉到", "撞到"))
        and any(word in compact for word in ("地板", "地面", "地上", "平面"))
    )
    english_drop = (
        any(word in compact for word in ("waterdrop", "droplet"))
        and any(word in compact for word in ("ground", "floor", "plane"))
    )
    if chinese_drop or english_drop:
        if any(word in compact for word in ("干燥", "干地面", "干地板", "dry")):
            scene_name = "droplet_ground_dry.json"
            route_name = "water_droplet_ground_dry"
        elif any(word in compact for word in ("细水珠", "毫米级", "微型水滴", "microdroplet")):
            scene_name = "droplet_ground_micro_wet.json"
            route_name = "water_droplet_ground_micro_wet"
        elif any(word in compact for word in ("高清", "高细节", "精细", "highdetail")):
            scene_name = "droplet_ground.json"
            route_name = "water_droplet_ground_balanced"
        elif any(word in compact for word in ("极速", "rapid")):
            scene_name = "droplet_ground_rapid.json"
            route_name = "water_droplet_ground_rapid"
        elif any(word in compact for word in ("快速", "预览", "fast")):
            scene_name = "droplet_ground_fast.json"
            route_name = "water_droplet_ground_fast"
        else:
            scene_name = "droplet_ground.json"
            route_name = "water_droplet_ground_balanced"
        scene = _load_scene(scene_name)
        scene["budget"]["backend"] = "native"
        return route_name, scene

    if "双摆" in compact or "doublependulum" in compact:
        from physics_demo import build_system

        scene = build_system(
            {
                "type": "double_pendulum",
                "lengths": [1.0, 1.0],
                "masses": [1.0, 1.0],
                "angles": [2.0, 2.4],
                "angular_velocities": [0.0, 0.0],
                "duration": 8.0,
                "dt": 0.002,
                "output_fps": 30,
                "quality": "balanced",
                "wall_time_s": 60,
            }
        )
        return "double_pendulum", scene

    raise UnsupportedRequest("current release supports water-drop-on-ground, double-pendulum and three-body videos")


def _watchable_segments(duration: float) -> list[dict[str, float]]:
    """Slow a short physical impact for inspection without changing its states."""
    first = duration * 0.25
    second = duration * 0.50
    return [
        {"physical_start_s": 0.0, "physical_end_s": 0.0, "playback_duration_s": 0.5},
        {"physical_start_s": 0.0, "physical_end_s": first, "playback_duration_s": 1.5},
        {"physical_start_s": first, "physical_end_s": second, "playback_duration_s": 2.2},
        {"physical_start_s": second, "physical_end_s": duration, "playback_duration_s": 1.8},
        {"physical_start_s": duration, "physical_end_s": duration, "playback_duration_s": 0.8},
    ]


def _impact_segments(duration: float) -> list[dict[str, float]]:
    """Give the recorded impact most of the playback time, without idle holds."""
    impact_start = duration * 0.5
    return [
        {"physical_start_s": 0.0, "physical_end_s": impact_start, "playback_duration_s": 0.7},
        {"physical_start_s": impact_start, "physical_end_s": duration, "playback_duration_s": 3.3},
    ]


def _fine_impact_segments(duration: float) -> list[dict[str, float]]:
    """Show the 3 mm drop's short impact without extending its settled tail."""
    approach = min(0.05, duration * 0.25)
    active_end = min(0.15, duration * 0.8)
    return [
        {"physical_start_s": 0.0, "physical_end_s": approach, "playback_duration_s": 0.7},
        {"physical_start_s": approach, "physical_end_s": active_end, "playback_duration_s": 2.7},
        {"physical_start_s": active_end, "physical_end_s": duration, "playback_duration_s": 0.6},
    ]


def _short_water_impact(scene: dict) -> bool:
    """Recognize a prepared drop impact without relying on its generated name."""
    world = scene.get("world", {})
    duration = world.get("duration")
    gravity = world.get("gravity")
    if not isinstance(duration, (int, float)) or not 0 < duration <= 2.0:
        return False
    if not isinstance(gravity, list) or len(gravity) != 3 or gravity[1] >= 0:
        return False
    planes = [
        collider for collider in scene.get("colliders", [])
        if collider.get("type") == "plane"
        and collider.get("normal") == [0, 1, 0]
    ]
    for entity in scene.get("entities", []):
        if (entity.get("type") != "fluid" or entity.get("preset") != "water"
                or entity.get("shape", {}).get("type") != "sphere"):
            continue
        shape = entity["shape"]
        center = shape.get("center")
        radius = shape.get("radius")
        if (isinstance(center, list) and len(center) == 3
                and isinstance(radius, (int, float))
                and any(center[1] - radius > plane.get("offset", 0)
                        for plane in planes)):
            return True
    return False


def _needs_watchable_video(scene: dict, route_name: str, request_text: str) -> bool:
    normalized = "".join(request_text.lower().split())
    if any(word in normalized for word in ("实时", "原速", "正常速度", "realtime", "real-time")):
        return False
    return route_name.startswith("water_droplet_ground_") or _short_water_impact(scene)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _publish_watchable_video(
    artifacts: Path, summary: Dict[str, Any], duration: float,
    camera_zoom: float = 1.0, camera_focus_quantile: float | None = None,
    water_renderer: str = "continuous",
    segments: list[dict[str, float]] | None = None,
) -> Dict[str, Any]:
    from physics_demo.analysis.results import _summary
    from physics_demo.io.video import encode_watchable_mp4

    video = artifacts / "simulation.mp4"
    temporary_video = artifacts / ".simulation-watchable.tmp.mp4"
    try:
        metadata = encode_watchable_mp4(
            artifacts / "result.json",
            temporary_video,
            fps=30,
            segments=segments or _watchable_segments(duration),
            timeout_seconds=30.0,
            camera_zoom=camera_zoom,
            camera_focus_quantile=camera_focus_quantile,
            water_renderer=water_renderer,
        )
        os.replace(temporary_video, video)
    finally:
        temporary_video.unlink(missing_ok=True)

    metadata["path"] = str(video.resolve())
    metadata["bytes"] = video.stat().st_size
    metadata["physical_duration_s"] = duration
    metadata["time_scale_to_physical"] = duration / metadata["duration_s"]
    validation = metadata.get("decode_validation", {})
    metadata["width"] = validation.get("first_frame_width")
    metadata["height"] = validation.get("first_frame_height")

    result_path = artifacts / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result.setdefault("artifacts", {})["video"] = metadata
    _atomic_write_json(result_path, result)

    refreshed_summary = _summary(
        result,
        artifacts,
        verified_video_probe=metadata["decode_validation"],
    )
    if not refreshed_summary.get("ok") or not refreshed_summary.get(
        "quality_gate", {}
    ).get("passed"):
        raise RuntimeError("watchable video failed the final release quality gate")
    refreshed_summary["artifact_persistence_s"] = summary.get(
        "artifact_persistence_s", 0.0
    )
    _atomic_write_json(artifacts / "summary.json", refreshed_summary)
    summary.clear()
    summary.update(refreshed_summary)
    return metadata


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise ValueError("usage: run_simulation.py REQUEST_JSON ARTIFACTS_DIR")
    request_path = Path(argv[1]).resolve()
    artifacts = Path(argv[2]).resolve()
    if request_path.parent != artifacts.parent:
        raise ValueError("request and artifacts must belong to the same job")
    if artifacts.name != "artifacts" or request_path.name != "request.json":
        raise ValueError("unexpected release paths")
    request = _read_request(request_path)
    seed = request.get('seed', 0)
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError('seed must be an unsigned 64-bit integer')
    route_name, scene = route(_routing_text(request), seed=seed)

    scene.setdefault('budget', {}).setdefault('validation', 'visual')
    return simulate_scene(scene, artifacts, route_name, _routing_text(request))


def simulate_scene(
    scene: dict, artifacts: Path, route_name: str = "agent_authored", request_text: str = ""
) -> int:
    from physics_demo.runner import simulate

    summary = simulate(scene, artifacts, make_video=True)
    video = artifacts / "simulation.mp4"
    if not summary.get("ok") or not summary.get("quality_gate", {}).get("passed"):
        print(json.dumps({"ok": False, "route": route_name, "summary": summary}, ensure_ascii=False))
        return 2
    if not video.is_file() or video.stat().st_size <= 0:
        raise RuntimeError("verified simulation completed without simulation.mp4")
    presentation = None
    if _needs_watchable_video(scene, route_name, request_text):
        micro_film = any(
            entity.get("id") == "film" and entity.get("type") == "fluid"
            for entity in scene.get("entities", [])
        )
        cone = route_name == "water_droplet_cone" or scene.get("name") == "water-drop-on-cone-splash"
        macro_ground = route_name in {
            "water_droplet_ground_balanced",
            "water_droplet_ground_fast",
            "water_droplet_ground_rapid",
        }
        fine_ground = route_name in {
            "water_droplet_ground_dry",
            "water_droplet_ground_micro_wet",
        }
        duration = float(scene["world"]["duration"])
        metadata = _publish_watchable_video(
            artifacts, summary, duration,
            camera_zoom=1.5 if micro_film else (1.6 if cone else 1.0),
            camera_focus_quantile=0.99 if micro_film else None,
            water_renderer="mesh_hybrid" if cone else (
                "legacy_v2" if macro_ground or fine_ground else "continuous"
            ),
            segments=_impact_segments(duration) if macro_ground or cone
            else (_fine_impact_segments(duration) if fine_ground else None),
        )
        presentation = metadata["presentation"]
    print(
        json.dumps(
            {
                "ok": True,
                "route": route_name,
                "video": str(video),
                "bytes": video.stat().st_size,
                "quality_gate": summary["quality_gate"],
                "presentation": presentation,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except UnsupportedRequest as exc:
        print(json.dumps({"ok": False, "code": "unsupported_request", "error": str(exc)}))
        raise SystemExit(3)
    except Exception as exc:
        print(json.dumps({"ok": False, "code": "release_error", "error": str(exc)}))
        raise SystemExit(1)
