"""Wall-budget delivery gates include recorded persistence, without invented timing."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from physics_demo.jsonio import write
from physics_demo.results import _apply_runtime_budget, _summary
from physics_demo.runner import inspect, load_scene, prepare, query, simulate


def scene():
    value = load_scene(Path(__file__).parents[1] / "examples" / "three_body.json")
    value["world"]["duration"] = .02
    value["budget"]["wall_time_s"] = 5
    return value


class RuntimeBudgetTests(unittest.TestCase):
    def test_longer_budget_is_explicit_and_preserves_video_and_physics_parameters(self):
        value=scene()
        result=prepare(value, 180)
        self.assertTrue(result['ready_to_simulate'])
        self.assertEqual(result['scene']['budget']['wall_time_s'],180)
        self.assertEqual(result['scene']['world']['duration'],value['world']['duration'])
        self.assertFalse(prepare(value,301)['ok'])

    def assert_budget_failure(self, result):
        self.assertFalse(result["ok"], result)
        self.assertFalse(result["quality_gate"]["passed"])
        self.assertFalse(result["quality_gate"]["runtime_checks"]["wall_time_within_budget"])
        self.assertEqual(result["quality_gate"]["failed_checks"].count("wall_time_within_budget"), 1)
        self.assertIn("wall_time_budget_exceeded", [error["code"] for error in result["errors"]])

    def delayed_run(self, directory, delayed_artifact):
        """Advance only runner's clock after a real write; no sleeping or solver mocks."""
        offset, result_writes = 0.0, 0

        def record(path, value):
            nonlocal offset, result_writes
            write(path, value)
            if path.name == "result.json":
                result_writes += 1
            if path.name == delayed_artifact and (path.name != "result.json" or result_writes == 2):
                offset += 6.0

        clock = SimpleNamespace(monotonic=lambda: time.monotonic()+offset, time=time.time)
        with patch("physics_demo.runner.time", clock), patch("physics_demo.runner._write_json", side_effect=record):
            return simulate(scene(), directory, make_video=False)

    def test_slow_final_result_persistence_fails_live_and_inspected_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            returned = self.delayed_run(directory, "result.json")
            inspected = inspect(directory)
            stored = json.loads((Path(directory)/"summary.json").read_text())
        for result in (returned, inspected, stored):
            self.assert_budget_failure(result)
        self.assertEqual(returned["runtime_measurement_boundary"], "through_completion_persistence")
        self.assertEqual(inspected["runtime_measurement_boundary"], "through_summary_persistence")

    def test_slow_summary_persistence_is_in_completion_record(self):
        with tempfile.TemporaryDirectory() as directory:
            returned = self.delayed_run(directory, "summary.json")
            inspected = inspect(directory)
            completion = json.loads((Path(directory)/"completion.json").read_text())
        self.assert_budget_failure(returned)
        self.assert_budget_failure(inspected)
        self.assertGreater(completion["wall_runtime_s"], 5)
        self.assertEqual(completion["measurement_boundary"], "through_summary_persistence")

    def test_completion_write_overrun_is_persisted_without_inventing_final_write_time(self):
        with tempfile.TemporaryDirectory() as directory:
            returned = self.delayed_run(directory, "completion.json")
            inspected = inspect(directory)
            completion = json.loads((Path(directory)/"completion.json").read_text())
            stored = json.loads((Path(directory)/"summary.json").read_text())
        self.assert_budget_failure(returned)
        self.assert_budget_failure(inspected)
        self.assert_budget_failure(stored)
        self.assertGreaterEqual(stored["artifact_persistence_s"], 6)
        self.assertGreater(completion["wall_runtime_s"], 5)
        self.assertGreaterEqual(returned["total_runtime_s"]-completion["wall_runtime_s"], 6)
        # Refreshing the failed summary preserves the existing record format;
        # the final completion write's own time remains exclusive to live data.
        self.assertEqual(inspected["runtime_measurement_boundary"], "through_summary_persistence")

    def test_final_quality_check_runtime_is_gated_before_result_persistence(self):
        offset = 0.0

        def slow_summary(*args, **kwargs):
            nonlocal offset
            result = _summary(*args, **kwargs)
            offset += 6.0
            return result

        clock = SimpleNamespace(monotonic=lambda: time.monotonic()+offset, time=time.time)
        with tempfile.TemporaryDirectory() as directory:
            with patch("physics_demo.runner.time", clock), patch("physics_demo.runner._summary", side_effect=slow_summary):
                returned = simulate(scene(), directory, make_video=False)
            document = json.loads((Path(directory)/"result.json").read_text())
            inspected = inspect(directory)
        self.assert_budget_failure(returned)
        self.assert_budget_failure(inspected)
        self.assertFalse(document["ok"])

    def test_recorded_result_and_completion_overruns_cannot_replay_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = simulate(scene(), directory, make_video=False)
            self.assertTrue(valid["ok"], valid)
            path = Path(directory)
            document = json.loads((path/"result.json").read_text())
            late = copy.deepcopy(document)
            late["total_runtime_s"] = 6
            self.assert_budget_failure(_summary(late, path))
            completion = json.loads((path/"completion.json").read_text())
            completion["wall_runtime_s"] = 6
            write(path/"completion.json", completion)
            self.assert_budget_failure(inspect(directory))
            self.assert_budget_failure(query(directory))

    def test_legacy_result_without_runtime_gate_fields_still_inspects(self):
        with tempfile.TemporaryDirectory() as directory:
            valid = simulate(scene(), directory, make_video=False)
            self.assertTrue(valid["ok"], valid)
            path = Path(directory)/"result.json"
            document = json.loads(path.read_text())
            document.pop("runtime_measurement_boundary", None)
            write(path, document)
            inspected = inspect(directory)
        self.assertTrue(inspected["ok"], inspected)
        self.assertTrue(inspected["quality_gate"]["runtime_checks"]["wall_time_within_budget"])

    def test_runtime_gate_is_idempotent_and_does_not_resurrect_other_failures(self):
        summary = {"ok": False, "plan": {"wall_time_limit_s": 5}, "total_runtime_s": 5,
                   "quality_gate": {"passed": False, "failed_checks": ["finite_state"]}}
        self.assertFalse(_apply_runtime_budget(summary)["ok"])
        self.assertTrue(summary["quality_gate"]["runtime_checks"]["wall_time_within_budget"])
        _apply_runtime_budget(summary, 6)
        _apply_runtime_budget(summary, 7)
        self.assert_budget_failure(summary)
        self.assertIn("finite_state", summary["quality_gate"]["failed_checks"])
        self.assertEqual(len(summary["errors"]), 1)

    def test_infeasible_budget_message_describes_an_estimate(self):
        definition = scene()
        definition["budget"]["wall_time_s"] = 1
        prepared = prepare(definition)
        with tempfile.TemporaryDirectory() as directory:
            rejected = simulate(definition, directory, make_video=False)
        for result in (prepared, rejected):
            self.assertFalse(result["ok"])
            self.assertIn("estimated p90", result["errors"][0]["message"])
            self.assertNotIn("calibrated", result["errors"][0]["message"])


if __name__ == "__main__":
    unittest.main()
