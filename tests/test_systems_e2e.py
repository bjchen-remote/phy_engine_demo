"""Public build/run/read/analyse workflow, independent of solver internals."""
from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import unittest

from physics_demo import build_system, load_run, run_system
from physics_demo.analysis import compare_pendulums, pendulum_report
from physics_demo.api import call_tool


class SystemWorkflowTests(unittest.TestCase):
    def test_system_tool_returns_exact_factory_scene_and_preparation_handoff(self):
        spec = {"type": "double_pendulum", "duration": .04,
                "angles": [1.1, 2.2], "angular_velocities": [.3, -.7]}
        built = call_tool("physics_system", {"spec_json": json.dumps(spec)})
        self.assertTrue(built["ok"], built)
        self.assertEqual(built["status"], "system_built")
        self.assertEqual(built["next_action"]["tool"], "physics_prepare")
        self.assertEqual(built["scene"], build_system(spec))
        self.assertEqual(json.loads(built["scene_json"]), built["scene"])
        ready = call_tool("physics_prepare", {"scene_json": built["scene_json"]})
        self.assertTrue(ready["ok"] and ready["ready_to_simulate"], ready)
        self.assertEqual(ready["plan"]["backend"], "connections")
        self.assertEqual(ready["plan"]["measurement_plan"]["query_count"], 8)

    def test_run_reader_and_energy_use_declared_macro_state(self):
        spec = {"type": "double_pendulum", "duration": .04, "dt": .002,
                "output_fps": 1, "angles": [0, 0], "masses": [2, 3],
                "lengths": [1, 2], "angular_velocities": [.3, -.7]}
        with tempfile.TemporaryDirectory() as out:
            summary = run_system(spec, out, make_video=False)
            self.assertTrue(summary["ok"], summary)
            run = load_run(out)
            initial = run.state("bob2", 0)
            self.assertEqual(initial["position_m"], [0, -3, 0])
            self.assertAlmostEqual(initial["speed_m_s"], 1.1, places=14)
            self.assertEqual(initial["source"], "solver_macro_steps")
            self.assertAlmostEqual(run.state("bob2")["time_s"], .04)
            self.assertEqual(len(run.times), 21)
            report = pendulum_report(run)
            self.assertAlmostEqual(report["kinetic_energy_J"][0], .5 * 2 * .3**2 + .5 * 3 * 1.1**2)
            self.assertAlmostEqual(report["potential_energy_J"][0], -9.81 * (2 + 9))
            self.assertLess(report["maximum_rod_length_error_m"], 1e-6)
            self.assertLess(report["peak_energy_drift_fraction_of_scale"], 1e-5)
            self.assertFalse(report["long_term_stability_proven"])
            speed = run.series("bob2-speed")
            self.assertAlmostEqual(speed["values"][0], initial["speed_m_s"])
            self.assertEqual(speed["times_s"], list(run.times))

    def test_nearby_initial_conditions_report_sensitivity_without_chaos_claim(self):
        spec = {"type": "double_pendulum", "duration": .04}
        with tempfile.TemporaryDirectory() as parent:
            paths = [Path(parent) / name for name in ("base", "perturbed")]
            first = run_system(spec, paths[0], make_video=False)
            second = run_system({**spec, "angles": [2, 2.402]}, paths[1], make_video=False)
            self.assertTrue(first["ok"] and second["ok"], (first, second))
            report = compare_pendulums(load_run(paths[0]), load_run(paths[1]))
            self.assertAlmostEqual(report["initial_separation_m"], 2 * math.sin(.001), places=12)
            self.assertGreaterEqual(report["maximum_separation_m"], report["initial_separation_m"])
            self.assertFalse(report["chaos_proven"])

    def test_invalid_system_tool_preserves_protocol_failure_metadata(self):
        cases = [('{"type":"unknown"}', "needs_argument_fix", "invalid_arguments"),
                 ('{"type":"double_pendulum","masses":[0,1]}', "needs_argument_fix", "invalid_arguments"),
                 ('{"type":"pendulum","gravity":NaN}', "input_rejected", "ambiguous_json"),
                 ('{"type":"pendulum","type":"double_pendulum"}', "input_rejected", "ambiguous_json")]
        for spec_json, status, code in cases:
            with self.subTest(spec_json=spec_json):
                failed = call_tool("physics_system", {"spec_json": spec_json})
                self.assertFalse(failed["ok"])
                self.assertEqual(failed["stage"], "arguments")
                self.assertEqual(failed["status"], status)
                if status == "needs_argument_fix":
                    self.assertEqual(failed["next_action"]["tool"], "physics_system")
                else:
                    self.assertEqual(failed["next_action"]["kind"], "respond_to_user")
                self.assertTrue(failed["errors"])
                for error in failed["errors"]:
                    self.assertEqual(error["code"], code)
                    self.assertTrue(error["message"])
                    self.assertIn("retryable", error)
                    self.assertTrue(error["suggestion"])

    def test_run_system_keeps_budget_failure_instead_of_creating_an_incomplete_run(self):
        with tempfile.TemporaryDirectory() as out:
            result = run_system({"type": "double_pendulum", "duration": 30, "dt": .0001},
                                out, make_video=False)
            self.assertFalse(result["ok"], result)
            self.assertEqual(result["stage"], "estimate")
            self.assertIn("connection_budget_exceeded", [error["code"] for error in result["errors"]])
            self.assertFalse((Path(out) / "result.json").exists())
            self.assertFalse((Path(out) / "simulation.mp4").exists())


if __name__ == "__main__":
    unittest.main()
