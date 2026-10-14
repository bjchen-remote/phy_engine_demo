"""Data-stage integration against the existing solver, with no rendering."""
from __future__ import annotations

import copy
from contextlib import ExitStack
import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


TOOLBOXES = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLBOXES))

from pipeline_stages.common import digest, engine_module, file_digest, read_json
from pipeline_stages.modeling import build_model
from pipeline_stages.simulation import run_simulation


class PipelineDataStagesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pipeline-stages-")
        self.addCleanup(self.temporary.cleanup)
        self.job = Path(self.temporary.name)
        self.physics = Path(os.environ.get("PHYSICS_PIPELINE_PHYSICS_ROOT", str(TOOLBOXES / "physics")))
        # Production workers have separate processes. Discovery loads other
        # engine versions first; scope imports to this fixture and restore them.
        context = ExitStack()
        self.addCleanup(context.close)
        context.enter_context(patch.dict(sys.modules))
        for name in list(sys.modules):
            if name == 'physics_demo' or name.startswith('physics_demo.') or name == 'pcb_thermal':
                del sys.modules[name]
        context.enter_context(patch.object(sys, 'path', [str(self.physics / 'physics_release'),
            str(self.physics), *sys.path]))
        context.enter_context(patch.dict(os.environ,
            {'PHYSICS_PIPELINE_PHYSICS_ROOT': str(self.physics.resolve())}))

    def scene(self):
        scene = read_json(self.physics / "physics_release/scenes/three_body.json")
        scene["world"].update(duration=0.1, dt=0.005, output_fps=10)
        scene["budget"].update(backend="python", validation="strict", wall_time_s=30)
        scene["queries"] = [{"id": "=position", "type": "series",
                             "metric": {"type": "centroid", "entity": "body-a", "axis": "x"}}]
        return scene

    def pcb(self):
        model = read_json(self.physics / "pcb_thermal_demo.json")
        model["grid"] = {"nx": 12, "ny": 10}
        model["transient"] = {"duration_s": 2, "time_step_s": 0.5,
                              "initial_c": 25, "snapshot_count": 3}
        return model

    def test_mechanical_uses_real_solver_without_video_and_exports_macro_steps(self):
        scene = self.scene()
        original = copy.deepcopy(scene)
        built = build_model("physics", scene, self.job / "model", budget_seconds=30)
        self.assertTrue(built["ok"], built)
        self.assertEqual(scene, original)
        runner = engine_module("physics_demo.runner")
        with patch.object(runner, "encode_mp4", side_effect=AssertionError("No renderer may run")):
            result = run_simulation(built["bundle_path"], self.job / "run", budget_seconds=30)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["data_usable"], result)
        self.assertFalse(list((self.job / "run").glob("*.mp4")))
        self.assertFalse(read_json(result["result_path"])["video_required"])
        manifest = read_json(result["manifest_path"])
        self.assertFalse(manifest["data"]["full_state"])
        self.assertEqual(manifest["data"]["capability"], "sampled_state")
        self.assertGreater(manifest["data"]["observations"]["sample_count"],
                           manifest["data"]["sample_count"])
        for item in manifest["artifacts"]:
            path = Path(result["manifest_path"]).parent / item["path"]
            self.assertEqual(item["sha256"], file_digest(path))
        with (self.job / "run/measurements.csv").open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["query_id"], "'=position")
        self.assertEqual(rows[0]["source"], "solver_macro_steps")
        self.assertEqual(len(rows), 21)
        self.assertAlmostEqual(float(rows[-1]["time_s"]), 0.1)
        # The original inspect and query paths remain valid after metadata is added.
        self.assertTrue(runner.inspect(result["result_path"])["ok"])
        self.assertTrue(runner.query(result["result_path"], "=position")["ok"])

    def test_pcb_exports_verified_field_and_time_series(self):
        built = build_model("pcb_thermal", self.pcb(), self.job / "model", budget_seconds=30)
        self.assertTrue(built["ok"], built)
        result = run_simulation(built["bundle_path"], self.job / "run", budget_seconds=30)
        self.assertTrue(result["ok"] and result["data_usable"], result)
        raw = read_json(result["result_path"])
        self.assertTrue(raw["energy_balance"]["passed"])
        self.assertAlmostEqual(raw["total_power_w"], 3.3)
        self.assertEqual(result["data"]["observations"]["sample_count"], 5)
        with (self.job / "run/temperature-field.csv").open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 120)
        self.assertAlmostEqual(float(rows[0]["temperature_c"]), raw["temperature_c"][0][0])
        self.assertEqual(result["data"]["field"]["sampling"], "cell_centers")

    def test_modified_model_is_rejected_before_solver_runs(self):
        built = build_model("physics", self.scene(), self.job / "model", budget_seconds=30)
        bundle = read_json(built["bundle_path"])
        bundle["model"]["world"]["duration"] = 0.2
        Path(built["bundle_path"]).write_text(json.dumps(bundle))
        result = run_simulation(built["bundle_path"], self.job / "run", budget_seconds=30)
        self.assertEqual(result["code"], "model_digest_mismatch")
        self.assertFalse((self.job / "run").exists())

    def test_engine_pin_mismatch_is_rejected(self):
        built = build_model("physics", self.scene(), self.job / "model", budget_seconds=30)
        bundle = read_json(built["bundle_path"])
        bundle["source_engine"]["version"] = "unverified"
        Path(built["bundle_path"]).write_text(json.dumps(bundle))
        result = run_simulation(built["bundle_path"], self.job / "run", budget_seconds=30)
        self.assertEqual(result["code"], "engine_pin_mismatch")

    def test_budget_override_cannot_change_the_prepared_model(self):
        built = build_model("physics", self.scene(), self.job / "model", budget_seconds=30)
        result = run_simulation(built["bundle_path"], self.job / "run", budget_seconds=60)
        self.assertEqual(result["code"], "execution_limits_mismatch")
        self.assertFalse((self.job / "run").exists())

    def test_high_quality_is_explicitly_unsupported_and_cannot_silently_downgrade(self):
        result = build_model("physics", self.scene(), self.job / "model", quality="high")
        self.assertEqual(result["code"], "unsupported_modeling_quality")
        self.assertFalse((self.job / "model").exists())

    def test_stage_outputs_are_immutable(self):
        built = build_model("physics", self.scene(), self.job / "model", budget_seconds=30)
        before = file_digest(Path(built["bundle_path"]))
        with self.assertRaisesRegex(ValueError, "new or empty"):
            build_model("physics", self.scene(), self.job / "model", budget_seconds=30)
        self.assertEqual(before, file_digest(Path(built["bundle_path"])))

    def test_bad_pcb_input_does_not_leave_prepared_bundle(self):
        result = build_model("pcb_thermal", {}, self.job / "model")
        self.assertEqual(result["code"], "invalid_pcb_model")
        self.assertFalse((self.job / "model").exists())

    def test_visual_pass_never_claims_quantitative_usability(self):
        built = build_model("physics", self.scene(), self.job / "model", budget_seconds=30)
        runner = engine_module("physics_demo.runner")
        simulate = runner.simulate
        def advisory(*args, **kwargs):
            summary = simulate(*args, **kwargs)
            self.assertTrue(summary["ok"], summary)
            summary["quality_gate"]["numerical_passed"] = False
            return summary
        with patch.object(runner, "simulate", side_effect=advisory):
            result = run_simulation(built["bundle_path"], self.job / "run", budget_seconds=30)
        self.assertTrue(result["ok"])
        self.assertFalse(result["data_usable"])
        self.assertFalse((self.job / "run/measurements.csv").exists())
        self.assertTrue((self.job / "run/measurements.json").is_file())

    def test_reject_nonfinite_budget_before_any_run(self):
        with self.assertRaisesRegex(ValueError, "finite and positive"):
            build_model("physics", self.scene(), self.job / "model", budget_seconds=float("nan"))

    def test_host_unlimited_mode_preserves_query_and_gate_contracts(self):
        built = build_model("physics", self.scene(), self.job / "model", unlimited=True)
        self.assertTrue(built["ok"], built)
        self.assertTrue(read_json(built["bundle_path"])["model"]["budget"]["unlimited_runtime"])
        result = run_simulation(built["bundle_path"], self.job / "run", unlimited=True)
        self.assertTrue(result["ok"] and result["data_usable"], result)


if __name__ == "__main__":
    unittest.main()
