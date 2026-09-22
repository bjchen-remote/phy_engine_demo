"""Independent query audit: uncertainty, malformed input, and evidence binding."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import time
import unittest

from physics_demo.planning import make_plan
from physics_demo.queries import _crossing, digest, evaluate, validate_observations
from physics_demo.runner import query, simulate
from physics_demo.schema import normalize_and_validate
from physics_demo.sliders import run_scene


ROOT = Path(__file__).resolve().parents[1]


def _raw_slider() -> dict:
    return json.loads((ROOT / "examples" / "sliders_separate.json").read_text())


def _normal(raw: dict) -> dict:
    report = normalize_and_validate(raw)
    if not report["valid"]:
        raise AssertionError(report["errors"])
    return report["scene"]


def _collected() -> tuple[dict, dict, dict]:
    raw = _raw_slider()
    raw["world"].update(duration=0.1, dt=0.01, output_fps=10)
    scene = _normal(raw)
    plan = make_plan(scene, include_video=False)
    trajectory = run_scene(scene, plan, time.monotonic() + 5)
    return scene, plan, trajectory


def _stability() -> tuple[dict, dict, dict]:
    raw = json.loads((ROOT / "examples" / "three_body.json").read_text())
    raw["world"].update(duration=0.02, dt=0.005)
    raw["queries"] = [{"id": "stable", "type": "nbody_stability", "max_radius": 3.0, "min_separation": 0.01}]
    scene = _normal(raw)
    plan = make_plan(scene, include_video=False)
    trajectory = {
        "diagnostics": {"completed": True, "finite": True, "simulated_time_s": 0.02,
                        "steps": 4, "nbody_momentum_tolerance": 1e-8,
                        "nbody_invariants_applicable": True, "nbody_invariants_conserved": True},
        "observations": {"version": 1, "source": "solver_macro_steps",
                         "times": [0, 0.005, 0.01, 0.015, 0.02], "columns": {},
                         "nbody": {"stable": {"max_com_radius": [1.0] * 5, "min_pair_distance": [0.5] * 5,
                                               "energy": [-1.0] * 5, "momentum": [[0.0, 0.0, 0.0] for _ in range(5)]}}},
    }
    return scene, plan, trajectory


def _answer(scene: dict, plan: dict, trajectory: dict) -> dict:
    return evaluate(scene, plan, trajectory, "audit", "audit-hash")["answers"][0]


class QueryAdversarialTests(unittest.TestCase):
    def test_malformed_query_values_are_rejected_without_crash(self):
        cases = [None, True, {}, "query", [None], [True], [{"id": [], "type": []}],
                 [{"id": "q", "type": "threshold", "metric": []}],
                 [{"id": "q", "type": "threshold", "metric": {"type": []}}]]
        for value in cases:
            with self.subTest(value=value):
                raw = _raw_slider()
                raw["queries"] = value
                self.assertFalse(normalize_and_validate(raw)["valid"])

    def test_query_numbers_and_targets_never_accept_boolean_nan_or_lists(self):
        mutations = [("value", True), ("value", float("nan")), ("value", float("inf")),
                     ("hold_for", True), ("hold_for", -1), ("hold_for", 100), ("operator", []),
                     ("id", []), ("id", ""), ("id", "x" * 129)]
        for field, value in mutations:
            with self.subTest(field=field, value=value):
                raw = _raw_slider()
                raw["queries"][0][field] = value
                self.assertFalse(normalize_and_validate(raw)["valid"])
        for ids in (True, [], ["left"], ["left", "left"], ["left", []], ["left", "missing"]):
            with self.subTest(targets=ids):
                raw = _raw_slider()
                raw["queries"][0]["metric"]["entities"] = ids
                self.assertFalse(normalize_and_validate(raw)["valid"])

    def test_duplicate_query_ids_and_unknown_fields_rejected(self):
        raw = _raw_slider()
        raw["queries"][1]["id"] = raw["queries"][0]["id"]
        self.assertFalse(normalize_and_validate(raw)["valid"])
        raw = _raw_slider()
        raw["queries"][0]["metric"]["unit"] = "centimeters"
        self.assertFalse(normalize_and_validate(raw)["valid"])

    def test_stability_rejects_fixed_or_externally_forced_bodies(self):
        base = json.loads((ROOT / "examples" / "three_body.json").read_text())
        base["queries"] = [{"id": "stable", "type": "nbody_stability", "max_radius": 3, "min_separation": 0.01}]
        fixed = copy.deepcopy(base)
        fixed["entities"][0]["fixed"] = True
        self.assertFalse(normalize_and_validate(fixed)["valid"])
        forced = copy.deepcopy(base)
        forced["force_fields"] = [{"id": "push", "type": "uniform", "targets": ["body-a"],
                                   "start_time": 0, "end_time": 1, "acceleration": [1, 0, 0]}]
        self.assertFalse(normalize_and_validate(forced)["valid"])

    def test_dwell_reset_and_no_interpolation_manufactured_hold(self):
        query = {"operator": "gte", "value": 0.0, "hold_for": 0.4}
        answer = _crossing(query, [0, 0.25, 0.5, 0.75, 1], [-1, 1, -1, 1, 1])
        self.assertEqual(answer["status"], "not_observed")
        # Interpolated onset .05 followed by final sample .1 cannot certify .04s dwell.
        query["hold_for"] = 0.04
        self.assertEqual(_crossing(query, [0, 0.1], [-1, 1])["status"], "not_observed")

    def test_initial_and_lte_threshold_semantics(self):
        query = {"operator": "lte", "value": 1.0, "hold_for": 0}
        answer = _crossing(query, [0, 0.1, 0.2], [2, 1.5, 0.5])
        self.assertEqual(answer["status"], "reached")
        self.assertAlmostEqual(answer["time_s"], 0.15)
        self.assertEqual(answer["time_bracket_s"], [0.1, 0.2])
        query["value"] = 2
        self.assertEqual(_crossing(query, [0, 0.1], [2, 1])["status"], "initially_satisfied")

    def test_whole_window_energy_and_momentum_spikes_are_inconclusive(self):
        for column, spike in (("energy", -1.1), ("momentum", [1e-3, 0, 0])):
            with self.subTest(column=column):
                scene, plan, trajectory = _stability()
                trajectory["observations"]["nbody"]["stable"][column][2] = spike
                answer = _answer(scene, plan, trajectory)
                self.assertEqual(answer["status"], "inconclusive")
                self.assertFalse(answer["numerical_integrity_passed"])
                self.assertFalse(answer["long_term_stability_proven"])

    def test_conservation_does_not_imply_stability(self):
        scene, plan, trajectory = _stability()
        trajectory["observations"]["nbody"]["stable"]["max_com_radius"][2] = 4.0
        answer = _answer(scene, plan, trajectory)
        self.assertEqual(answer["status"], "criteria_violated")
        self.assertEqual(answer["first_violation_observed_s"], 0.01)
        self.assertFalse(answer["long_term_stability_proven"])

    def test_even_success_only_claims_sampled_finite_window(self):
        scene, plan, trajectory = _stability()
        answer = _answer(scene, plan, trajectory)
        self.assertEqual(answer["status"], "criteria_satisfied")
        self.assertFalse(answer["long_term_stability_proven"])
        self.assertIn("sampled", answer["interpretation"])
        self.assertEqual(answer["window_s"], [0, 0.02])

    def test_missing_or_decimated_solver_samples_are_rejected(self):
        scene, plan, trajectory = _collected()
        for stride in (2, 3):
            decimated = copy.deepcopy(trajectory)
            observations = decimated["observations"]
            observations["times"] = observations["times"][::stride]
            observations["columns"] = {key: values[::stride] for key, values in observations["columns"].items()}
            with self.assertRaises(ValueError):
                validate_observations(scene, plan, decimated)

    def test_observation_type_ids_source_and_finiteness_are_bound(self):
        scene, plan, trajectory = _collected()
        for field, value in (("version", True), ("source", "video_frames"), ("times", [True])):
            invalid = copy.deepcopy(trajectory)
            invalid["observations"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_observations(scene, plan, invalid)
        for value in (True, float("nan"), float("inf")):
            invalid = copy.deepcopy(trajectory)
            invalid["observations"]["columns"]["gap-history"][1] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_observations(scene, plan, invalid)
        invalid = copy.deepcopy(trajectory)
        invalid["observations"]["columns"]["undeclared"] = [0] * len(invalid["observations"]["times"])
        with self.assertRaises(ValueError):
            validate_observations(scene, plan, invalid)

    def test_incomplete_measurement_never_reports_success(self):
        scene, plan, trajectory = _collected()
        trajectory["diagnostics"]["completed"] = False
        answers = evaluate(scene, plan, trajectory, "audit", "hash")["answers"]
        self.assertTrue(all(answer["status"] == "inconclusive" for answer in answers))
        self.assertTrue(all(answer.get("time_s") is None for answer in answers))

    def test_linear_separation_answer_matches_dt_refinement_and_fps_change(self):
        answers = []
        counts = []
        for dt, fps in ((0.01, 10), (0.0025, 60)):
            raw = _raw_slider()
            raw["world"].update(dt=dt, output_fps=fps)
            scene = _normal(raw)
            plan = make_plan(scene, include_video=False)
            trajectory = run_scene(scene, plan, time.monotonic() + 5)
            response = evaluate(scene, plan, trajectory, "audit", "hash")
            answer = next(item for item in response["answers"] if item["id"] == "separate-1m")
            answers.append(answer)
            counts.append(len(trajectory["frames"]))
            self.assertAlmostEqual(answer["time_s"], 2.0, places=10)
            self.assertLessEqual(answer["time_bracket_s"][1] - answer["time_bracket_s"][0], dt + 1e-12)
        self.assertNotEqual(counts[0], counts[1])
        self.assertAlmostEqual(answers[0]["time_s"], answers[1]["time_s"], places=10)

    def test_retrieval_rejects_undeclared_queries_and_returns_raw_series(self):
        with tempfile.TemporaryDirectory() as output:
            self.assertTrue(simulate(_raw_slider(), output, make_video=False)["ok"])
            unknown = query(output, "invented-query")
            self.assertFalse(unknown["ok"])
            self.assertEqual(unknown["errors"][0]["code"], "query_not_declared")
            history = query(output, "gap-history")
            self.assertTrue(history["ok"])
            self.assertEqual(len(history["series"]["times_s"]), 301)
            self.assertAlmostEqual(history["series"]["values"][-1], 1.5, places=12)
        raw = _raw_slider()
        raw.pop("queries")
        with tempfile.TemporaryDirectory() as output:
            self.assertTrue(simulate(raw, output, make_video=False)["ok"])
            self.assertEqual(query(output)["errors"][0]["code"], "queries_not_recorded")

    def test_persisted_answer_and_observation_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as output:
            self.assertTrue(simulate(_raw_slider(), output, make_video=False)["ok"])
            root = Path(output)
            measurement_path, result_path = root / "measurements.json", root / "result.json"
            measurements = json.loads(measurement_path.read_text())
            result = json.loads(result_path.read_text())

            forged_measurements = copy.deepcopy(measurements)
            forged_measurements["answers"][1]["time_s"] = 0.01
            forged_result = copy.deepcopy(result)
            forged_result["measurements_sha256"] = digest(forged_measurements)
            measurement_path.write_text(json.dumps(forged_measurements))
            result_path.write_text(json.dumps(forged_result))
            self.assertFalse(query(output, "separate-1m")["ok"])

            measurement_path.write_text(json.dumps(measurements))
            forged_result = copy.deepcopy(result)
            forged_result["trajectory"]["observations"]["columns"]["gap-history"][100] = 123
            result_path.write_text(json.dumps(forged_result))
            self.assertFalse(query(output, "gap-history")["ok"])

            result_path.write_text(json.dumps(result))
            measurement_path.unlink()
            self.assertFalse(query(output)["ok"])

    def test_slider_roundoff_tail_has_no_fake_observation_step(self):
        raw = _raw_slider()
        raw["world"].update(duration=0.07, dt=0.01)
        with tempfile.TemporaryDirectory() as output:
            response = simulate(raw, output, make_video=False)
            self.assertTrue(response["ok"], response)
            history = query(output, "gap-history")
            self.assertEqual(len(history["series"]["times_s"]), 8)
            self.assertEqual(history["series"]["times_s"][-1], 0.07)


if __name__ == "__main__":
    unittest.main()
