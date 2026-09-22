"""Preflight resource guards and public query boundaries before allocation."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from physics_demo.api import call_tool
from physics_demo.runner import prepare, simulate

ROOT = Path(__file__).resolve().parents[1]


class QueryResourceTests(unittest.TestCase):
    def test_scalar_budget_rejected_before_solver(self):
        scene = json.loads((ROOT / "examples/sliders_separate.json").read_text())
        scene["world"].update(duration=30, dt=.0001, output_fps=1)
        with tempfile.TemporaryDirectory() as output, patch("physics_demo.runner.run_scene") as solver:
            result = simulate(scene, output, make_video=False)
            self.assertFalse(result["ok"])
            self.assertEqual(result["errors"][0]["code"], "measurement_budget_exceeded")
            solver.assert_not_called()

    def test_pairwise_observation_work_is_bounded_even_with_low_video_fps(self):
        scene = {"world": {"duration": 2, "dt": .0001, "output_fps": 1},
                 "entities": [{"id": str(i), "type": "point_mass", "position": [i - 32, 1, 0]} for i in range(64)],
                 "queries": [{"id": "orbit", "type": "nbody_stability", "max_radius": 100, "min_separation": .1}]}
        result = prepare(scene)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "measurement_budget_exceeded")
        self.assertGreater(result["plan"]["measurement_plan"]["work_units"], 50_000_000)
        self.assertLess(result["plan"]["measurement_plan"]["scalar_capacity"], 250_000)

    def test_query_count_cap_does_not_accept_unbounded_declarations(self):
        scene = json.loads((ROOT / "examples/sliders_separate.json").read_text())
        query = scene["queries"][0]
        scene["queries"] = [{**copy.deepcopy(query), "id": str(i)} for i in range(17)]
        result = prepare(scene)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "query_limit")

    def test_explicit_native_does_not_silently_run_slider_python(self):
        scene = json.loads((ROOT / "examples/sliders_separate.json").read_text())
        scene["budget"]["backend"] = "native"
        result = prepare(scene)
        self.assertFalse(result["ok"])
        self.assertEqual(result["errors"][0]["code"], "slider_model_boundary")

    def test_python_decimal_terminal_step_keeps_all_measurements(self):
        scene = {"world": {"duration": .07, "dt": .01, "gravity": [0, 0, 0]},
                 "budget": {"backend": "python"},
                 "entities": [{"id": "p", "type": "point_mass", "position": [0, 1, 0], "velocity": [1, 0, 0]}],
                 "queries": [{"id": "x", "type": "series", "metric": {"type": "centroid", "entity": "p", "axis": "x"}}]}
        with tempfile.TemporaryDirectory() as output:
            result = simulate(scene, output, make_video=False)
            self.assertTrue(result["ok"], result.get("errors"))
            queried = call_tool("physics_query", {"result_path": output, "query_id": "x"})
            self.assertEqual(queried["status"], "query_completed")
            self.assertEqual(len(queried["series"]["times_s"]), 8)
            self.assertAlmostEqual(queried["series"]["values"][-1], .07)


if __name__ == "__main__":
    unittest.main()
