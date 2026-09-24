from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

try:
    from jsonschema import Draft202012Validator
except ImportError:  # Optional developer dependency; runtime has no JSON Schema dependency.
    Draft202012Validator = None

from physics_demo.api import call_tool
from physics_demo import __version__
from physics_demo.agent_contract import guide_tool_result
from physics_demo.backend import FallbackBudgetExceeded
from physics_demo.native_backend import NativeBackendUnavailable, NativeSimulationError
from physics_demo.engine import (
    Particle,
    _project_particle_contacts,
    build_neighbors,
    project_colliders,
)
from physics_demo.math3d import unit
from physics_demo.runner import estimate, inspect, load_scene, patch_scene, prepare, simulate, validate
from physics_demo.video import VideoEncodingError
import physics_demo.video as video_module
import physics_demo.analysis.results as results_module


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


class ContractTests(unittest.TestCase):
    def test_watchable_camera_changes_only_the_temporary_encoder_document(self):
        positions = [[-1000.0, -1000.0, -1000.0]] + [
            [float(index), float(index) / 10.0, -float(index) / 100.0]
            for index in range(199)
        ] + [[1000.0, 1000.0, 1000.0]]
        source = {
            "scene": {"name": "camera-source"},
            "trajectory": {"particle_materials": ["water"], "frames": [
                {"t": 0.0, "p": positions, "g": [], "r": []},
                {"t": 1.0, "p": positions, "g": [], "r": []},
            ]},
        }
        captured = {}

        def inspect_encoder(result_path, output_path, fps, *, timeout_seconds):
            captured["document"] = json.loads(result_path.read_text(encoding="utf-8"))
            captured["fps"] = fps
            return {"renderer": {}}

        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "result.json"
            result_path.write_text(json.dumps(source), encoding="utf-8")
            original_bytes = result_path.read_bytes()
            with patch.object(video_module, "encode_mp4", side_effect=inspect_encoder):
                metadata = video_module.encode_watchable_mp4(
                    result_path,
                    Path(directory) / "simulation.mp4",
                    fps=2,
                    segments=[{
                        "physical_start_s": 0.0,
                        "physical_end_s": 1.0,
                        "playback_duration_s": 2.0,
                    }],
                    camera_zoom=1.5,
                    camera_focus_quantile=0.99,
                    water_renderer="legacy_v2",
                )
            self.assertEqual(result_path.read_bytes(), original_bytes)
            self.assertEqual(json.loads(result_path.read_text(encoding="utf-8")), source)

        rendered = captured["document"]
        self.assertEqual(captured["fps"], 2)
        self.assertEqual(len(rendered["trajectory"]["frames"]), 4)
        self.assertEqual(rendered["scene"]["__presentation_camera_zoom"], 1.5)
        self.assertEqual(rendered["scene"]["__presentation_water_renderer"], "legacy_v2")
        corners = rendered["scene"]["__presentation_camera_corners"]
        self.assertEqual(len(corners), 8)
        self.assertEqual((min(point[0] for point in corners), max(point[0] for point in corners)), (0.0, 198.0))
        self.assertEqual((min(point[1] for point in corners), max(point[1] for point in corners)), (0.0, 19.8))
        self.assertEqual((min(point[2] for point in corners), max(point[2] for point in corners)), (-1.98, 0.0))
        self.assertEqual(metadata["presentation"]["camera_zoom"], 1.5)
        self.assertEqual(metadata["presentation"]["camera_focus_quantile"], 0.99)
        self.assertEqual(metadata["presentation"]["water_renderer"], "legacy_v2")
        self.assertTrue(metadata["presentation"]["solver_result_unchanged"])

    def test_watchable_video_retimes_display_frames_without_mutating_solver_result(self):
        source = {
            "trajectory": {
                "frames": [
                    {"t": 0.0, "p": [[0.0, 0.0, 0.0]], "g": [], "r": []},
                    {"t": 1.0, "p": [[3.0, 0.0, 0.0]], "g": [], "r": []},
                ]
            }
        }
        original = json.loads(json.dumps(source))
        retimed, presentation = video_module._retime_result_document(
            source,
            fps=2,
            segments=[{
                "physical_start_s": 0.0,
                "physical_end_s": 1.0,
                "playback_duration_s": 2.0,
            }],
        )
        frames = retimed["trajectory"]["frames"]
        self.assertEqual(source, original)
        self.assertEqual(len(frames), 4)
        self.assertAlmostEqual(frames[1]["t"], 1 / 3)
        self.assertAlmostEqual(frames[1]["p"][0][0], 1.0)
        self.assertAlmostEqual(frames[2]["p"][0][0], 2.0)
        self.assertEqual(frames[-1], original["trajectory"]["frames"][-1])
        self.assertEqual(presentation["playback_duration_s"], 2.0)
        self.assertEqual(presentation["time_scale_to_physical"], 0.5)
        self.assertTrue(presentation["solver_result_unchanged"])

    def test_verified_watchable_video_may_have_more_frames_than_solver_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video_path = root / "simulation.mp4"
            video_path.write_bytes(b"\0\0\0\x18ftyp" + b"x" * 2000)
            probe = {
                "first_frame_width": 960,
                "first_frame_height": 540,
                "track_duration_s": 2.0,
                "codec_tag": "jpeg",
                "sample_count": 4,
                "status": "software_all_mjpeg_samples_validated",
            }
            result = {
                "trajectory": {
                    "frames": [{"t": 0.0}, {"t": 1.0}],
                    "diagnostics": {"simulated_time_s": 1.0},
                },
                "artifacts": {
                    "video": {
                        "path": str(video_path),
                        "bytes": video_path.stat().st_size,
                        "codec": "Motion JPEG",
                        "codec_tag": "jpeg",
                        "fps": 2,
                        "duration_s": 2.0,
                        "sample_count": 4,
                        "width": 960,
                        "height": 540,
                        "physical_duration_s": 1.0,
                        "time_scale_to_physical": 0.5,
                        "presentation": {
                            "mode": "piecewise-linear-display-retiming-v1",
                            "fps": 2,
                            "sample_count": 4,
                            "playback_duration_s": 2.0,
                            "source_physical_duration_s": 1.0,
                            "solver_result_unchanged": True,
                        },
                    }
                },
            }
            valid, detail = results_module._verify_video_artifact(
                result, root, verified_probe=probe
            )
            self.assertTrue(valid, detail)

    def test_box_collider_appearance_is_bounded_and_render_only(self):
        base = {
            "entities": [{"type": "point_mass"}],
            "colliders": [{
                "id": "vessel",
                "type": "box",
                "center": [0, 1, 0],
                "size": [1, 1, 1],
            }],
        }
        defaulted = validate(base)
        self.assertTrue(defaulted["ok"], defaulted)
        self.assertEqual(defaulted["scene"]["colliders"][0]["appearance"], "solid")
        glass = json.loads(json.dumps(base))
        glass["colliders"][0]["appearance"] = "glass"
        accepted = validate(glass)
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual(accepted["scene"]["colliders"][0]["appearance"], "glass")
        invalid = json.loads(json.dumps(base))
        invalid["colliders"][0]["appearance"] = "invisible"
        rejected = validate(invalid)
        self.assertFalse(rejected["ok"], rejected)
        self.assertIn("collider_appearance", {error["code"] for error in rejected["errors"]})

    def test_plane_water_adhesion_is_optional_bounded_and_plane_only(self):
        base = {
            "entities": [{
                "type": "fluid",
                "shape": {"type": "sphere", "center": [0, 1, 0], "radius": .3},
            }],
            "colliders": [{"type": "plane", "normal": [0, 1, 0], "offset": 0}],
        }
        omitted = validate(base)
        self.assertTrue(omitted["ok"], omitted)
        self.assertNotIn("water_adhesion", omitted["scene"]["colliders"][0])

        wettable = json.loads(json.dumps(base))
        wettable["colliders"][0]["water_adhesion"] = 40
        accepted = validate(wettable)
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual(accepted["scene"]["colliders"][0]["water_adhesion"], 40.0)

        excessive = json.loads(json.dumps(base))
        excessive["colliders"][0]["water_adhesion"] = 251
        rejected = validate(excessive)
        self.assertFalse(rejected["ok"], rejected)
        self.assertIn("water_adhesion_range", {error["code"] for error in rejected["errors"]})

        sphere = json.loads(json.dumps(base))
        sphere["colliders"] = [{
            "type": "sphere", "center": [0, 0, 0], "radius": 1,
            "water_adhesion": 10,
        }]
        rejected = validate(sphere)
        self.assertFalse(rejected["ok"], rejected)
        self.assertIn("unknown_field", {error["code"] for error in rejected["errors"]})

    def test_prepare_assumptions_survive_exact_scene_simulation(self):
        prepared = prepare({"entities": [{"type": "point_mass"}]}, 10)
        self.assertTrue(prepared["ok"], prepared)
        self.assertTrue(prepared["assumptions"])
        scene = json.loads(prepared["scene_json"])
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 10, make_video=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["assumptions"], prepared["assumptions"])

    def test_dynamic_rigid_box_is_rejected_instead_of_silently_frozen(self):
        report = validate({
            "entities": [{
                "id": "moving-box",
                "type": "rigid",
                "shape": {"type": "box", "size": [1, 1, 1]},
                "position": [0, 1, 0],
                "velocity": [1, 0, 0],
                "mass": 2,
            }],
        })
        self.assertFalse(report["ok"], report)
        self.assertIn("unsupported_dynamic_box", {error["code"] for error in report["errors"]})
        self.assertEqual(report["scene"]["entities"][0]["mass"], 2.0)

    def test_retryable_simulation_and_quality_failures_require_scene_correction(self):
        for stage in ("simulate", "quality"):
            with self.subTest(stage=stage):
                guided = guide_tool_result("physics_simulate", {
                    "ok": False,
                    "stage": stage,
                    "errors": [{
                        "code": "failed",
                        "path": "entities",
                        "message": "scene requires correction",
                        "retryable": True,
                        "suggestion": "Reduce scene complexity.",
                    }],
                })
                self.assertEqual(guided["next_action"]["tool"], "physics_patch")
                self.assertIn("Do not rerun", guided["next_action"]["preconditions"][1])

    def test_all_examples_validate_and_estimate(self):
        for path in sorted(EXAMPLES.glob("*.json")):
            with self.subTest(path=path.name):
                scene = load_scene(path)
                report = validate(scene)
                self.assertTrue(report["ok"], report)
                plan = estimate(scene, 60)
                self.assertTrue(plan["ok"], plan)
                self.assertLessEqual(plan["plan"]["planned_particles"], plan["plan"]["particle_limit"] + 12)

    @unittest.skipUnless(Draft202012Validator is not None, "install jsonschema to exercise the published machine schema")
    def test_all_examples_pass_published_json_schema(self):
        schema = json.loads((ROOT / "agent" / "scene-v1.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for path in sorted(EXAMPLES.glob("*.json")):
            with self.subTest(path=path.name):
                errors = sorted(
                    validator.iter_errors(load_scene(path)),
                    key=lambda error: tuple(str(part) for part in error.absolute_path),
                )
                self.assertEqual([], errors, "\n".join(error.message for error in errors))

    def test_machine_schema_declares_runtime_scalar_limits(self):
        schema = json.loads((ROOT / "agent" / "scene-v1.schema.json").read_text(encoding="utf-8"))
        definitions = schema["$defs"]
        self.assertEqual(
            definitions["identifier"],
            {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r"\S"},
        )
        self.assertEqual(definitions["positive_shape_extent"]["exclusiveMinimum"], 0)
        self.assertEqual(definitions["positive_shape_extent"]["maximum"], 1000)
        self.assertEqual(definitions["positive_mass"]["maximum"], 1_000_000_000_000)
        self.assertEqual(definitions["mass"]["maximum"], 1_000_000_000_000)
        self.assertEqual(definitions["coordinate"]["minimum"], -10_000)
        self.assertEqual(definitions["coordinate"]["maximum"], 10_000)
        self.assertEqual(definitions["velocity_component"]["minimum"], -500)
        self.assertEqual(definitions["velocity_component"]["maximum"], 500)
        self.assertEqual(definitions["acceleration_component"]["minimum"], -250)
        self.assertEqual(definitions["acceleration_component"]["maximum"], 250)
        plane_schema = definitions["collider"]["oneOf"][0]["properties"]
        self.assertEqual(plane_schema["water_adhesion"]["minimum"], 0)
        self.assertEqual(plane_schema["water_adhesion"]["maximum"], 250)
        self.assertEqual(definitions["fluid"]["properties"]["spacing"]["minimum"], 0.0001)
        self.assertEqual(definitions["granular"]["properties"]["spacing"]["minimum"], 0.0001)
        self.assertEqual(schema["properties"]["entities"]["allOf"][0]["maxContains"], 64)
        interaction_properties = schema["properties"]["interactions"]["properties"]
        self.assertEqual(interaction_properties["gravity_G"]["maximum"], 1_000_000)
        self.assertEqual(interaction_properties["softening"]["maximum"], 100)
        self.assertEqual(interaction_properties["water_sand_drag"]["maximum"], 10)
        self.assertEqual(interaction_properties["wetting_rate"]["maximum"], 100)
        for field_schema in definitions["force_field"]["oneOf"]:
            self.assertEqual(field_schema["properties"]["start_time"]["maximum"], 30)
            self.assertEqual(field_schema["properties"]["end_time"]["maximum"], 30)

    def test_runtime_rejects_values_above_published_scalar_limits(self):
        cases = (
            ("name", "three_body.json", lambda scene: scene.__setitem__("name", "x" * 129)),
            ("entity_id", "three_body.json", lambda scene: scene["entities"][0].__setitem__("id", "x" * 129)),
            ("collider_id", "droplet_ground.json", lambda scene: scene["colliders"][0].__setitem__("id", "x" * 129)),
            ("force_field_id", "geyser.json", lambda scene: scene["force_fields"][0].__setitem__("id", "x" * 129)),
            ("shape_extent", "droplet_ground.json", lambda scene: scene["entities"][0]["shape"].__setitem__("radius", 1001)),
            ("shape_extent", "product_pour_tumbler.json", lambda scene: scene["entities"][0]["shape"].__setitem__("size", [1001, 1, 1])),
            ("mass_range", "three_body.json", lambda scene: scene["entities"][0].__setitem__("mass", 1_000_000_000_001)),
            ("mass_range", "droplet_sphere.json", lambda scene: scene["entities"][1].__setitem__("mass", 1_000_000_000_001)),
            ("coordinate_range", "three_body.json", lambda scene: scene["entities"][0].__setitem__("position", [10001, 0, 0])),
            ("velocity_range", "three_body.json", lambda scene: scene["entities"][0].__setitem__("velocity", [501, 0, 0])),
            ("gravity_range", "three_body.json", lambda scene: scene["world"].__setitem__("gravity", [251, 0, 0])),
            ("force_acceleration", "geyser.json", lambda scene: scene["force_fields"][0].__setitem__("acceleration", [251, 0, 0])),
            ("force_time_window", "geyser.json", lambda scene: scene["force_fields"][0].__setitem__("start_time", 31)),
            ("force_time_window", "geyser.json", lambda scene: scene["force_fields"][0].__setitem__("end_time", 31)),
            (
                "body_limit",
                "three_body.json",
                lambda scene: scene.__setitem__(
                    "entities",
                    [
                        {
                            **scene["entities"][0],
                            "id": f"body-{index}",
                            "position": list(scene["entities"][0]["position"]),
                            "velocity": list(scene["entities"][0]["velocity"]),
                        }
                        for index in range(65)
                    ],
                ),
            ),
            ("interaction_range", "three_body.json", lambda scene: scene["interactions"].__setitem__("gravity_G", 1_000_001)),
            ("interaction_range", "three_body.json", lambda scene: scene["interactions"].__setitem__("softening", 101)),
            ("interaction_range", "sandcastle_wash.json", lambda scene: scene["interactions"].__setitem__("water_sand_drag", 11)),
            ("interaction_range", "sandcastle_wash.json", lambda scene: scene["interactions"].__setitem__("wetting_rate", 101)),
        )
        for expected_code, example_name, mutate in cases:
            with self.subTest(expected_code=expected_code, example=example_name):
                scene = load_scene(EXAMPLES / example_name)
                mutate(scene)
                report = validate(scene)
                self.assertFalse(report["ok"], report)
                self.assertIn(expected_code, {item["code"] for item in report["errors"]})

    @unittest.skipUnless(Draft202012Validator is not None, "install jsonschema to exercise rejected machine-schema inputs")
    def test_machine_schema_rejects_obvious_overlimit_scalars(self):
        schema = json.loads((ROOT / "agent" / "scene-v1.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        cases = (
            ("name", "three_body.json", lambda scene: scene.__setitem__("name", "x" * 129)),
            ("radius", "droplet_ground.json", lambda scene: scene["entities"][0]["shape"].__setitem__("radius", 1001)),
            ("size", "product_pour_tumbler.json", lambda scene: scene["entities"][0]["shape"].__setitem__("size", [1001, 1, 1])),
            ("mass", "three_body.json", lambda scene: scene["entities"][0].__setitem__("mass", 1_000_000_000_001)),
            ("id", "droplet_ground.json", lambda scene: scene["entities"][0].__setitem__("id", "x" * 129)),
            ("start_time", "geyser.json", lambda scene: scene["force_fields"][0].__setitem__("start_time", 31)),
            ("end_time", "geyser.json", lambda scene: scene["force_fields"][0].__setitem__("end_time", 31)),
            (
                "point_mass_count",
                "three_body.json",
                lambda scene: scene.__setitem__(
                    "entities",
                    [
                        {
                            **scene["entities"][0],
                            "id": f"body-{index}",
                            "position": list(scene["entities"][0]["position"]),
                            "velocity": list(scene["entities"][0]["velocity"]),
                        }
                        for index in range(65)
                    ],
                ),
            ),
            ("gravity_G", "three_body.json", lambda scene: scene["interactions"].__setitem__("gravity_G", 1_000_001)),
            ("softening", "three_body.json", lambda scene: scene["interactions"].__setitem__("softening", 101)),
            ("water_sand_drag", "sandcastle_wash.json", lambda scene: scene["interactions"].__setitem__("water_sand_drag", 11)),
            ("wetting_rate", "sandcastle_wash.json", lambda scene: scene["interactions"].__setitem__("wetting_rate", 101)),
        )
        for field, example_name, mutate in cases:
            with self.subTest(field=field):
                scene = load_scene(EXAMPLES / example_name)
                mutate(scene)
                self.assertFalse(validator.is_valid(scene), field)

    def test_bad_scene_returns_all_semantic_errors(self):
        scene = {
            "world": {"duration": -1, "dt": 1},
            "budget": {"quality": "impossible", "wall_time_s": 301},
            "entities": [{"id": "x", "type": "point_mass", "mass": -2}],
        }
        report = validate(scene)
        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["errors"]}
        self.assertTrue({"duration_range", "dt_range", "quality", "budget_range", "mass"} <= codes)

    def test_malformed_nested_types_and_huge_integers_are_structured_errors(self):
        malformed_scenes = []
        for bad_entity in (None, True, "entity", []):
            scene = load_scene(EXAMPLES / "droplet_ground.json")
            scene["entities"][0] = bad_entity
            malformed_scenes.append(scene)
        for bad_kind in ([], {}):
            scene = load_scene(EXAMPLES / "droplet_ground.json")
            scene["entities"][0]["type"] = bad_kind
            malformed_scenes.append(scene)
        scene = load_scene(EXAMPLES / "droplet_ground.json")
        scene["entities"][0]["shape"] = []
        malformed_scenes.append(scene)

        huge = 10**1000
        for field in ("duration", "dt"):
            scene = load_scene(EXAMPLES / "droplet_ground.json")
            scene["world"][field] = huge
            malformed_scenes.append(scene)
        scene = load_scene(EXAMPLES / "droplet_ground.json")
        scene["world"]["gravity"] = [huge, 0, 0]
        malformed_scenes.append(scene)
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["entities"][0]["mass"] = huge
        malformed_scenes.append(scene)
        scene = load_scene(EXAMPLES / "three_body.json")
        scene.setdefault("interactions", {})["gravity_G"] = huge
        malformed_scenes.append(scene)

        for scene in malformed_scenes:
            report = validate(scene)
            self.assertFalse(report["ok"], report)
            response = call_tool("physics_validate", {"scene_json": json.dumps(scene)})
            self.assertFalse(response["ok"], response)
            self.assertIn(response["stage"], {"validate", "arguments"})

        scene = load_scene(EXAMPLES / "three_body.json")
        estimate_result = estimate(scene, huge)
        self.assertFalse(estimate_result["ok"], estimate_result)
        self.assertEqual(estimate_result["stage"], "arguments")

    def test_unknown_fields_are_rejected(self):
        scene = load_scene(EXAMPLES / "droplet_ground.json")
        scene["world"]["magic"] = 42
        report = validate(scene)
        self.assertFalse(report["ok"])
        self.assertIn("unknown_field", {item["code"] for item in report["errors"]})

    def test_output_fps_requires_an_integer_value(self):
        for value in (24, 24.0, 120):
            with self.subTest(value=value):
                scene = load_scene(EXAMPLES / "three_body.json")
                scene["world"]["output_fps"] = value
                self.assertTrue(validate(scene)["ok"])

        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["output_fps"] = 1.49
        report = validate(scene)
        self.assertFalse(report["ok"])
        self.assertIn("fps_integer", {item["code"] for item in report["errors"]})

        scene["world"]["output_fps"] = 121
        report = validate(scene)
        self.assertFalse(report["ok"])
        self.assertIn("fps_range", {item["code"] for item in report["errors"]})

    def test_millimetre_scale_particle_spacing_is_supported(self):
        scene = load_scene(EXAMPLES / "droplet_ground.json")
        scene["world"].update({
            "duration": 0.12,
            "dt": 0.001,
            "output_fps": 60,
            "bounds": {"min": [-0.03, 0.0, -0.03], "max": [0.03, 0.025, 0.03]},
        })
        drop = scene["entities"][0]
        drop["shape"].update({"center": [0.0, 0.015, 0.0], "radius": 0.003})
        drop["spacing"] = 0.0004
        report = validate(scene)
        self.assertTrue(report["ok"], report)
        plan = estimate(scene, 55)
        self.assertTrue(plan["ok"], plan)
        self.assertGreater(plan["plan"]["planned_particles"], 1_000)
        self.assertLess(plan["plan"]["planned_particles"], 4_000)

    def test_present_object_fields_with_wrong_types_are_rejected(self):
        mutations = (
            ("bounds_type", lambda scene: scene["world"].__setitem__("bounds", 42)),
            ("budget_type", lambda scene: scene.__setitem__("budget", [])),
            ("interactions_type", lambda scene: scene.__setitem__("interactions", "bad")),
        )
        for expected_code, mutate in mutations:
            with self.subTest(expected_code=expected_code):
                scene = load_scene(EXAMPLES / "droplet_ground.json")
                mutate(scene)
                report = validate(scene)
                self.assertFalse(report["ok"], report)
                self.assertIn(expected_code, {item["code"] for item in report["errors"]})

    def test_particle_shape_type_and_fields_match_machine_schema(self):
        scene = load_scene(EXAMPLES / "droplet_ground.json")
        scene["entities"][0]["shape"] = {
            "type": "box",
            "center": [0, 1, 0],
            "size": "oops",
        }
        report = validate(scene)
        self.assertFalse(report["ok"], report)
        self.assertIn("vec3", {item["code"] for item in report["errors"]})

        scene = load_scene(EXAMPLES / "droplet_ground.json")
        scene["entities"][0]["shape"]["size"] = [1, 1, 1]
        report = validate(scene)
        self.assertFalse(report["ok"], report)
        self.assertIn("unknown_field", {item["code"] for item in report["errors"]})

    def test_patch_is_atomic_and_preserves_input(self):
        scene = load_scene(EXAMPLES / "droplet_ground.json")
        original = json.loads(json.dumps(scene))
        changed = patch_scene(scene, [{"op": "replace", "path": "/entities/0/shape/center/1", "value": 2.0}])
        self.assertTrue(changed["ok"], changed)
        self.assertEqual(changed["scene"]["entities"][0]["shape"]["center"][1], 2.0)
        self.assertEqual(scene, original)

        nbody = load_scene(EXAMPLES / "three_body.json")
        inserted = {
            "id": "inserted",
            "type": "point_mass",
            "mass": 1,
            "position": [0, 1, 0],
            "velocity": [0, 0, 0],
        }
        added_front = patch_scene(nbody, [{"op": "add", "path": "/entities/0", "value": inserted}])
        self.assertTrue(added_front["ok"], added_front)
        self.assertEqual(len(added_front["scene"]["entities"]), 4)
        self.assertEqual(added_front["scene"]["entities"][0]["id"], "inserted")
        self.assertEqual(added_front["scene"]["entities"][1]["id"], "body-a")
        inserted["id"] = "appended"
        added_end = patch_scene(nbody, [{"op": "add", "path": "/entities/3", "value": inserted}])
        self.assertTrue(added_end["ok"], added_end)
        self.assertEqual(len(added_end["scene"]["entities"]), 4)
        self.assertEqual(added_end["scene"]["entities"][-1]["id"], "appended")
        failed = patch_scene(scene, [{"op": "replace", "path": "/missing/value", "value": 1}])
        self.assertFalse(failed["ok"])
        self.assertEqual(failed["stage"], "patch")
        self.assertEqual(scene, original)

        invalid = call_tool("physics_patch", {
            "scene_json": json.dumps(scene),
            "operations": [{"op": "replace", "path": "/entities/@drop/shape/radius", "value_json": "-1"}],
        })
        self.assertFalse(invalid["ok"], invalid)
        self.assertEqual(invalid["status"], "needs_scene_fix")
        self.assertEqual(invalid["next_action"]["tool"], "physics_patch")
        self.assertTrue(invalid["errors"])

        invalid_scene = json.loads(json.dumps(scene))
        invalid_scene["entities"][0]["shape"]["radius"] = -1
        validation = call_tool("physics_validate", {"scene_json": json.dumps(invalid_scene)})
        self.assertEqual(validation["status"], "needs_scene_fix")
        self.assertEqual(validation["next_action"]["tool"], "physics_patch")

    def test_agent_tools_are_strict_and_patch_value_json(self):
        definitions = json.loads((ROOT / "agent" / "tools.json").read_text(encoding="utf-8"))
        self.assertEqual(len(definitions), 12)
        self.assertEqual(
            {tool["name"] for tool in definitions},
            {
                "physics_capabilities",
                "physics_liquid",
                "physics_system",
                "physics_example",
                "physics_mesh",
                "physics_validate",
                "physics_estimate",
                "physics_prepare",
                "physics_simulate",
                "physics_inspect",
                "physics_query",
                "physics_patch",
            },
        )
        self.assertTrue(all(tool["strict"] for tool in definitions))
        self.assertTrue(all(tool["input_schema"]["additionalProperties"] is False for tool in definitions))
        by_name = {tool["name"]: tool for tool in definitions}
        self.assertEqual(by_name["physics_patch"]["input_schema"]["properties"]["operations"]["maxItems"], 64)
        self.assertEqual(by_name["physics_inspect"]["input_schema"]["properties"]["result_path"]["maxLength"], 4096)
        scene = load_scene(EXAMPLES / "droplet_ground.json")
        response = call_tool("physics_patch", {
            "scene_json": json.dumps(scene),
            "operations": [{"op": "replace", "path": "/entities/@drop/shape/center/1", "value_json": "2.0"}],
        })
        self.assertTrue(response["ok"], response)
        self.assertEqual(response["scene"]["entities"][0]["shape"]["center"][1], 2.0)

    def test_guided_capabilities_example_prepare_protocol(self):
        capabilities = call_tool("physics_capabilities", {})
        self.assertTrue(capabilities["ok"], capabilities)
        self.assertEqual(capabilities["protocol_version"], "agent-physics/1.0")
        self.assertEqual(capabilities["status"], "capabilities_ready")
        self.assertEqual(capabilities["next_action"]["kind"], "choose")
        self.assertEqual(
            {option["tool"] for option in capabilities["next_action"]["options"]},
            {"physics_system", "physics_example", "physics_liquid", "physics_prepare"},
        )

        example = call_tool("physics_example", {"name": "droplet_ground"})
        liquid = call_tool("physics_liquid", {"preset": "honey", "entity_id": "drop"})
        self.assertTrue(liquid["ok"], liquid)
        self.assertEqual(liquid["status"], "liquid_preset_ready")
        self.assertEqual(liquid["next_action"]["kind"], "choose")
        self.assertIn(
            "physics_patch",
            {option["tool"] for option in liquid["next_action"]["options"]},
        )
        patched = call_tool("physics_patch", {
            "scene_json": example["scene_json"],
            "operations": liquid["patch_arguments"]["operations"],
        })
        self.assertTrue(patched["ok"], patched)
        self.assertEqual(patched["scene"]["entities"][0]["preset"], "honey")
        self.assertEqual(patched["scene"]["entities"][0]["properties"], liquid["preset"]["properties"])

        self.assertTrue(example["ok"], example)
        self.assertEqual(example["protocol_version"], capabilities["protocol_version"])
        self.assertEqual(example["status"], "example_loaded")
        self.assertEqual(example["next_action"]["kind"], "choose")
        self.assertEqual(
            {option["tool"] for option in example["next_action"]["options"]},
            {"physics_liquid", "physics_patch", "physics_prepare"},
        )

        prepared = call_tool("physics_prepare", {"scene_json": example["scene_json"]})
        self.assertTrue(prepared["ok"], prepared)
        self.assertTrue(prepared["ready_to_simulate"], prepared)
        self.assertEqual(prepared["protocol_version"], capabilities["protocol_version"])
        self.assertEqual(prepared["status"], "ready_to_simulate")
        self.assertEqual(prepared["next_action"]["kind"], "call_tool")
        self.assertEqual(prepared["next_action"]["tool"], "physics_simulate")
        self.assertEqual(json.loads(prepared["scene_json"]), prepared["scene"])

        customer_example = call_tool("physics_example", {"name": "flood_bridge_piers"})
        self.assertTrue(customer_example["ok"], customer_example)
        self.assertTrue(customer_example["customer_prompts"])
        self.assertTrue(customer_example["inherited_assumptions"])
        self.assertIn("loads", customer_example["physics_boundary"])
        self.assertLess(
            customer_example["reference_prepare"]["p90_s"],
            customer_example["reference_prepare"]["budget_s"],
        )

    def test_tool_boundary_rejects_nonstandard_and_ambiguous_json(self):
        invalid_documents = {
            "non-finite number": '{"version":1,"value":Infinity}',
            "duplicate key": '{"version":1,"version":1}',
            "lone UTF-16 surrogate": '{"version":1,"name":"\\ud800"}',
        }
        for label, document in invalid_documents.items():
            with self.subTest(label=label):
                response = call_tool("physics_validate", {"scene_json": document})
                self.assertFalse(response["ok"], response)
                self.assertEqual(response["stage"], "arguments")
                self.assertEqual(response["status"], "input_rejected")
                self.assertEqual(response["errors"][0]["code"], "ambiguous_json")
                self.assertFalse(response["errors"][0]["retryable"])
                self.assertEqual(response["next_action"]["kind"], "respond_to_user")

        scene = load_scene(EXAMPLES / "three_body.json")
        scene["name"] = "\ud800"
        direct = validate(scene)
        self.assertFalse(direct["ok"], direct)
        self.assertEqual(direct["errors"][0]["code"], "unicode_scalar")

    def test_catalog_common_patch_paths_resolve_against_their_examples(self):
        capabilities = call_tool("physics_capabilities", {})
        self.assertTrue(capabilities["ok"], capabilities)

        def resolve_pointer(document, pointer):
            value = document
            for encoded in pointer.removeprefix("/").split("/"):
                token = encoded.replace("~1", "/").replace("~0", "~")
                if isinstance(value, list):
                    if token.startswith("@"):
                        identifier = token[1:]
                        matches = [item for item in value if isinstance(item, dict) and item.get("id") == identifier]
                        self.assertEqual(len(matches), 1, f"{pointer}: expected exactly one item with id {identifier!r}")
                        value = matches[0]
                    else:
                        value = value[int(token)]
                else:
                    value = value[token]
            return value

        for catalog_item in capabilities["examples"]:
            example = call_tool("physics_example", {"name": catalog_item["name"]})
            self.assertTrue(example["ok"], example)
            for pointer in example["common_patches"]:
                with self.subTest(example=catalog_item["name"], pointer=pointer):
                    current = resolve_pointer(example["scene"], pointer)
                    patched = call_tool("physics_patch", {
                        "scene_json": example["scene_json"],
                        "operations": [{"op": "replace", "path": pointer, "value_json": json.dumps(current)}],
                    })
                    self.assertTrue(patched["ok"], patched)

    def test_timing_estimate_and_frame_sample_guard_are_explicit(self):
        scene = load_scene(EXAMPLES / "high_detail_droplet_sphere.json")
        scene["world"]["duration"] = 30
        scene["world"]["output_fps"] = 60
        result = estimate(scene, 60)
        self.assertTrue(result["ok"], result)
        plan = result["plan"]
        self.assertLessEqual(plan["frame_particle_samples"], 600_000)
        self.assertGreater(plan["timing_estimate"]["total_p90_s"], 0)
        self.assertEqual(plan["timing_estimate"]["hard_limit_s"], 60)
        self.assertIn("fits_budget", plan["timing_estimate"])
        self.assertGreater(plan["estimated_peak_memory_mb"], 0)

    def test_planner_rejects_bounds_smaller_than_coarsened_particle(self):
        scene = load_scene(EXAMPLES / "droplet_ground.json")
        scene["entities"][0]["shape"] = {"type": "box", "center": [0, 0, 0], "size": [1, 1, 1]}
        scene["entities"][0]["spacing"] = 0.02
        scene["world"]["bounds"] = {"min": [-0.015, -0.015, -0.015], "max": [0.015, 0.015, 0.015]}
        report = estimate(scene, 10)
        self.assertFalse(report["ok"], report)
        self.assertEqual(report["stage"], "estimate")
        self.assertEqual(report["errors"][0]["code"], "infeasible_bounds")
        self.assertFalse(report["plan"]["bounds_feasible"])
        with tempfile.TemporaryDirectory() as directory:
            simulated = simulate(scene, directory, 10, make_video=False)
        self.assertFalse(simulated["ok"], simulated)
        self.assertEqual(simulated["errors"][0]["code"], "infeasible_bounds")

    def test_planner_rejects_bounds_smaller_than_dynamic_rigid_sphere(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["entities"] = [{
            "id": "ball",
            "type": "rigid",
            "shape": {"type": "sphere", "radius": 0.5},
            "position": [0, 0, 0],
            "velocity": [0, 0, 0],
            "mass": 1,
        }]
        scene["world"]["bounds"] = {"min": [-0.001, -0.001, -0.001], "max": [0.001, 0.001, 0.001]}
        report = estimate(scene, 10)
        self.assertFalse(report["ok"], report)
        self.assertEqual(report["errors"][0]["code"], "infeasible_bounds")
        self.assertAlmostEqual(report["plan"]["minimum_particle_bounds_span_m"], 1.0)

    def test_planner_rejects_initial_geometry_outside_world_bounds(self):
        scene = load_scene(EXAMPLES / "droplet_ground.json")
        scene["entities"][0]["shape"]["center"][1] = 3.1
        report = estimate(scene, 20)
        self.assertFalse(report["ok"], report)
        self.assertEqual(report["errors"][0]["code"], "initial_state_outside_bounds")
        self.assertFalse(report["plan"]["initial_bounds_feasible"])
        self.assertIn("drop", report["plan"]["initial_bounds_violations"][0])

    def test_excessive_initial_particle_volume_overlap_is_rejected(self):
        scene = load_scene(EXAMPLES / "droplet_ground.json")
        template = scene["entities"][0]
        scene["entities"] = [
            json.loads(json.dumps(template)) | {"id": f"drop-{index}"}
            for index in range(9)
        ]
        started = time.monotonic()
        report = validate(scene)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(report["ok"], report)
        self.assertIn("initial_particle_overlap_limit", {error["code"] for error in report["errors"]})

    def test_close_nbody_scene_requires_a_smaller_quantitative_timestep(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"].update({"duration": 1.0, "dt": 0.05})
        scene["entities"] = scene["entities"][:2]
        scene["entities"][0].update({"position": [-0.01, 0, 0], "velocity": [0, 0, 0]})
        scene["entities"][1].update({"position": [0.01, 0, 0], "velocity": [0, 0, 0]})
        report = estimate(scene, 10)
        self.assertFalse(report["ok"], report)
        self.assertEqual(report["errors"][0]["code"], "unstable_nbody_timestep")
        self.assertFalse(report["agent_report"]["nbody_timestep_feasible"])

    def test_large_direction_vectors_normalize_and_plane_offsets_are_bounded(self):
        direction = unit([1e308, 1e308, 0.0])
        self.assertAlmostEqual(math.dist(direction, [0, 0, 0]), 1.0, places=12)
        self.assertGreater(direction[0], 0.7)

        scene = load_scene(EXAMPLES / "droplet_ground.json")
        scene["colliders"][0]["normal"] = [1e308, 1e308, 0]
        self.assertTrue(validate(scene)["ok"])
        scene["colliders"][0]["offset"] = 1e308
        report = validate(scene)
        self.assertFalse(report["ok"], report)
        self.assertIn("coordinate_range", {error["code"] for error in report["errors"]})

    def test_package_versions_have_one_value(self):
        metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{__version__}"', metadata)

    def test_extreme_geometry_and_wrong_scalar_types_are_rejected_quickly(self):
        scene = load_scene(EXAMPLES / "droplet_ground.json")
        scene["entities"][0]["shape"]["radius"] = 1e308
        started = time.monotonic()
        report = validate(scene)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertFalse(report["ok"])
        self.assertIn("shape_extent", {item["code"] for item in report["errors"]})

        scene = load_scene(EXAMPLES / "droplet_ground.json")
        scene["world"]["duration"] = "forever"
        scene["budget"]["wall_time_s"] = True
        report = validate(scene)
        self.assertFalse(report["ok"])
        self.assertIn("finite_number", {item["code"] for item in report["errors"]})

    def test_force_field_limits_and_targets_are_validated(self):
        scene = load_scene(EXAMPLES / "geyser.json")
        scene["force_fields"][0]["targets"] = ["missing"]
        report = validate(scene)
        self.assertFalse(report["ok"])
        self.assertIn("unknown_force_target", {item["code"] for item in report["errors"]})

        scene = load_scene(EXAMPLES / "geyser.json")
        base = scene["force_fields"][0]
        scene["force_fields"] = [json.loads(json.dumps(base)) | {"id": f"field-{index}"} for index in range(9)]
        report = validate(scene)
        self.assertFalse(report["ok"])
        codes = {item["code"] for item in report["errors"]}
        self.assertIn("force_field_limit", codes)

    def test_non_dedicated_output_directory_is_refused(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.02
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "unrelated.txt").write_text("keep", encoding="utf-8")
            result = simulate(scene, directory, 10, make_video=False)
            self.assertFalse(result["ok"])
            self.assertEqual(result["errors"][0]["code"], "unsafe_output_directory")

    def test_artifact_write_failure_is_a_structured_output_error(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.02
        with tempfile.TemporaryDirectory() as directory, patch(
            "physics_demo.runner._write_json",
            side_effect=PermissionError("forced read-only output"),
        ):
            result = simulate(scene, directory, 10, make_video=False)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["stage"], "output")
        self.assertEqual(result["errors"][0]["code"], "output_write_failed")

    def test_inspect_rejects_overlong_path_without_raising(self):
        result = call_tool("physics_inspect", {"result_path": "/" + "x" * 10_000})
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["stage"], "inspect")
        self.assertEqual(result["status"], "inspection_failed")

    def test_output_artifact_symlink_is_rejected_without_touching_target(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.02
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent)
            output = root / "run"
            output.mkdir()
            target = root / "do-not-overwrite.txt"
            target.write_text("sentinel", encoding="utf-8")
            (output / "result.json").symlink_to(target)
            result = simulate(scene, output, 10, make_video=False)
            self.assertFalse(result["ok"])
            self.assertEqual(result["stage"], "output")
            self.assertEqual(result["errors"][0]["code"], "unsafe_output_directory")
            self.assertEqual(target.read_text(encoding="utf-8"), "sentinel")

    def test_inspect_rejects_missing_video_artifact(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.02
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 20, make_video=True)
            self.assertTrue(result["ok"], result)
            result_path = Path(directory) / "result.json"
            Path(result["artifacts"]["video"]["path"]).unlink()
            inspected = inspect(result_path)
            self.assertFalse(inspected["ok"], inspected)
            self.assertTrue(inspected["solver_ok"])
            self.assertEqual(inspected["stage"], "quality")
            self.assertEqual(inspected["errors"][0]["code"], "quality_gate_failed")
            self.assertFalse(inspected["quality_gate"]["video_present"])
            self.assertIn("verified_video_artifact", inspected["quality_gate"]["failed_checks"])

            guided_result = call_tool("physics_inspect", {"result_path": str(result_path)})
            self.assertEqual(guided_result["status"], "failed_quality_gate")
            self.assertEqual(guided_result["next_action"]["kind"], "respond_to_user")

    @unittest.skipUnless(shutil.which("clang"), "macOS clang/AVFoundation is required")
    def test_inspect_rejects_forged_ftyp_file_without_video_track(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.02
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 20, make_video=True)
            self.assertTrue(result["ok"], result)
            video_path = Path(directory) / "simulation.mp4"
            video_path.write_bytes(
                b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isommp41" + b"X" * 2000
            )
            result_path = Path(directory) / "result.json"
            document = json.loads(result_path.read_text(encoding="utf-8"))
            document["artifacts"]["video"]["bytes"] = video_path.stat().st_size
            result_path.write_text(json.dumps(document), encoding="utf-8")
            inspected = inspect(result_path)
        self.assertFalse(inspected["ok"], inspected)
        self.assertFalse(inspected["quality_gate"]["video_present"])
        self.assertIn("video track", inspected["quality_gate"]["video_verification"])

    def test_sandbox_blocked_h264_pixel_decode_is_not_accepted(self):
        blocked = subprocess.CompletedProcess(
            args=["probe"],
            returncode=77,
            stdout="960x540 1.25 avc1 30\n",
            stderr="Cannot Decode\n",
        )
        with patch.object(
            video_module, "_compile_renderer", return_value=Path("/tmp/fake-probe")
        ), patch.object(video_module.subprocess, "run", return_value=blocked):
            with self.assertRaisesRegex(VideoEncodingError, "pixel-decode"):
                video_module._validate_first_frame(
                    "clang", Path("probe.m"), Path("video.mp4"), 5
                )

    def test_video_compiler_cache_key_includes_framework_recipe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "renderer.m"
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            commands = []

            def fake_compile(command, **kwargs):
                del kwargs
                commands.append(command)
                Path(command[-1]).touch()
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(
                video_module.tempfile, "gettempdir", return_value=directory
            ), patch.object(video_module.subprocess, "run", side_effect=fake_compile):
                foundation_only = video_module._compile_renderer(
                    "clang",
                    sources=[source],
                    frameworks=("Foundation",),
                    cache_name="recipe-test",
                    timeout_seconds=5,
                )
                with_imageio = video_module._compile_renderer(
                    "clang",
                    sources=[source],
                    frameworks=("Foundation", "ImageIO"),
                    cache_name="recipe-test",
                    timeout_seconds=5,
                )
        self.assertNotEqual(foundation_only, with_imageio)
        self.assertEqual(len(commands), 2)
        self.assertIn("ImageIO", commands[1])

    def test_public_inspect_does_not_offer_debug_no_video_result_as_delivery(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.02
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 10, make_video=False)
            self.assertTrue(result["ok"], result)
            inspected = call_tool("physics_inspect", {"result_path": str(Path(directory) / "result.json")})
        self.assertTrue(inspected["ok"], inspected)
        self.assertEqual(inspected["status"], "debug_result_no_video")
        self.assertEqual(inspected["next_action"]["tool"], "physics_simulate")


class SolverTests(unittest.TestCase):
    def test_wetting_rate_is_not_multiplied_by_neighbor_count(self):
        sand = Particle([0, 0, 0], [0, 0, 0], "sand", "sand", 0.1)
        water_a = Particle([0.05, 0, 0], [0, 0, 0], "water", "water", 0.1)
        water_b = Particle([-0.05, 0, 0], [0, 0, 0], "water", "water", 0.1)
        particles = [sand, water_a, water_b]
        _project_particle_contacts(particles, [[1, 2], [0], [0]], wetting_rate=2.0, dt=0.1)
        self.assertAlmostEqual(sand.wetness, 0.2)

    def test_sand_cohesion_relaxation_is_timestep_consistent(self):
        base_scene = {
            "version": 1,
            "name": "sand-cohesion-dt-regression",
            "world": {
                "gravity": [0, 0, 0],
                "duration": 0.1,
                "dt": 0.01,
                "output_fps": 10,
                "bounds": {"min": [-2, -2, -2], "max": [2, 2, 2]},
            },
            "budget": {"wall_time_s": 60, "quality": "preview", "backend": "native"},
            "entities": [{
                "id": "sand",
                "type": "granular",
                "shape": {"type": "box", "center": [0, 0, 0], "size": [0.1, 0.1, 0.1]},
                "spacing": 0.2,
                "velocity": [1, 0, 0],
                "properties": {"friction": 0, "cohesion": 0.2},
            }],
            "colliders": [],
            "interactions": {},
        }
        for backend in ("native", "python"):
            displacements = []
            for dt in (0.01, 0.005):
                scene = json.loads(json.dumps(base_scene))
                scene["world"]["dt"] = dt
                scene["budget"]["backend"] = backend
                with tempfile.TemporaryDirectory() as directory:
                    result = simulate(scene, directory, 60, make_video=False)
                self.assertTrue(result["ok"], result)
                displacements.append(result["diagnostics"]["mean_sand_displacement_m"])
            relative_difference = abs(displacements[0] - displacements[1]) / max(displacements)
            self.assertLess(relative_difference, 0.12, (backend, displacements))

    def test_spatial_hash_matches_brute_force(self):
        points = [[0, 0, 0], [0.4, 0, 0], [1.1, 0, 0], [0.2, 0.7, 0], [-0.5, 0, 0]]
        particles = [Particle(list(point), [0, 0, 0], "water", "g", 0.1) for point in points]
        h = 0.8
        actual = build_neighbors(particles, h)
        expected = []
        for i, point in enumerate(points):
            expected.append([
                j for j, other in enumerate(points)
                if i != j and sum((point[k] - other[k]) ** 2 for k in range(3)) < h * h
            ])
        self.assertEqual([sorted(row) for row in actual], [sorted(row) for row in expected])

    def test_plane_projection_removes_penetration(self):
        point = [0.0, -0.25, 0.0]
        correction = project_colliders(point, 0.1, [{"type": "plane", "normal": [0, 1, 0], "offset": 0}])
        self.assertAlmostEqual(point[1], 0.1)
        self.assertAlmostEqual(correction, 0.35)

    def test_plane_projection_applies_bounded_positional_friction(self):
        point = [1.0, -0.1, 0.0]
        correction = project_colliders(
            point,
            0.1,
            [{"type": "plane", "normal": [0, 1, 0], "offset": 0, "friction": 0.5}],
            [0.0, 0.1, 0.0],
        )
        self.assertAlmostEqual(correction, 0.2)
        self.assertAlmostEqual(point[0], 0.9)
        self.assertAlmostEqual(point[1], 0.1)

    def test_swept_box_projection_prevents_thin_wall_tunnelling(self):
        point = [1.0, 0.0, 0.0]
        correction = project_colliders(
            point,
            0.1,
            [{"type": "box", "center": [0, 0, 0], "size": [0.1, 2, 2], "friction": 0}],
            [-1.0, 0.0, 0.0],
        )
        self.assertGreater(correction, 1.0)
        self.assertAlmostEqual(point[0], -0.15, places=8)

        on_face = [2.0, 0.0, 0.0]
        correction = project_colliders(
            on_face,
            0.1,
            [{"type": "box", "center": [0, 0, 0], "size": [1, 2, 2], "friction": 0}],
            [-0.6, 0.0, 0.0],
        )
        self.assertGreater(correction, 2.5)
        self.assertAlmostEqual(on_face[0], -0.6, places=8)

        moving_outward = [-1.0, 0.0, 0.0]
        correction = project_colliders(
            moving_outward,
            0.1,
            [{"type": "box", "center": [0, 0, 0], "size": [1, 2, 2], "friction": 0}],
            [-0.6, 0.0, 0.0],
        )
        self.assertEqual(correction, 0.0)
        self.assertEqual(moving_outward, [-1.0, 0.0, 0.0])

        just_inside = [2.0, 0.0, 0.0]
        correction = project_colliders(
            just_inside,
            0.1,
            [{"type": "box", "center": [0, 0, 0], "size": [1, 2, 2], "friction": 0}],
            [-0.599999, 0.0, 0.0],
        )
        self.assertGreater(correction, 2.5)
        self.assertLess(just_inside[0], -0.599999)

    def test_exact_overlap_particle_contacts_separate_by_one_diameter(self):
        sand_a = Particle([0, 0, 0], [0, 0, 0], "sand", "a", 0.1)
        sand_b = Particle([0, 0, 0], [0, 0, 0], "sand", "b", 0.1)
        _project_particle_contacts([sand_a, sand_b], [[1], [0]], wetting_rate=0.0, dt=0.01)
        self.assertAlmostEqual(math.dist(sand_a.pos, sand_b.pos), 0.2, places=12)

        scene = {
            "version": 1,
            "name": "native-coincident-particles",
            "world": {
                "gravity": [0, 0, 0], "duration": 0.01, "dt": 0.01, "output_fps": 60,
                "bounds": {"min": [-1, -1, -1], "max": [1, 1, 1]},
            },
            "budget": {"wall_time_s": 10, "quality": "preview", "backend": "native"},
            "entities": [
                {"id": "a", "type": "granular", "shape": {"type": "box", "center": [0, 0, 0], "size": [0.1, 0.1, 0.1]}, "spacing": 0.2, "velocity": [0, 0, 0], "properties": {"friction": 0, "cohesion": 0}},
                {"id": "b", "type": "granular", "shape": {"type": "box", "center": [0, 0, 0], "size": [0.1, 0.1, 0.1]}, "spacing": 0.2, "velocity": [0, 0, 0], "properties": {"friction": 0, "cohesion": 0}},
            ],
            "colliders": [],
            "interactions": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 10, make_video=False)
            full = json.loads((Path(directory) / "result.json").read_text(encoding="utf-8"))
        self.assertTrue(result["ok"], result)
        positions = full["trajectory"]["frames"][-1]["p"]
        self.assertAlmostEqual(math.dist(positions[0], positions[1]), 0.184, places=5)
        self.assertGreaterEqual(result["diagnostics"]["max_particle_contact_correction_m"], 0.0)

    def test_native_sphere_exact_center_projection_matches_reference(self):
        reference = [0.0, 0.0, 0.0]
        correction = project_colliders(reference, 0.1, [{"type": "sphere", "center": [0, 0, 0], "radius": 0.2}])
        self.assertAlmostEqual(correction, 0.3)
        self.assertAlmostEqual(math.dist(reference, [0, 0, 0]), 0.3)

        scene = {
            "version": 1,
            "name": "native-sphere-center-degeneracy",
            "world": {
                "gravity": [0, 0, 0], "duration": 0.01, "dt": 0.01, "output_fps": 60,
                "bounds": {"min": [-1, -1, -1], "max": [1, 1, 1]},
            },
            "budget": {"wall_time_s": 10, "quality": "preview", "backend": "native"},
            "entities": [{
                "id": "sand", "type": "granular",
                "shape": {"type": "box", "center": [0, 0, 0], "size": [0.1, 0.1, 0.1]},
                "spacing": 0.2, "velocity": [0, 0, 0],
                "properties": {"friction": 0, "cohesion": 0},
            }],
            "colliders": [{"id": "sphere", "type": "sphere", "center": [0, 0, 0], "radius": 0.2, "friction": 0}],
            "interactions": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 10, make_video=False)
            full = json.loads((Path(directory) / "result.json").read_text(encoding="utf-8"))
        self.assertTrue(result["ok"], result)
        final = full["trajectory"]["frames"][-1]["p"][0]
        expected = 0.2 + full["trajectory"]["particle_radius"]
        self.assertAlmostEqual(math.dist(final, [0, 0, 0]), expected, places=5)
        self.assertGreaterEqual(result["diagnostics"]["max_projection_correction_m"], 0.0)

    def test_native_swept_box_contact_stops_fast_particle(self):
        scene = {
            "version": 1,
            "name": "fast-particle-thin-wall",
            "world": {
                "gravity": [0, 0, 0],
                "duration": 0.01,
                "dt": 0.01,
                "output_fps": 60,
                "bounds": {"min": [-2, -1, -1], "max": [2, 1, 1]},
            },
            "budget": {"wall_time_s": 10, "quality": "preview", "backend": "native"},
            "entities": [{
                "id": "fast-water",
                "type": "fluid",
                "shape": {"type": "box", "center": [-0.102, 0, 0], "size": [0.1, 0.1, 0.1]},
                "spacing": 0.2,
                "velocity": [200, 0, 0],
                "properties": {"viscosity": 0, "surface_tension": 0},
            }],
            "colliders": [{
                "id": "thin-wall",
                "type": "box",
                "center": [0, 0, 0],
                "size": [0.02, 2, 2],
                "friction": 0,
            }],
            "interactions": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 10, make_video=False)
            full = json.loads((Path(directory) / "result.json").read_text(encoding="utf-8"))
        self.assertTrue(result["ok"], result)
        self.assertLess(full["trajectory"]["frames"][-1]["p"][0][0], -0.1)

    def test_capsule_projection_removes_penetration(self):
        point = [0.0, 0.5, 0.0]
        correction = project_colliders(point, 0.1, [{"type": "capsule", "a": [0, 0, 0], "b": [0, 1, 0], "radius": 0.2}])
        self.assertAlmostEqual(correction, 0.3)
        self.assertAlmostEqual(point[0], 0.3)

    def test_native_capsule_axis_projection_uses_deterministic_normal(self):
        spacing = 0.2
        collider_radius = 0.2
        expected_x = collider_radius + 0.46 * spacing
        scene = {
            "version": 1,
            "name": "capsule-axis-degeneracy",
            "world": {
                "gravity": [0, 0, 0],
                "duration": 0.01,
                "dt": 0.01,
                "output_fps": 60,
                "bounds": {"min": [-1, -1, -1], "max": [1, 2, 1]},
            },
            "budget": {"wall_time_s": 10, "quality": "preview", "backend": "native"},
            "entities": [{
                "id": "one-water-particle",
                "type": "fluid",
                "shape": {"type": "box", "center": [0, 0.5, 0], "size": [0.1, 0.1, 0.1]},
                "spacing": spacing,
                "velocity": [0, 0, 0],
                "properties": {"viscosity": 0, "surface_tension": 0},
            }],
            "colliders": [{
                "id": "post",
                "type": "capsule",
                "a": [0, 0, 0],
                "b": [0, 1, 0],
                "radius": collider_radius,
                "friction": 0,
            }],
            "interactions": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 10, make_video=False)
            full = json.loads((Path(directory) / "result.json").read_text(encoding="utf-8"))

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["diagnostics"]["backend"], "native-c11-dfsph")
        self.assertEqual(result["diagnostics"]["particle_count"], 1)
        final_position = full["trajectory"]["frames"][-1]["p"][0]
        self.assertAlmostEqual(final_position[0], expected_x, places=5)
        self.assertAlmostEqual(final_position[1], 0.5, places=5)
        self.assertAlmostEqual(final_position[2], 0.0, places=5)
        self.assertAlmostEqual(result["diagnostics"]["max_projection_correction_m"], expected_x, places=12)

    def test_figure_eight_conserves_energy_and_returns_near_start(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 20, make_video=False)
            self.assertTrue(result["ok"], result)
            drift = result["diagnostics"]["nbody_relative_energy_drift"]
            self.assertLess(abs(drift), 2e-3)
            self.assertLess(result["diagnostics"]["nbody_momentum_drift"], 1e-9)

    def test_fixed_point_mass_disables_closed_system_invariant_claim(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.1
        scene["entities"][0]["fixed"] = True
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 10, make_video=False)
        self.assertTrue(result["ok"], result)
        diagnostics = result["diagnostics"]
        self.assertFalse(diagnostics["nbody_invariants_conserved"])
        self.assertFalse(diagnostics["nbody_invariants_applicable"])
        self.assertEqual(diagnostics["nbody_invariant_exclusion_reason"], "fixed_body_breaks_closed_system_momentum")
        self.assertIsNone(diagnostics["nbody_relative_energy_drift"])
        self.assertIsNone(diagnostics["nbody_momentum_drift"])

    def test_particle_scenes_complete_under_budget_without_video(self):
        names = ["droplet_ground.json", "droplet_pool.json", "droplet_sphere.json", "water_blob.json", "sandcastle_wash.json"]
        for name in names:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                started = time.monotonic()
                result = simulate(load_scene(EXAMPLES / name), directory, 30, make_video=False)
                elapsed = time.monotonic() - started
                self.assertTrue(result["ok"], result)
                self.assertTrue(result["completed"])
                self.assertTrue(result["diagnostics"]["finite"])
                if name == "droplet_ground.json":
                    self.assertGreater(result["diagnostics"]["minimum_water_separation_ratio"], 0.65)
                    self.assertLess(result["diagnostics"]["close_water_particle_fraction"], 0.02)
                self.assertLess(elapsed, 30)

    def test_new_field_and_capsule_scenes_complete_under_budget(self):
        names = ["geyser.json", "sand_blast.json", "whirlpool.json", "water_obstacle_course.json", "zero_g_droplet_collision.json"]
        for name in names:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                started = time.monotonic()
                result = simulate(load_scene(EXAMPLES / name), directory, 40, make_video=False)
                elapsed = time.monotonic() - started
                self.assertTrue(result["ok"], result)
                self.assertTrue(result["diagnostics"]["finite"])
                self.assertGreater(result["diagnostics"]["minimum_substep_s"], 1e-6)
                self.assertLess(elapsed, 40)

    def test_uniform_field_matches_constant_acceleration(self):
        scene = {
            "version": 1,
            "name": "constant-acceleration",
            "world": {"gravity": [0, 0, 0], "duration": 1.0, "dt": 0.01, "output_fps": 10, "bounds": {"min": [-5, -5, -5], "max": [5, 5, 5]}},
            "budget": {"wall_time_s": 20, "quality": "preview", "backend": "native"},
            "entities": [{"id": "body", "type": "point_mass", "mass": 1, "position": [0, 0, 0], "velocity": [0, 0, 0], "fixed": False}],
            "colliders": [],
            "force_fields": [{"id": "push", "type": "uniform", "targets": ["body"], "acceleration": [2, 0, 0], "start_time": 0, "end_time": 1}],
            "interactions": {"mutual_gravity": False, "gravity_G": 1, "softening": 0.02, "water_sand_drag": 0.16, "wetting_rate": 1.8},
        }
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 20, make_video=False)
            full = json.loads((Path(directory) / "result.json").read_text(encoding="utf-8"))
        self.assertTrue(result["ok"], result)
        self.assertAlmostEqual(full["trajectory"]["frames"][-1]["g"][0][0], 1.0, places=6)
        self.assertEqual(result["diagnostics"]["force_field_count"], 1)
        self.assertFalse(result["diagnostics"]["nbody_invariants_conserved"])

    def test_native_c11_backend_is_default_and_reports_convergence(self):
        scene = load_scene(EXAMPLES / "droplet_ground.json")
        scene["world"]["duration"] = 0.12
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 20, make_video=False)
        self.assertTrue(result["ok"], result)
        diagnostics = result["diagnostics"]
        self.assertEqual(diagnostics["backend"], "native-c11-dfsph")
        self.assertEqual(diagnostics["state_precision"], "float64")
        self.assertGreater(diagnostics["density_iterations_total"], 0)
        self.assertGreater(diagnostics["divergence_iterations_total"], 0)
        self.assertGreater(diagnostics["minimum_substep_s"], 0)
        self.assertTrue(math.isfinite(diagnostics["peak_mean_density_excess"]))
        self.assertTrue(math.isfinite(diagnostics["final_mean_water_density_ratio"]))
        self.assertGreater(diagnostics["represented_water_volume_m3"], 0)

    def test_millimetre_surface_tension_uses_capillary_substeps(self):
        scene = load_scene(EXAMPLES / "droplet_ground.json")
        scene["world"]["duration"] = 0.001
        scene["world"]["output_fps"] = 1
        scene["entities"][0]["properties"]["surface_tension"] = 10.0
        scene["entities"][0]["shape"] = {
            "type": "box", "center": [0, 0.015, 0],
            "size": [0.0001, 0.0001, 0.0001],
        }
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 10, make_video=False)
        self.assertTrue(result["ok"], result)
        diagnostics = result["diagnostics"]
        self.assertIn("capillary-timestep", diagnostics["solver_features"])
        self.assertEqual(diagnostics["max_substeps_used"], 8)
        self.assertLessEqual(diagnostics["minimum_substep_s"], 0.0001250001)

    def test_plane_water_adhesion_moves_nearby_water_toward_surface(self):
        final_y = []
        for adhesion in (0.0, 10.0):
            scene = load_scene(EXAMPLES / "droplet_ground.json")
            scene["world"].update({
                "gravity": [0, 0, 0], "duration": .01, "dt": .001,
                "output_fps": 60,
            })
            scene["budget"].update({"quality": "preview", "wall_time_s": 10})
            scene["entities"][0].update({
                "spacing": .001,
                "shape": {
                    "type": "box", "center": [0, .0015, 0],
                    "size": [.0001, .0001, .0001],
                },
                "properties": {"viscosity": 0, "surface_tension": 0},
            })
            scene["colliders"][0].update({"friction": 0, "water_adhesion": adhesion})
            with tempfile.TemporaryDirectory() as directory:
                summary = simulate(scene, directory, 10, make_video=False)
                self.assertTrue(summary["ok"], summary)
                result = json.loads(Path(summary["artifacts"]["result"]).read_text(encoding="utf-8"))
            final_y.append(result["trajectory"]["frames"][-1]["p"][0][1])
        self.assertLess(final_y[1], final_y[0])

    def test_inspect_rejects_invalid_native_water_diagnostic_sentinel(self):
        scene = load_scene(EXAMPLES / "droplet_ground.json")
        scene["world"]["duration"] = 0.04
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 10, make_video=False)
            self.assertTrue(result["ok"], result)
            result_path = Path(directory) / "result.json"
            baseline = json.loads(result_path.read_text(encoding="utf-8"))
            invalid_values = {
                "close_water_particle_fraction": -1.0,
                "final_mean_water_density_ratio": -1.0,
                "final_max_water_density_ratio": -1.0,
                "minimum_water_separation_ratio": -1.0,
                "water_density_p50_ratio": -1.0,
                "water_density_p95_ratio": -1.0,
                "planar_boundary_support_fraction": 2.0,
                "peak_mean_density_excess": -1.0,
                "minimum_substep_s": 0.0,
                "density_iterations_total": 0.5,
            }
            for key, value in invalid_values.items():
                with self.subTest(key=key):
                    document = json.loads(json.dumps(baseline))
                    document["trajectory"]["diagnostics"][key] = value
                    result_path.write_text(json.dumps(document), encoding="utf-8")
                    inspected = inspect(result_path)
                    self.assertFalse(inspected["ok"], inspected)
                    self.assertTrue(inspected["solver_ok"])
                    self.assertFalse(
                        inspected["quality_gate"]["numerical_checks"]
                        ["water_diagnostics_in_physical_ranges"]
                    )

    def test_inspect_rejects_truthy_non_boolean_status_fields(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.04
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 10, make_video=False)
            self.assertTrue(result["ok"], result)
            result_path = Path(directory) / "result.json"
            baseline = json.loads(result_path.read_text(encoding="utf-8"))
            mutations = (
                lambda document: document.__setitem__("ok", "false"),
                lambda document: document.__setitem__("video_required", "false"),
                lambda document: document["trajectory"]["diagnostics"].__setitem__("finite", "false"),
                lambda document: document["trajectory"]["diagnostics"].__setitem__("completed", 1),
            )
            for mutate in mutations:
                document = json.loads(json.dumps(baseline))
                mutate(document)
                result_path.write_text(json.dumps(document), encoding="utf-8")
                inspected = inspect(result_path)
                self.assertFalse(inspected["ok"], inspected)
                self.assertEqual(inspected["errors"][0]["code"], "invalid_result_contract")

    def test_inspect_rejects_corrupt_backend_scene_and_numeric_contracts(self):
        scene = load_scene(EXAMPLES / "droplet_ground.json")
        scene["world"]["duration"] = 0.04
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 10, make_video=False)
            self.assertTrue(result["ok"], result)
            result_path = Path(directory) / "result.json"
            baseline = json.loads(result_path.read_text(encoding="utf-8"))
            mutations = (
                lambda document: document["trajectory"]["diagnostics"].__setitem__("backend", {}),
                lambda document: document["trajectory"]["diagnostics"].__setitem__("backend", ""),
                lambda document: (
                    document["trajectory"]["diagnostics"].__setitem__("backend", "native-c11-verlet"),
                    document["attempts"][-1].__setitem__("backend", "native-c11-verlet"),
                ),
                lambda document: document.__setitem__("scene", []),
                lambda document: document["scene"].__setitem__("entities", {}),
                lambda document: document["trajectory"]["diagnostics"].__setitem__("runtime_s", 10**1000),
                lambda document: document["physics_claims"].__setitem__(
                    "fluid", "Certified engineering CFD with validated pressure and loads."
                ),
                lambda document: document["plan"].update({
                    "quality": "high",
                    "effective_spacing": 1.0e-9,
                    "planned_particles": 24000,
                }),
                lambda document: document["trajectory"]["diagnostics"].__setitem__(
                    "particle_count", 1
                ),
                lambda document: document["trajectory"]["diagnostics"].__setitem__(
                    "render_particle_count", 1
                ),
                lambda document: document["trajectory"]["diagnostics"].__setitem__(
                    "steps", -1
                ),
                lambda document: document["trajectory"]["diagnostics"].__setitem__(
                    "max_substeps_used", -1
                ),
                lambda document: document["trajectory"]["diagnostics"].__setitem__(
                    "force_field_count", 99
                ),
                lambda document: document["trajectory"]["diagnostics"].__setitem__(
                    "threads_used", 999
                ),
                lambda document: document["trajectory"]["frames"].pop(),
            )
            for mutate in mutations:
                document = json.loads(json.dumps(baseline))
                mutate(document)
                result_path.write_text(json.dumps(document), encoding="utf-8")
                inspected = inspect(result_path)
                self.assertFalse(inspected["ok"], inspected)
                self.assertEqual(inspected["errors"][0]["code"], "invalid_result_contract")

    def test_inspect_checks_completed_time_against_requested_duration(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.04
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 10, make_video=False)
            self.assertTrue(result["ok"], result)
            result_path = Path(directory) / "result.json"
            document = json.loads(result_path.read_text(encoding="utf-8"))
            document["trajectory"]["diagnostics"]["simulated_time_s"] = 0.0
            result_path.write_text(json.dumps(document), encoding="utf-8")
            inspected = inspect(result_path)
            self.assertFalse(inspected["ok"], inspected)
            self.assertFalse(inspected["solver_ok"])
            self.assertFalse(
                inspected["quality_gate"]["numerical_checks"]["completed_physical_duration"]
            )

    def test_inspect_rejects_large_closed_nbody_energy_drift(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.04
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 10, make_video=False)
            self.assertTrue(result["ok"], result)
            result_path = Path(directory) / "result.json"
            document = json.loads(result_path.read_text(encoding="utf-8"))
            diagnostics = document["trajectory"]["diagnostics"]
            diagnostics["nbody_relative_energy_drift"] = 0.03
            diagnostics["nbody_invariants_conserved"] = False
            result_path.write_text(json.dumps(document), encoding="utf-8")
            inspected = inspect(result_path)
            self.assertFalse(inspected["ok"], inspected)
            self.assertFalse(
                inspected["quality_gate"]["numerical_checks"]
                ["nbody_relative_energy_drift_at_most_0_02"]
            )

    def test_python_reference_backend_remains_selectable(self):
        scene = load_scene(EXAMPLES / "droplet_ground.json")
        scene["world"]["duration"] = 0.04
        scene["budget"]["backend"] = "python"
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 20, make_video=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["diagnostics"]["backend"], "python-reference")

    def test_auto_backend_falls_back_after_native_runtime_error(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"].update({"duration": 0.04, "dt": 0.01, "output_fps": 10})
        scene["budget"].update({"backend": "auto", "wall_time_s": 60})
        with tempfile.TemporaryDirectory() as directory, patch(
            "physics_demo.backend.run_scene_native",
            side_effect=NativeSimulationError("forced native failure"),
        ):
            result = simulate(scene, directory, 60, make_video=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["diagnostics"]["backend"], "python-reference")
        self.assertIn("forced native failure", result["diagnostics"]["native_fallback"])

    def test_explicit_native_unavailability_is_an_environment_error(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"].update({"duration": 0.04, "dt": 0.01, "output_fps": 10})
        scene["budget"].update({"backend": "native", "wall_time_s": 10})
        with tempfile.TemporaryDirectory() as directory, patch(
            "physics_demo.runner.run_scene",
            side_effect=NativeBackendUnavailable("no usable C11 compiler"),
        ):
            result = simulate(scene, directory, 10, make_video=False)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["stage"], "environment")
        self.assertEqual(result["errors"][0]["code"], "native_backend_unavailable")
        self.assertEqual(result["errors"][0]["path"], "budget.backend")
        self.assertFalse(result["errors"][0]["retryable"])
        guided = guide_tool_result("physics_simulate", result)
        self.assertEqual(guided["status"], "environment_unavailable")
        self.assertEqual(guided["next_action"]["kind"], "respond_to_user")

    def test_exhausted_fallback_budget_requests_a_new_plan(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"].update({"duration": 0.04, "dt": 0.01, "output_fps": 10})
        scene["budget"].update({"backend": "auto", "wall_time_s": 10})
        with tempfile.TemporaryDirectory() as directory, patch(
            "physics_demo.runner.run_scene",
            side_effect=FallbackBudgetExceeded("fallback p90 exceeds remaining deadline"),
        ):
            result = simulate(scene, directory, 10, make_video=False)
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["stage"], "estimate")
        self.assertEqual(result["errors"][0]["code"], "fallback_budget_exhausted")
        self.assertEqual(result["errors"][0]["path"], "budget.wall_time_s")
        self.assertTrue(result["errors"][0]["retryable"])

    def test_high_detail_scene_keeps_more_than_legacy_particle_count(self):
        scene = load_scene(EXAMPLES / "high_detail_droplet_sphere.json")
        result = estimate(scene, 60)
        self.assertTrue(result["ok"], result)
        self.assertGreater(result["plan"]["planned_particles"], 10_000)
        self.assertEqual(result["plan"]["backend"], "native")
        self.assertEqual(result["plan"]["particle_limit"], 24_000)

    def test_output_frames_interpolate_without_changing_dynamics(self):
        for backend in ("native", "python"):
            with self.subTest(backend=backend), tempfile.TemporaryDirectory() as directory:
                runs = {}
                for fps in (1, 60):
                    scene = load_scene(EXAMPLES / "three_body.json")
                    scene["world"].update({"duration": 0.2, "dt": 0.05, "output_fps": fps})
                    scene["budget"]["backend"] = backend
                    output = Path(directory) / f"fps-{fps}"
                    result = simulate(scene, output, 10, make_video=False)
                    full = json.loads((output / "result.json").read_text(encoding="utf-8"))
                    self.assertTrue(result["ok"], result)
                    self.assertEqual(len(full["trajectory"]["frames"]), result["plan"]["output_frames"])
                    self.assertEqual(result["plan"]["steps"], 4)
                    self.assertEqual(result["diagnostics"]["steps"], 4)
                    runs[fps] = (result, full)

                high_frames = runs[60][1]["trajectory"]["frames"]
                high_times = [frame["t"] for frame in high_frames]
                self.assertEqual(len(high_times), 13)
                self.assertAlmostEqual(high_times[-1], 0.2, places=12)
                self.assertTrue(all(later > earlier for earlier, later in zip(high_times, high_times[1:])))
                for axis in range(3):
                    start = high_frames[0]["g"][0][axis]
                    first_macro_end = high_frames[3]["g"][0][axis]
                    expected = start + (first_macro_end - start) / 3.0
                    self.assertAlmostEqual(high_frames[1]["g"][0][axis], expected, places=6)

                low_final = runs[1][1]["trajectory"]["frames"][-1]["g"]
                high_final = runs[60][1]["trajectory"]["frames"][-1]["g"]
                for low_body, high_body in zip(low_final, high_final):
                    for low_value, high_value in zip(low_body, high_body):
                        self.assertAlmostEqual(low_value, high_value, places=12)

                low_diagnostics = runs[1][0]["diagnostics"]
                high_diagnostics = runs[60][0]["diagnostics"]
                for key in (
                    "steps",
                    "simulated_time_s",
                    "nbody_relative_energy_drift",
                    "nbody_momentum_drift",
                    "nbody_invariants_conserved",
                    "force_field_count",
                ):
                    low_value = low_diagnostics[key]
                    high_value = high_diagnostics[key]
                    if isinstance(low_value, float):
                        self.assertAlmostEqual(low_value, high_value, places=12, msg=key)
                    else:
                        self.assertEqual(low_value, high_value, key)
                if backend == "native":
                    self.assertEqual(low_diagnostics["substeps"], high_diagnostics["substeps"])
                    self.assertEqual(low_diagnostics["cfl_limited_steps"], high_diagnostics["cfl_limited_steps"])

    def test_mixed_particle_spacing_policy_is_explicit(self):
        result = estimate(load_scene(EXAMPLES / "sand_sculpture_wave.json"), 55)
        self.assertTrue(result["ok"], result)
        plan = result["plan"]
        self.assertIn("single-resolution", plan["resolution_policy"])
        self.assertGreater(
            plan["uniform_spacing_particles_before_cap"],
            plan["requested_particles"],
        )
        self.assertTrue(any("unified particle entities" in item for item in plan["adjustments"]))

    def test_failed_solver_summary_preserves_stage_and_error(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.02
        incomplete = {
            "frames": [],
            "particle_materials": [],
            "particle_groups": [],
            "particle_radius": 0.0,
            "gravity_body_ids": [],
            "rigid_ids": [],
            "rigid_shapes": [],
            "diagnostics": {
                "finite": True,
                "completed": False,
                "simulated_time_s": 0.0,
                "runtime_s": 0.001,
                "particle_count": 0,
                "render_particle_count": 0,
                "steps": 0,
                "substeps": 0,
                "max_substeps_used": 0,
                "threads_used": 1,
                "force_field_count": 0,
                "backend": "native-c11-verlet",
                "native_status": "timed_out",
                "nbody_invariants_applicable": True,
                "nbody_invariants_conserved": True,
                "nbody_relative_energy_drift": 0.0,
                "nbody_momentum_drift": 0.0,
                "nbody_momentum_tolerance": 1.0e-9,
            },
        }
        with tempfile.TemporaryDirectory() as directory, patch(
            "physics_demo.runner.run_scene", return_value=incomplete
        ) as mocked_run:
            result = simulate(scene, directory, 10, make_video=False)
            saved_summary = json.loads((Path(directory) / "summary.json").read_text(encoding="utf-8"))
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["stage"], "simulate")
        self.assertEqual(result["errors"][0]["code"], "simulation_incomplete")
        self.assertEqual(saved_summary["stage"], "simulate")
        self.assertEqual(saved_summary["errors"][0]["code"], "simulation_incomplete")
        self.assertEqual(mocked_run.call_count, 1)
        self.assertEqual(result["plan"]["quality"], scene["budget"]["quality"])

    def test_video_failure_summary_preserves_stage_and_error(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.02
        with tempfile.TemporaryDirectory() as directory, patch(
            "physics_demo.runner.encode_mp4",
            side_effect=VideoEncodingError("forced encoder failure"),
        ):
            result = simulate(scene, directory, 10, make_video=True)
            saved_summary = json.loads((Path(directory) / "summary.json").read_text(encoding="utf-8"))
        self.assertFalse(result["ok"], result)
        self.assertEqual(result["stage"], "video")
        self.assertEqual(result["errors"][0]["code"], "video_encoding_failed")
        self.assertEqual(saved_summary["stage"], "video")
        self.assertEqual(saved_summary["errors"][0]["code"], "video_encoding_failed")

    def test_simulate_reuses_encoder_probe_and_accounts_for_final_gate_time(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.02

        def trusted_encoder(_result_path, output_path, fps, timeout_seconds=30.0):
            self.assertGreater(timeout_seconds, 0)
            time.sleep(0.02)
            encoded_result = json.loads(Path(_result_path).read_text(encoding="utf-8"))
            sample_count = len(encoded_result["trajectory"]["frames"])
            output_path.write_bytes(
                b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isommp41" + b"X" * 2000
            )
            validation = {
                "method": "test-probe",
                "status": "imageio_all_samples_validated_avasset_sandbox_unavailable",
                "first_frame_width": 960,
                "first_frame_height": 540,
                "track_duration_s": 0.2,
                "codec_tag": "jpeg",
                "sample_count": sample_count,
            }
            return {
                "path": str(output_path.resolve()),
                "bytes": output_path.stat().st_size,
                "codec": "Motion JPEG",
                "codec_tag": "jpeg",
                "container": "MP4",
                "fps": fps,
                "duration_s": 0.2,
                "sample_count": sample_count,
                "encoder": "test",
                "fallback": True,
                "decode_validation": validation,
                "renderer": {
                    "name": "coregraphics-continuous-liquid",
                    "version": 4,
                    "camera": "scale-adaptive-auto-fit",
                    "trajectory_preserving": True,
                },
            }

        with tempfile.TemporaryDirectory() as directory, patch(
            "physics_demo.runner.encode_mp4", side_effect=trusted_encoder
        ), patch(
            "physics_demo.results.probe_mp4",
            side_effect=AssertionError("final gate must reuse the successful encoder probe"),
        ) as probe:
            started = time.monotonic()
            result = simulate(scene, directory, 3, make_video=True)
            elapsed = time.monotonic() - started
        self.assertTrue(result["ok"], result)
        self.assertEqual(probe.call_count, 0)
        self.assertGreaterEqual(result["total_runtime_s"], 0.02)
        self.assertLess(elapsed - result["total_runtime_s"], 0.1)

    def test_completion_record_accounts_for_large_artifact_persistence(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.02
        with tempfile.TemporaryDirectory() as directory:
            returned = simulate(scene, directory, 10, make_video=False)
            self.assertTrue(returned["ok"], returned)
            result_path = Path(directory) / "result.json"
            document = json.loads(result_path.read_text(encoding="utf-8"))
            completion = json.loads(
                (Path(directory) / "completion.json").read_text(encoding="utf-8")
            )
            inspected = inspect(result_path)
        self.assertEqual(
            document["artifacts"]["completion"],
            str((Path(directory) / "completion.json").resolve()),
        )
        self.assertEqual(completion["measurement_boundary"], "through_summary_persistence")
        self.assertGreaterEqual(
            completion["wall_runtime_s"], document["total_runtime_s"]
        )
        self.assertTrue(inspected["ok"], inspected)
        self.assertAlmostEqual(
            inspected["total_runtime_s"], completion["wall_runtime_s"], places=9
        )
        self.assertGreaterEqual(returned["total_runtime_s"], inspected["total_runtime_s"])

    @unittest.skipUnless(shutil.which("clang"), "macOS clang/AVFoundation is required")
    def test_real_mp4_is_generated(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.12
        scene["world"]["output_fps"] = 12
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 30, make_video=True)
            self.assertTrue(result["ok"], result)
            video = Path(result["artifacts"]["video"]["path"])
            self.assertGreater(video.stat().st_size, 1000)
            with video.open("rb") as handle:
                header = handle.read(32)
            self.assertIn(b"ftyp", header)
            self.assertIn(result["artifacts"]["video"]["codec"], ("H.264", "Motion JPEG"))
            self.assertIn(result["artifacts"]["video"]["codec_tag"], ("avc1", "jpeg"))
            self.assertIn(
                result["artifacts"]["video"]["decode_validation"]["status"],
                (
                    "passed",
                    "imageio_all_samples_validated_avasset_sandbox_unavailable",
                    "software_all_mjpeg_samples_validated",
                ),
            )
            self.assertAlmostEqual(
                result["artifacts"]["video"]["duration_s"],
                result["artifacts"]["video"]["decode_validation"]["track_duration_s"],
                places=6,
            )
            encoded_result = json.loads(
                Path(result["artifacts"]["result"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                result["artifacts"]["video"]["sample_count"],
                len(encoded_result["trajectory"]["frames"]),
            )
            self.assertEqual(result["artifacts"]["video"]["renderer"]["version"], 4)
            self.assertTrue(result["artifacts"]["video"]["renderer"]["trajectory_preserving"])
            self.assertTrue(result["quality_gate"]["passed"])
            self.assertTrue(all(result["quality_gate"]["numerical_checks"].values()))
            inspected = inspect(result["artifacts"]["result"])
            self.assertTrue(inspected["ok"], inspected)
            self.assertTrue(inspected["quality_gate"]["video_present"])

    @unittest.skipUnless(shutil.which("clang"), "macOS clang/AVFoundation is required")
    def test_two_frame_video_reports_track_and_physical_durations_separately(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.001
        scene["world"]["output_fps"] = 60
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 30, make_video=True)
            self.assertTrue(result["ok"], result)
            video = result["artifacts"]["video"]
            result_path = Path(result["artifacts"]["result"])
            document = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual(len(document["trajectory"]["frames"]), 2)
            self.assertAlmostEqual(
                video["duration_s"], video["decode_validation"]["track_duration_s"], places=6
            )
            self.assertAlmostEqual(video["physical_duration_s"], 0.001, places=9)
            self.assertAlmostEqual(
                video["time_scale_to_physical"],
                video["physical_duration_s"] / video["duration_s"],
                places=9,
            )
            document["artifacts"]["video"]["sample_count"] += 1
            result_path.write_text(json.dumps(document), encoding="utf-8")
            sample_mismatch = inspect(result_path)
            self.assertFalse(sample_mismatch["ok"], sample_mismatch)
            self.assertIn(
                "sample count", sample_mismatch["quality_gate"]["video_verification"]
            )
            document["artifacts"]["video"]["sample_count"] -= 1
            document["artifacts"]["video"]["duration_s"] += 1.0 / video["fps"]
            result_path.write_text(json.dumps(document), encoding="utf-8")
            inspected = inspect(result_path)
        self.assertFalse(inspected["ok"], inspected)
        self.assertIn("duration", inspected["quality_gate"]["video_verification"])

    @unittest.skipUnless(shutil.which("clang"), "macOS clang/AVFoundation is required")
    def test_inspect_rejects_motion_jpeg_with_corrupt_middle_frame(self):
        scene = load_scene(EXAMPLES / "three_body.json")
        scene["world"]["duration"] = 0.2
        scene["world"]["output_fps"] = 12
        with tempfile.TemporaryDirectory() as directory:
            real_run_renderer = video_module._run_renderer

            def force_h264_failure(renderer, *args, **kwargs):
                if renderer.name.startswith("renderer-h264"):
                    return False, "forced unavailable H.264 encoder"
                return real_run_renderer(renderer, *args, **kwargs)

            with patch.object(
                video_module, "_run_renderer", side_effect=force_h264_failure
            ):
                result = simulate(scene, directory, 20, make_video=True)
            self.assertTrue(result["ok"], result)
            result_path = Path(result["artifacts"]["result"])
            video_path = Path(result["artifacts"]["video"]["path"])
            video = result["artifacts"]["video"]
            self.assertEqual(video["codec_tag"], "jpeg")
            self.assertGreaterEqual(video["sample_count"], 3)

            payload = bytearray(video_path.read_bytes())
            stsz_type = payload.rfind(b"stsz")
            self.assertGreaterEqual(stsz_type, 4)
            stsz_start = stsz_type - 4
            self.assertEqual(
                int.from_bytes(payload[stsz_start + 16 : stsz_start + 20], "big"),
                video["sample_count"],
            )
            first_size = int.from_bytes(
                payload[stsz_start + 20 : stsz_start + 24], "big"
            )
            second_size = int.from_bytes(
                payload[stsz_start + 24 : stsz_start + 28], "big"
            )
            offset = 0
            while offset + 8 <= len(payload):
                size = int.from_bytes(payload[offset : offset + 4], "big")
                kind = bytes(payload[offset + 4 : offset + 8])
                header = 8
                if size == 1:
                    size = int.from_bytes(payload[offset + 8 : offset + 16], "big")
                    header = 16
                if kind == b"mdat":
                    second_offset = offset + header + first_size
                    payload[second_offset : second_offset + second_size] = (
                        b"\0" * second_size
                    )
                    break
                offset += size
            else:
                self.fail("generated Motion JPEG MP4 has no mdat box")
            video_path.write_bytes(payload)

            inspected = inspect(result_path)
        self.assertFalse(inspected["ok"], inspected)
        self.assertFalse(inspected["quality_gate"]["video_present"])
        self.assertIn("invalid", inspected["quality_gate"]["video_verification"])


if __name__ == "__main__":
    unittest.main()
