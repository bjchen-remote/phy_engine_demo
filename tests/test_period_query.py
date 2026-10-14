"""End-to-end period answers must be grounded in validated solver steps."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from physics_demo.analysis.queries import _period
from physics_demo.runner import prepare, query, simulate
from physics_demo.systems import build_system


class PeriodQueryTests(unittest.TestCase):
    @staticmethod
    def scene(duration: float = 8.0, dt: float = .002) -> dict:
        scene = build_system({"type": "pendulum", "lengths": [1.0],
                              "masses": [1.0], "angles": [math.pi / 6],
                              "angular_velocities": [0.0], "gravity": 9.8,
                              "duration": duration, "dt": dt, "output_fps": 1})
        scene["budget"]["validation"] = "strict"
        return scene

    def test_measured_period_and_analytic_references_have_distinct_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(self.scene(), Path(directory) / "run", make_video=False)
            self.assertTrue(result["ok"], result)
            response = query(Path(directory) / "run", "bob1-period")
            self.assertTrue(response["ok"], response)
            self.assertTrue(response["quality_gate"]["numerical_passed"])
            answer = response["answers"][0]
            self.assertEqual(answer["status"], "measured")
            self.assertEqual(answer["direction"], "falling")
            self.assertEqual(answer["reference_value"], 0)
            self.assertEqual(answer["complete_cycles"], 3)
            self.assertAlmostEqual(answer["period_s"], 2.042030945, delta=2e-6)
            self.assertAlmostEqual(answer["frequency_hz"], 1 / answer["period_s"])
            self.assertEqual(len(answer["crossings"]), 4)
            self.assertTrue(all(left <= crossing["time_s"] <= right
                                for crossing in answer["crossings"]
                                for left, right in [crossing["sample_bracket_s"]]))
            self.assertEqual(answer["sampling_interval_s"], .002)
            reference = answer["ideal_single_pendulum_reference"]
            self.assertAlmostEqual(reference["small_angle_period_s"], 2.007089923, delta=1e-8)
            self.assertAlmostEqual(reference["finite_amplitude_period_s"], 2.042030945, delta=1e-8)
            self.assertGreater(answer["period_s"], reference["small_angle_period_s"])

    def test_short_window_does_not_invent_period(self):
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(self.scene(duration=1.0), Path(directory) / "run", make_video=False)
            self.assertTrue(result["ok"], result)
            answer = query(Path(directory) / "run", "bob1-period")["answers"][0]
            self.assertEqual(answer["status"], "insufficient_cycles")
            self.assertIsNone(answer["period_s"])
            self.assertEqual(answer["window_s"], [0.0, 1.0])

    def test_length_sweep_requires_separate_runs_and_sufficient_windows(self):
        periods = {}
        with tempfile.TemporaryDirectory() as directory:
            for length in (1.0, 2.0, 4.0):
                scene = build_system({"type": "pendulum", "lengths": [length],
                                      "angles": [.05], "gravity": 9.8,
                                      "duration": 9 * math.sqrt(length),
                                      "dt": .004, "output_fps": 1})
                scene["budget"]["validation"] = "strict"
                output = Path(directory) / str(length)
                self.assertTrue(simulate(scene, output, make_video=False)["ok"])
                answer = query(output, "bob1-period")["answers"][0]
                self.assertEqual(answer["status"], "measured")
                periods[length] = answer["period_s"]
                self.assertAlmostEqual(answer["period_s"] / math.sqrt(length),
                                       2 * math.pi / math.sqrt(9.8), delta=.002)
        self.assertGreater(periods[4.0], periods[2.0])
        self.assertGreater(periods[2.0], periods[1.0])

    def test_declared_direction_and_reference_are_required(self):
        scene = self.scene()
        scene["queries"][-1].pop("direction")
        self.assertFalse(prepare(scene)["ok"])
        scene = self.scene()
        scene["queries"][-1]["reference_value"] = float("nan")
        self.assertFalse(prepare(scene)["ok"])

    def test_irregular_returns_and_underresolved_returns_are_not_fixed_periods(self):
        declaration = {"reference_value": 0.0, "direction": "falling", "min_cycles": 2}
        times = [float(x) for x in range(12)]
        irregular = [1.0, -1.0, 1.0, -1.0, 1.0, 1.0, -1.0,
                     1.0, 1.0, 1.0, -1.0, 1.0]
        answer = _period(declaration, times, irregular)
        self.assertEqual(answer["status"], "sampling_too_coarse")
        self.assertIsNone(answer["period_s"])
        # Piecewise-linear oscillation with falling crossings at 0.5, 2.5,
        # 5 and 9 seconds. Three complete intervals disagree substantially.
        anchors = [(0, 1), (1, -1), (2, 1), (3, -1),
                   (4, 1), (6, -1), (8, 1), (10, -1), (12, 1)]
        times = [x / 10 for x in range(121)]
        values = []
        for time_s in times:
            for (left_t, left_v), (right_t, right_v) in zip(anchors, anchors[1:]):
                if left_t <= time_s <= right_t:
                    values.append(left_v + (right_v - left_v) *
                                  (time_s - left_t) / (right_t - left_t))
                    break
        answer = _period(declaration, times, values)
        self.assertEqual(answer["status"], "irregular_returns")
        self.assertIsNone(answer["period_s"])


if __name__ == "__main__":
    unittest.main()
