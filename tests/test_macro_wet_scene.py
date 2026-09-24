"""The macroscopic wet-floor scene keeps its film and dry/wet routing explicit."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

from physics_demo.catalog import example
from physics_demo.runner import prepare
from toolboxes import build_physics


ROOT = Path(__file__).resolve().parents[1]
ROUTE_PATH = ROOT / "toolboxes" / "physics" / "run_simulation.py"
SPEC = importlib.util.spec_from_file_location("macro_wet_route", ROUTE_PATH)
assert SPEC is not None and SPEC.loader is not None
route_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(route_module)


class MacroWetSceneTests(unittest.TestCase):
    def test_catalog_scene_declares_film_and_uncalibrated_cohesion(self):
        loaded = example("droplet_ground_macro_wet")
        self.assertTrue(loaded["ok"], loaded)
        scene = loaded["scene"]
        self.assertIn("4cm", scene["name"])
        self.assertEqual(scene["world"]["duration"], 0.8)
        self.assertEqual(scene["world"]["gravity"], [0, -9.81, 0])
        self.assertEqual(scene["entities"][0]["shape"]["radius"], 0.32)
        film = next(entity for entity in scene["entities"] if entity["id"] == "film")
        self.assertEqual(film["shape"]["size"], [1.0, 0.04, 1.0])
        self.assertEqual(film["shape"]["center"][1], 0.02)
        self.assertEqual(
            [entity["properties"]["surface_tension"] for entity in scene["entities"]],
            [0.055, 0.055],
        )
        self.assertIn("uncalibrated", loaded["limitations"])
        self.assertTrue(prepare(scene)["ready_to_simulate"])

    def test_release_build_includes_wet_scene_and_keeps_dry_scene(self):
        scenes = build_physics._scene_documents()
        self.assertEqual(
            scenes["droplet_ground_macro_wet.json"],
            json.loads((ROOT / "toolboxes" / "physics" / "physics_release" / "scenes"
                        / "droplet_ground_macro_wet.json").read_text()),
        )
        self.assertEqual(len(scenes["droplet_ground_splash.json"]["entities"]), 1)
        self.assertEqual(len(scenes["droplet_ground_dry.json"]["entities"]), 1)

    def test_router_distinguishes_macro_wet_micro_wet_and_dry(self):
        for prompt in (
            "一滴水落到有水膜的地面并飞溅",
            "一滴水落到预湿地面并飞溅",
            "一滴水落到地面并飞溅",
            "water droplet splashes on a pre-wetted floor",
        ):
            with self.subTest(prompt=prompt):
                name, scene = route_module.route(prompt)
                self.assertEqual(name, "water_droplet_ground_macro_wet")
                self.assertEqual(scene["entities"][1]["shape"]["size"][1], 0.04)
                self.assertEqual(route_module._water_impact_display(scene)[0], "cohesive_spray")

        micro_name, micro = route_module.route("细水珠落到有水膜的地面飞溅")
        self.assertEqual(micro_name, "water_droplet_ground_micro_wet")
        self.assertEqual(micro["entities"][1]["shape"]["size"][1], 0.0012)
        self.assertEqual(route_module._water_impact_display(micro)[0], "legacy_v2")

        dry_name, dry = route_module.route("一滴水落到干燥地面并飞溅")
        self.assertEqual(dry_name, "water_droplet_ground_splash")
        self.assertEqual(len(dry["entities"]), 1)
        self.assertEqual(route_module._water_impact_display(dry)[0], "legacy_v2")


if __name__ == "__main__":
    unittest.main()
