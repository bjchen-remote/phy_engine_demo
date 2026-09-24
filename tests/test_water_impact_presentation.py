"""Water impact display follows the authored geometry, not a catalog route name."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest

from physics_demo.catalog import example
from physics_demo.io.video import _retime_result_document


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "toolboxes" / "physics" / "run_simulation.py"
SPEC = importlib.util.spec_from_file_location("release_water_impact", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def scene(name: str) -> dict:
    return json.loads((ROOT / "toolboxes" / "physics" / "physics_release" / "scenes" / name).read_text())


class WaterImpactPresentationTests(unittest.TestCase):
    def assert_uniform_timing(self, model: dict, segments: list[dict[str, float]]) -> None:
        duration = model["world"]["duration"]
        self.assertEqual(segments, [{
            "physical_start_s": 0.0,
            "physical_end_s": duration,
            "playback_duration_s": max(4.0, min(12.0, 8.0 * duration)),
        }])

    def test_retired_sphere_profiles_are_not_catalog_material(self):
        retired = {"droplet-on-sphere", "high-detail-droplet-on-sphere"}
        for name in ("droplet_sphere", "high_detail_droplet_sphere"):
            loaded = example(name)
            self.assertTrue(loaded["ok"], loaded)
            model = loaded["scene"]
            self.assertNotIn(model["name"], retired)
            self.assertEqual(model["world"]["duration"], 0.8)
            self.assertEqual(model["world"]["output_fps"], 60)
            self.assertEqual(model["entities"][0]["preset"], "water")
            self.assertTrue(any(entity["type"] == "rigid" for entity in model["entities"]))
        release_scenes = ROOT / "toolboxes" / "physics" / "physics_release" / "scenes"
        for old in ("droplet_ground.json", "droplet_ground_fast.json", "droplet_ground_rapid.json"):
            self.assertFalse((release_scenes / old).exists())

    def test_authored_large_ground_drop_gets_visible_beads_and_impact_timing(self):
        authored = scene("droplet_ground_splash.json")
        authored["name"] = "agent-authored-water"
        authored["world"].update(duration=1.45, output_fps=24)
        authored["world"]["bounds"]["min"][0] = -1.6
        authored["world"]["bounds"]["max"][0] = 1.6
        original = copy.deepcopy(authored)
        self.assertTrue(release._needs_watchable_video(authored, "agent_authored", "水滴落到地面"))
        renderer, zoom, focus, segments = release._water_impact_display(authored)
        self.assertEqual(renderer, "legacy_v2")
        self.assertEqual(zoom, 1.7)
        self.assertIsNone(focus)
        self.assert_uniform_timing(authored, segments)
        self.assertEqual(segments[0]["playback_duration_s"], 11.6)
        self.assertEqual(authored, original)

    def test_physical_time_and_linear_motion_advance_steadily_through_contact(self):
        dry = scene("droplet_ground_dry.json")
        authored = scene("droplet_ground_splash.json")
        authored["world"]["duration"] = 1.45
        for model in (dry, authored):
            with self.subTest(duration=model["world"]["duration"]):
                duration = model["world"]["duration"]
                segments = release._water_impact_display(model)[3]
                source = {"trajectory": {"frames": [
                    {"t": t, "p": [[t, 0.0, 0.0]]}
                    for t in (0.0, duration / 2.0, duration)
                ]}}
                retimed, _ = _retime_result_document(source, fps=30, segments=segments)
                frames = retimed["trajectory"]["frames"]
                times = [frame["t"] for frame in frames]
                positions = [frame["p"][0][0] for frame in frames]
                time_steps = [right - left for left, right in zip(times, times[1:])]
                position_steps = [right - left for left, right in zip(positions, positions[1:])]
                self.assertGreater(min(time_steps), 0)
                self.assertLess(max(time_steps) - min(time_steps), 1.0e-10)
                self.assertLess(max(position_steps) - min(position_steps), 1.0e-10)
                self.assertAlmostEqual(times[-1], duration)
                self.assertAlmostEqual(positions[-1], duration)

    def test_small_dry_drop_keeps_dry_beads_and_wet_film_is_explicit(self):
        dry = scene("droplet_ground_dry.json")
        renderer, zoom, focus, segments = release._water_impact_display(dry)
        self.assertEqual((renderer, zoom, focus), ("legacy_v2", 1.0, None))
        self.assertEqual(len(dry["entities"]), 1)
        self.assert_uniform_timing(dry, segments)
        self.assertEqual(segments[0]["playback_duration_s"], 4.0)

        wet = scene("droplet_ground_micro_wet.json")
        renderer, zoom, focus, segments = release._water_impact_display(wet)
        self.assertEqual((renderer, zoom, focus), ("legacy_v2", 1.5, 0.99))
        self.assertTrue(any(entity["id"] == "film" for entity in wet["entities"]))
        self.assert_uniform_timing(wet, segments)

    def test_authored_coupled_mesh_drop_uses_occluding_hybrid(self):
        cone = scene("droplet_cone.json")
        cone["name"] = "agent-authored-cone"
        renderer, zoom, focus, segments = release._water_impact_display(cone)
        self.assertEqual((renderer, zoom, focus), ("mesh_hybrid", 1.6, None))
        self.assertLess(release._impact_time(cone), 0.4)
        self.assert_uniform_timing(cone, segments)

    def test_static_sphere_impact_uses_beads_and_its_earlier_contact(self):
        sphere = json.loads((ROOT / "examples" / "droplet_sphere.json").read_text())
        renderer, zoom, focus, segments = release._water_impact_display(sphere)
        self.assertEqual((renderer, zoom, focus), ("legacy_v2", 1.7, None))
        self.assertLess(release._impact_time(sphere), 0.4)
        self.assert_uniform_timing(sphere, segments)

        moving_sphere = copy.deepcopy(sphere)
        moving_sphere["entities"][1]["mass"] = 1.0
        renderer, _, _, segments = release._water_impact_display(moving_sphere)
        self.assertEqual(renderer, "continuous")
        self.assert_uniform_timing(moving_sphere, segments)

    def test_unrelated_water_scene_keeps_its_renderer(self):
        blob = json.loads((ROOT / "examples" / "water_blob.json").read_text())
        self.assertIsNone(release._impact_time(blob))
        renderer, _, _, segments = release._water_impact_display(blob)
        self.assertEqual(renderer, "continuous")
        self.assert_uniform_timing(blob, segments)


if __name__ == "__main__":
    unittest.main()
