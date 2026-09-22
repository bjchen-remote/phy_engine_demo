"""Liquid presets stay discoverable, deterministic, and separate from the C ABI."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from physics_demo.api import call_tool
from physics_demo.core.liquids import LIQUID_PRESETS, LIQUID_PRESET_NAMES, render_material_names
from physics_demo.core.state import Particle
from physics_demo.core.backend import run_scene
from physics_demo.core.native_backend import NativeBackendUnavailable, _render_indices
from physics_demo.io.jsonio import canonical_bytes
from physics_demo.io.planning import make_plan
from physics_demo.runner import inspect, prepare, simulate, validate


ROOT = Path(__file__).resolve().parents[1]


def base_scene() -> dict:
    return json.loads((ROOT / "examples" / "droplet_ground.json").read_text())


class LiquidPresetTests(unittest.TestCase):
    def test_each_preset_expands_to_exact_model_coefficients(self):
        for name in LIQUID_PRESET_NAMES:
            with self.subTest(name=name):
                scene = base_scene()
                entity = scene["entities"][0]
                entity["preset"] = name
                entity.pop("properties", None)
                result = validate(scene)
                self.assertTrue(result["ok"], result)
                normalized = result["scene"]["entities"][0]
                self.assertEqual(normalized["preset"], name)
                self.assertEqual(normalized["properties"], LIQUID_PRESETS[name]["properties"])
                self.assertTrue(validate(result["scene"])["ok"])

    def test_unknown_preset_and_python_non_water_are_rejected(self):
        scene = base_scene()
        scene["entities"][0]["preset"] = "unobtainium"
        result = validate(scene)
        self.assertFalse(result["ok"])
        self.assertIn("liquid_preset", {error["code"] for error in result["errors"]})

        scene = base_scene()
        scene["entities"][0]["preset"] = "honey"
        scene["budget"]["backend"] = "python"
        result = validate(scene)
        self.assertFalse(result["ok"])
        self.assertIn("unsupported_python_liquid_preset", {error["code"] for error in result["errors"]})

    def test_auto_never_prepares_an_impossible_native_liquid_python_rigid_mix(self):
        scene = base_scene()
        scene["entities"][0]["preset"] = "honey"
        scene["entities"].append({
            "id": "ball", "type": "rigid", "mass": 1.0,
            "shape": {"type": "sphere", "radius": 0.1},
            "position": [0.8, 0.5, 0], "velocity": [0, 0, 0],
        })
        result = prepare(scene)
        self.assertFalse(result["ok"], result)
        self.assertFalse(result["ready_to_simulate"])
        self.assertIn(
            "unsupported_liquid_dynamic_rigid_combination",
            {error["code"] for error in result["errors"]},
        )

    def test_explicit_coefficients_are_intentional_overrides(self):
        scene = base_scene()
        entity = scene["entities"][0]
        entity["preset"] = "honey"
        entity["properties"] = {"viscosity": 0.5, "surface_tension": 0.1}
        result = validate(scene)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["scene"]["entities"][0]["properties"], entity["properties"])

    def test_prepare_repeats_the_material_model_boundary(self):
        scene = base_scene()
        scene["entities"][0]["preset"] = "molten_lead"
        scene["entities"][0].pop("properties", None)
        result = prepare(scene)
        self.assertTrue(result["ok"], result)
        disclosure = "\n".join(result["assumptions"])
        self.assertIn("Liquid preset model boundary", disclosure)
        self.assertIn("Density, heat, freezing, oxidation, and toxicity are not modeled", disclosure)

    def test_pre_v1_implicit_water_result_remains_inspectable(self):
        scene = base_scene()
        scene["world"].update(duration=0.02, output_fps=10)
        with tempfile.TemporaryDirectory() as directory:
            summary = simulate(scene, directory, 10, make_video=False)
            self.assertTrue(summary["ok"], summary)
            self.assertEqual(summary["result_version"], 1)
            result_path = Path(directory) / "result.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(result.pop("result_version"), 1)
            for entity in result["scene"]["entities"]:
                if entity["type"] == "fluid":
                    self.assertEqual(entity.pop("preset"), "water")
            result["plan"] = make_plan(
                result["scene"], include_video=False, legacy_video_estimate=True,
            )
            result["scene_hash"] = hashlib.sha256(canonical_bytes(result["scene"])).hexdigest()
            result["assumptions"] = [
                item.replace("preset='water', ", "")
                for item in result["assumptions"]
                if not item.startswith("Liquid preset model boundary for entity ")
            ]
            result["physics_claims"]["fluid"] = (
                "Native C11 DFSPH with an Akinci surface force is the default; it constrains "
                "density and velocity divergence but remains an uncalibrated visual fluid model."
            )
            result_path.write_text(json.dumps(result), encoding="utf-8")
            completion_path = Path(directory) / "completion.json"
            completion = json.loads(completion_path.read_text(encoding="utf-8"))
            completion["scene_hash"] = result["scene_hash"]
            completion_path.write_text(json.dumps(completion), encoding="utf-8")

            inspected = inspect(result_path)
            self.assertTrue(inspected["ok"], inspected)
            self.assertTrue(inspected["solver_ok"])
            self.assertEqual(inspected["result_version"], 0)
            self.assertEqual(inspected["fidelity"]["fluid"], result["physics_claims"]["fluid"])

    def test_tool_returns_atomic_patch_fields_and_capabilities(self):
        capabilities = call_tool("physics_capabilities", {})
        presets = capabilities["capabilities"]["engines"]["fluid"]["presets"]
        self.assertEqual(tuple(presets), LIQUID_PRESET_NAMES)
        self.assertIn("蜂蜜", presets["honey"]["aliases"])
        self.assertFalse(presets["water"]["native_required"])
        self.assertTrue(all(presets[name]["native_required"] for name in ("honey", "glue", "molten_lead")))
        for name in LIQUID_PRESET_NAMES:
            result = call_tool("physics_liquid", {"preset": name, "entity_id": "drop"})
            self.assertTrue(result["ok"], result)
            self.assertEqual(len(result["patch_arguments"]["operations"]), 2)
            self.assertEqual(result["next_action"]["kind"], "choose")
            self.assertIn(
                "physics_patch",
                {option["tool"] for option in result["next_action"]["options"]},
            )
        self.assertFalse(call_tool("physics_liquid", {"preset": "unobtainium", "entity_id": "drop"})["ok"])

    def test_liquid_state_machine_requires_a_scene_before_patch(self):
        capabilities = call_tool("physics_capabilities", {})
        preferred_flow = capabilities["capabilities"]["agent_interface"]["preferred_flow"]
        scene_step = next(index for index, step in enumerate(preferred_flow) if "complete scene_json" in step)
        liquid_step = next(index for index, step in enumerate(preferred_flow) if "physics_liquid" in step)
        self.assertLess(scene_step, liquid_step)
        self.assertIn("physics_patch", preferred_flow[liquid_step])
        self.assertIn(
            "does not contain scene_json",
            capabilities["capabilities"]["engines"]["fluid"]["preset_interface"],
        )
        capability_options = {
            option["tool"]: option for option in capabilities["next_action"]["options"]
        }
        liquid_entry = capability_options["physics_liquid"]
        self.assertIn("complete scene_json", liquid_entry["when"])
        self.assertIn("target fluid entity ID", liquid_entry["when"])
        self.assertTrue(liquid_entry["preconditions"])

        example = call_tool("physics_example", {"name": "droplet_ground"})
        self.assertIn(
            "physics_liquid",
            {option["tool"] for option in example["next_action"]["options"]},
        )
        liquid = call_tool("physics_liquid", {"preset": "honey", "entity_id": "drop"})
        self.assertNotIn("scene_json", liquid)
        action = liquid["next_action"]
        self.assertEqual(action["kind"], "choose")
        self.assertIn("not scene_json", action["decision"])
        options = {option["tool"]: option for option in action["options"]}
        self.assertEqual(set(options), {"physics_patch", "physics_example", "physics_system"})
        self.assertIn("complete scene_json", options["physics_patch"]["when"])
        self.assertTrue(any(
            "Pass that scene_json separately" in item
            for item in options["physics_patch"]["preconditions"]
        ))

        patched = call_tool("physics_patch", {
            "scene_json": example["scene_json"],
            "operations": liquid["patch_arguments"]["operations"],
        })
        self.assertTrue(patched["ok"], patched)
        prepared = call_tool("physics_prepare", {"scene_json": patched["scene_json"]})
        self.assertTrue(prepared["ready_to_simulate"], prepared)

    def test_liquid_tool_uses_the_scene_identifier_contract_and_escapes_selectors(self):
        scene = base_scene()
        scene["entities"][0]["id"] = "水滴/主~体"
        helper = call_tool("physics_liquid", {"preset": "glue", "entity_id": "水滴/主~体"})
        self.assertTrue(helper["ok"], helper)
        patched = call_tool("physics_patch", {
            "scene_json": json.dumps(scene, ensure_ascii=False),
            "operations": helper["patch_arguments"]["operations"],
        })
        self.assertTrue(patched["ok"], patched)
        self.assertEqual(patched["scene"]["entities"][0]["preset"], "glue")

    def test_render_metadata_maps_group_without_mutating_physics_material(self):
        scene = base_scene()
        scene["entities"][0]["preset"] = "glue"
        particles = [Particle([0, 0, 0], [0, 0, 0], "water", "drop", 0.02),
                     Particle([0, 0, 0], [0, 0, 0], "sand", "sand", 0.02)]
        original = copy.deepcopy(particles)
        self.assertEqual(render_material_names(scene, particles), ["glue", "sand"])
        self.assertEqual(particles, original)

    def test_render_decimation_retains_small_groups_and_visual_presets(self):
        materials = [0] * 202
        groups = ["water-main"] * 100 + ["honey-tiny"] + ["water-tiny"] + ["glue-main"] * 100
        visual = ["water"] * 100 + ["honey", "water"] + ["glue"] * 100

        selected = _render_indices(
            materials, 4, groups=groups, visual_materials=visual,
        )
        self.assertEqual(len(selected), 4)
        self.assertEqual(selected, sorted(set(selected)))
        self.assertEqual({groups[index] for index in selected}, set(groups))
        self.assertEqual({visual[index] for index in selected}, set(visual))
        self.assertEqual(
            selected,
            _render_indices(materials, 4, groups=groups, visual_materials=visual),
        )
        self.assertEqual(
            _render_indices(materials, 4),
            _render_indices(
                materials, 4,
                groups=["one-group"] * len(materials),
                visual_materials=["water"] * len(materials),
            ),
        )

        # If every group cannot fit, visual presets still receive one slot each.
        crowded_groups = [f"group-{index}" for index in range(len(materials))]
        fallback_visual = ["water"] * 50 + ["honey"] + ["water"] * 50 + ["glue"] * 101
        self.assertNotIn(
            "honey", {fallback_visual[index] for index in _render_indices(materials, 3)},
        )
        visual_only = _render_indices(
            materials, 3, groups=crowded_groups, visual_materials=fallback_visual,
        )
        self.assertEqual(len(visual_only), 3)
        self.assertEqual({fallback_visual[index] for index in visual_only}, set(fallback_visual))

    def test_non_water_auto_never_silently_falls_back(self):
        scene = validate({**base_scene(), "budget": {
            "wall_time_s": 55, "quality": "balanced", "backend": "auto"}})["scene"]
        scene["entities"][0]["preset"] = "honey"
        plan = {"backend": "native", "requested_backend": "auto"}
        with patch("physics_demo.core.backend.run_scene_native",
                   side_effect=NativeBackendUnavailable("compiler unavailable")), \
             patch("physics_demo.core.backend.run_scene_python") as fallback:
            with self.assertRaises(NativeBackendUnavailable):
                run_scene(scene, plan, float("inf"))
            fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
