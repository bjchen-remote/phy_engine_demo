"""QQ-facing data summaries must match the verified requested quantity."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

PACKAGE = Path(__file__).resolve().parents[1] / "physics"
SOURCE = PACKAGE.parents[1]
sys.path.insert(0, str(SOURCE))
import physics_demo  # Keep the main test process on the source package.
sys.path.insert(0, str(PACKAGE))
from toolbox_adapter import _pcb_data_reply, _physics_data_reply
sys.path.remove(str(PACKAGE))
if str(PACKAGE / "physics_release") in sys.path:
    sys.path.remove(str(PACKAGE / "physics_release"))


class DataReplyTests(unittest.TestCase):
    def test_period_requires_numerical_gate_and_preserves_analytic_source(self):
        answer = {
            "id": "bob1-period", "type": "period", "status": "measured",
            "definition": {"metric": {"type": "centroid", "entity": "bob1", "axis": "x"}},
            "direction": "falling", "reference_value": 0.0,
            "period_s": 2.042030874, "frequency_hz": 1 / 2.042030874,
            "complete_cycles": 3, "sampling_interval_s": .002,
            "ideal_single_pendulum_reference": {
                "small_angle_period_s": 2.007089923,
                "finite_amplitude_period_s": 2.042030945,
                "release_angle_rad": .5235987756,
            },
        }
        summary = {"quality_gate": {"numerical_passed": True},
                   "measurements": {"usable": True, "answers": [answer]}}
        status, phrase, data = _physics_data_reply(summary, "请测量这个单摆的周期是多少")
        self.assertEqual(status, "measured")
        self.assertIn("求解器实测周期 2.042031 s", phrase)
        self.assertIn("大振幅解析参考", phrase)
        self.assertEqual(data[0]["analytic_reference"]["finite_amplitude_period_s"], 2.042030945)
        summary["quality_gate"]["numerical_passed"] = False
        status, phrase, data = _physics_data_reply(summary, "周期是多少")
        self.assertEqual(status, "unavailable")
        self.assertIn("未通过数值质量检查", phrase)
        self.assertFalse(data)

    def test_unasked_metric_is_not_substituted(self):
        summary = {"quality_gate": {"numerical_passed": True},
                   "measurements": {"usable": True, "answers": [
                       {"id": "bob1-x", "type": "series", "status": "measured",
                        "definition": {"metric": {"type": "centroid"}}},
                       {"id": "bob1-speed", "type": "series", "status": "measured",
                        "definition": {"metric": {"type": "speed"}},
                        "maximum": {"value": 1.6, "time_s": 1.0},
                        "unit": "m/s", "sampling_interval_s": .002},
                   ]}}
        self.assertEqual(_physics_data_reply(summary, "最大能量是多少")[0], "unavailable")
        status, phrase, _ = _physics_data_reply(summary, "最大速度是多少")
        self.assertEqual(status, "measured")
        self.assertIn("1.6 m/s", phrase)

    def test_pcb_time_threshold_and_model_boundary(self):
        result = {"mode": "transient", "duration_s": 2.0,
                  "min_temperature_c": 30.0, "mean_temperature_c": 50.0,
                  "max_temperature_c": 90.0, "total_power_w": 4.0,
                  "time_series": [
                      {"time_s": 0.0, "max_temperature_c": 20.0},
                      {"time_s": 1.0, "max_temperature_c": 70.0},
                      {"time_s": 2.0, "max_temperature_c": 90.0},
                  ]}
        status, phrase, data = _pcb_data_reply(result, "最高板温首次超过80 °C是什么时候")
        self.assertEqual(status, "measured")
        self.assertEqual(data[0]["sample_bracket_s"], [1.0, 2.0])
        self.assertEqual(data[0]["crossing_estimate_s"], 1.5)
        self.assertIn("热求解步括区", phrase)
        self.assertEqual(_pcb_data_reply(result, "芯片结温是多少")[0], "unavailable")


if __name__ == "__main__":
    unittest.main()
