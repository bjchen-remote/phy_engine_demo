"""System factories produce bounded scenes for the common native runner."""
from __future__ import annotations

import copy
import json
import math
import tempfile
import unittest

from physics_demo.api import call_tool
from physics_demo.runner import simulate
from physics_demo.systems import SYSTEM_REGISTRY, SYSTEM_TYPES, build_system


class SystemFactoryTests(unittest.TestCase):
    def test_registry_is_explicit_and_read_only(self):
        self.assertEqual(SYSTEM_TYPES, ("pendulum", "double_pendulum"))
        with self.assertRaises(TypeError):
            SYSTEM_REGISTRY["other"] = lambda _: {}

    def test_defaults_prepare_as_native_rods_with_full_state_observations(self):
        for kind, count in (("pendulum", 1), ("double_pendulum", 2)):
            with self.subTest(kind=kind):
                scene = build_system({"type": kind})
                self.assertEqual(len(scene["entities"]), count + 1)
                self.assertTrue(scene["entities"][0]["fixed"])
                self.assertEqual(scene["entities"][0]["position"], [0, 0, 0])
                self.assertEqual(len(scene["connections"]), count)
                self.assertEqual({link["type"] for link in scene["connections"]}, {"rod"})
                self.assertEqual(scene["force_fields"][0]["targets"],
                                 [f"bob{i}" for i in range(1, count + 1)])
                self.assertEqual(scene["force_fields"][0]["acceleration"], [0, -9.81, 0])
                self.assertEqual(scene["world"]["gravity"], [0, 0, 0])
                self.assertFalse(scene["interactions"]["mutual_gravity"])
                self.assertEqual([query["id"] for query in scene["queries"]],
                                 [f"bob{i}-{metric}" for i in range(1, count + 1)
                                  for metric in ("x", "y", "z", "speed")])
                prepared = call_tool("physics_prepare", {"scene_json": json.dumps(scene)})
                self.assertTrue(prepared["ready_to_simulate"], prepared)
                self.assertEqual(prepared["plan"]["backend"], "connections")
                self.assertEqual(prepared["plan"]["measurement_plan"]["source"], "solver_macro_steps")
                self.assertEqual(prepared["plan"]["measurement_plan"]["precision"], "float64")

    def test_absolute_angles_and_velocities_satisfy_both_rod_constraints(self):
        scene = build_system({"type": "double_pendulum", "lengths": [1.2, .7],
                              "masses": [2, 3], "angles": [.4, -.7],
                              "angular_velocities": [.8, -1.1]})
        nodes = scene["entities"]
        for index, (length, angle, omega) in enumerate(zip([1.2, .7], [.4, -.7], [.8, -1.1])):
            a, b = nodes[index:index + 2]
            delta = [y - x for x, y in zip(a["position"], b["position"])]
            relative_v = [y - x for x, y in zip(a["velocity"], b["velocity"])]
            self.assertAlmostEqual(math.hypot(*delta), length, places=14)
            self.assertAlmostEqual(math.atan2(delta[0], -delta[1]), angle, places=14)
            self.assertAlmostEqual(sum(x * v for x, v in zip(delta, relative_v)), 0, places=14)
            self.assertAlmostEqual(relative_v[0], length * omega * math.cos(angle), places=14)
            self.assertAlmostEqual(relative_v[1], length * omega * math.sin(angle), places=14)
        prepared = call_tool("physics_prepare", {"scene_json": json.dumps(scene)})
        self.assertTrue(prepared["ready_to_simulate"], prepared)

    def test_factory_does_not_mutate_or_alias_caller_input_or_previous_results(self):
        spec = {"type": "double_pendulum", "lengths": [1, 2], "masses": [2, 1],
                "angles": [.4, .3], "angular_velocities": [.1, .2]}
        original = copy.deepcopy(spec)
        first, second = build_system(spec), build_system(spec)
        first["entities"][1]["position"][0] = 99
        first["connections"][0]["rest_length"] = 99
        first["force_fields"][0]["targets"].append("other")
        self.assertEqual(spec, original)
        self.assertEqual(second, build_system(spec))

    def test_malformed_json_shapes_and_unknown_fields_are_value_errors(self):
        for value in (None, [], "pendulum", True, {}, {"type": []}, {"type": "unknown"},
                      {"type": "pendulum", 1: 2}, {"type": "pendulum", "length": 1},
                      {"type": "pendulum", "backend": "python"}):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    build_system(value)
        for name in ("lengths", "masses", "angles", "angular_velocities"):
            for value in (None, 1, {}, (), [1, 2], [], [None], [True], ["1"], [[]]):
                with self.subTest(name=name, value=repr(value)):
                    with self.assertRaises(ValueError):
                        build_system({"type": "pendulum", name: value})
        for value in (None, [], True, "unknown"):
            with self.assertRaises(ValueError):
                build_system({"type": "pendulum", "quality": value})

    def test_nonfinite_and_huge_numeric_inputs_never_leak_unstructured_exceptions(self):
        for name in ("lengths", "masses", "angles", "angular_velocities", "gravity",
                     "duration", "dt", "output_fps", "wall_time_s"):
            for value in (True, None, [], {}, "1", float("nan"), float("inf"), -float("inf"), 10 ** 500):
                supplied = [value] if name in ("lengths", "masses", "angles", "angular_velocities") else value
                with self.subTest(name=name, value=repr(value)[:30]):
                    with self.assertRaises(ValueError):
                        build_system({"type": "pendulum", name: supplied})

    def test_out_of_bounds_and_derived_speed_are_rejected(self):
        cases = [("lengths", [0]), ("lengths", [1001]), ("lengths", [401]),
                 ("masses", [1e-10]), ("masses", [1e12 + 1]),
                 ("angles", [7]), ("angular_velocities", [-501]),
                 ("gravity", 0), ("gravity", 251), ("duration", 0), ("duration", 31),
                 ("dt", .00009), ("dt", .051), ("output_fps", 0),
                 ("output_fps", 60.5), ("output_fps", 24.5),
                 ("wall_time_s", .9), ("wall_time_s", 301)]
        for name, value in cases:
            with self.subTest(name=name, value=value):
                with self.assertRaises(ValueError):
                    build_system({"type": "pendulum", name: value})
        with self.assertRaisesRegex(ValueError, "bob1"):
            build_system({"type": "pendulum", "lengths": [2], "angular_velocities": [251]})
        with self.assertRaisesRegex(ValueError, "bob2"):
            build_system({"type": "double_pendulum", "angles": [0, 0],
                          "angular_velocities": [251, 251]})

    def test_supported_endpoints_are_not_silently_clamped(self):
        scene = build_system({"type": "pendulum", "lengths": [400], "masses": [1e12],
                              "angles": [-2 * math.pi], "angular_velocities": [0],
                              "gravity": 250, "duration": 30, "dt": .05,
                              "output_fps": 60.0, "wall_time_s": 1, "quality": "high"})
        self.assertEqual(scene["connections"][0]["rest_length"], 400)
        self.assertEqual(scene["entities"][1]["mass"], 1e12)
        self.assertEqual(scene["world"]["output_fps"], 60)
        self.assertEqual(scene["world"]["dt"], .05)
        self.assertEqual(scene["budget"]["wall_time_s"], 1)
        self.assertLessEqual(scene["world"]["bounds"]["max"][0] - scene["world"]["bounds"]["min"][0], 1000)
        scene = build_system({"type": "pendulum", "lengths": [1e-6], "masses": [1e-9],
                              "duration": 1e-4, "dt": 1e-4, "gravity": 1e-9,
                              "angular_velocities": [500], "output_fps": 1})
        self.assertEqual(scene["connections"][0]["rest_length"], 1e-6)

    def test_real_native_run_records_velocity_instead_of_video_differences(self):
        scene = build_system({"type": "double_pendulum", "angles": [0, 0],
                              "angular_velocities": [.3, -.5], "duration": .02, "output_fps": 1})
        with tempfile.TemporaryDirectory() as output:
            result = simulate(scene, output, make_video=False)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["diagnostics"]["backend"], "native-c11-connections")
        answers = {item["id"]: item for item in result["measurements"]["answers"]}
        self.assertAlmostEqual(answers["bob1-speed"]["initial"], .3, places=14)
        self.assertAlmostEqual(answers["bob2-speed"]["initial"], .2, places=14)
        self.assertEqual(answers["bob1-z"]["maximum"]["value"], 0)
        self.assertEqual(answers["bob2-z"]["minimum"]["value"], 0)
        self.assertEqual(result["measurements"]["sampling"]["sample_count"], 11)


if __name__ == "__main__":
    unittest.main()
