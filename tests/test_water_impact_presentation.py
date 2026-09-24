"""Water impact display follows the authored geometry, not a catalog route name."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest

from physics_demo.catalog import example


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "toolboxes" / "physics" / "run_simulation.py"
SPEC = importlib.util.spec_from_file_location("release_water_impact", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def scene(name: str) -> dict:
    return json.loads((ROOT / "toolboxes" / "physics" / "physics_release" / "scenes" / name).read_text())


class WaterImpactPresentationTests(unittest.TestCase):
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
        self.assertLess(segments[0]["physical_end_s"], 0.5)
        self.assertGreater(segments[1]["physical_end_s"], 0.5)
        self.assertEqual(segments[-1]["physical_end_s"], 1.45)
        self.assertEqual(authored, original)

    def test_small_dry_drop_keeps_dry_beads_and_wet_film_is_explicit(self):
        dry = scene("droplet_ground_dry.json")
        renderer, zoom, focus, _ = release._water_impact_display(dry)
        self.assertEqual((renderer, zoom, focus), ("legacy_v2", 1.0, None))
        self.assertEqual(len(dry["entities"]), 1)

        wet = scene("droplet_ground_micro_wet.json")
        renderer, zoom, focus, _ = release._water_impact_display(wet)
        self.assertEqual((renderer, zoom, focus), ("legacy_v2", 1.5, 0.99))
        self.assertTrue(any(entity["id"] == "film" for entity in wet["entities"]))

    def test_authored_coupled_mesh_drop_uses_occluding_hybrid(self):
        cone = scene("droplet_cone.json")
        cone["name"] = "agent-authored-cone"
        renderer, zoom, focus, segments = release._water_impact_display(cone)
        self.assertEqual((renderer, zoom, focus), ("mesh_hybrid", 1.6, None))
        self.assertLess(release._impact_time(cone), 0.4)
        self.assertLess(segments[0]["physical_end_s"], 0.4)
        self.assertEqual(segments[-1]["physical_end_s"], cone["world"]["duration"])

    def test_static_sphere_impact_uses_beads_and_its_earlier_contact(self):
        sphere = json.loads((ROOT / "examples" / "droplet_sphere.json").read_text())
        renderer, zoom, focus, segments = release._water_impact_display(sphere)
        self.assertEqual((renderer, zoom, focus), ("legacy_v2", 1.7, None))
        self.assertLess(release._impact_time(sphere), 0.4)
        self.assertLess(segments[0]["physical_end_s"], 0.4)

        moving_sphere = copy.deepcopy(sphere)
        moving_sphere["entities"][1]["mass"] = 1.0
        self.assertEqual(release._water_impact_display(moving_sphere)[0], "continuous")

    def test_unrelated_water_scene_keeps_its_renderer(self):
        blob = json.loads((ROOT / "examples" / "water_blob.json").read_text())
        self.assertIsNone(release._impact_time(blob))
        self.assertEqual(release._water_impact_display(blob)[0], "continuous")


if __name__ == "__main__":
    unittest.main()
