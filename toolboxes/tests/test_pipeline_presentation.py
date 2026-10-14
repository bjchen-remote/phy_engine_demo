"""Saved-data integrity, deterministic exports and independently rerendered video."""

import csv
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "toolboxes"))
engine = Path(os.environ.get("PHYSICS_PIPELINE_PHYSICS_ROOT", str(ROOT / "toolboxes/physics")))
sys.path[:0] = [str(engine / "physics_release"), str(engine)]

from pipeline_presentation import PresentationError, export_data, render_simulation
from pipeline_presentation.artifacts import canonical, digest
from pipeline_presentation.providers import provider_request, validate_provider_receipt
from physics_demo.runner import load_scene, simulate
from pcb_thermal import simulate_pcb_thermal, validate_pcb_spec


class PresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.root = Path(cls.folder.name)
        cls.source = cls.root / "source"
        scene = load_scene(ROOT / "examples" / "three_body.json")
        scene["world"].update(duration=.1, dt=.005, output_fps=20)
        scene["budget"]["validation"] = "visual"
        scene["queries"] = [{"id": "x", "type": "series", "metric": {
            "type": "centroid", "entity": scene["entities"][0]["id"], "axis": "x"}}]
        checked = simulate(scene, cls.source, 30, make_video=False)
        if not checked["ok"]:
            raise AssertionError(checked)
        cls.result = cls.source / "result.json"
        cls.model = cls.source / "scene.normalized.json"

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def hashes(self):
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.source.iterdir() if path.is_file()}

    def stage_source(self, root):
        source = root / "source"
        source.mkdir()
        for path in self.source.iterdir():
            if path.is_file():
                (source / path.name).write_bytes(path.read_bytes())
        (source / "model.json").write_bytes(self.model.read_bytes())
        result = json.loads((source / "result.json").read_text())
        result["artifacts"]["measurements"] = str(source / "measurements.json")
        (source / "result.json").write_text(json.dumps(result))
        roles = {"result.json": "canonical_result", "model.json": "canonical_model",
                 "scene.normalized.json": "solver_model", "plan.json": "solver_plan",
                 "summary.json": "verification", "completion.json": "completion",
                 "measurements.json": "solver_observations"}
        artifacts = [{"path": name, "role": role, "media_type": "application/json",
                      "bytes": (source / name).stat().st_size, "sha256": digest((source / name).read_bytes())}
                     for name, role in roles.items() if (source / name).exists()]
        refs = {ref["path"]: ref for ref in artifacts}
        manifest = {"schema_version": "pipeline-simulation/1", "domain": "physics", "ok": True,
                    "model": refs["model.json"], "result": refs["result.json"],
                    "data_usable": True, "data": {"numerical_usable": True},
                    "quality_gate": {"passed": True, "numerical_passed": True}, "artifacts": artifacts}
        (source / "simulation-manifest.json").write_bytes(canonical(manifest))
        return source, manifest

    def test_export_is_repeatable_and_csv_preserves_solver_observations(self):
        before = self.hashes()
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            one = export_data(self.result, self.model, first)
            two = export_data(self.result, self.model, second)
            self.assertEqual(one["sha256"], two["sha256"])
            self.assertEqual(Path(one["path"]).read_bytes(), Path(two["path"]).read_bytes())
            self.assertTrue(one["quantitative_usable"])
            with zipfile.ZipFile(one["path"]) as archive:
                self.assertIsNone(archive.testzip())
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(digest(archive.read("result.json")), before["result.json"])
                self.assertEqual(digest(archive.read("model.json")), before["scene.normalized.json"])
                for member in manifest["members"]:
                    payload = archive.read(member["path"])
                    self.assertEqual(digest(payload), member["sha256"])
                    self.assertEqual(len(payload), member["bytes"])
                measurements = json.loads(archive.read("measurements.json"))
                rows = list(csv.DictReader(io.StringIO(archive.read("observations.csv").decode())))
                self.assertEqual([float(row["time_s"]) for row in rows], measurements["observations"]["times"])
                self.assertEqual([float(row["value"]) for row in rows], measurements["observations"]["columns"]["x"])
                self.assertTrue(all(row["quantitative_usable"] == "True" for row in rows))
                display = list(csv.DictReader(io.StringIO(archive.read("display-frames.csv").decode())))
                self.assertTrue(all(row["quantitative_usable"] == "False" for row in display))
                dictionary = json.loads(archive.read("data-dictionary.json"))
                self.assertIn("rounded", dictionary["sampling"]["display_frames"]["precision"])
        self.assertEqual(before, self.hashes())

    def test_failed_numerical_gate_labels_data_diagnostic(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            document = json.loads(self.result.read_text())
            document["trajectory"]["diagnostics"].update(nbody_relative_energy_drift=.03, nbody_invariants_conserved=False)
            document["artifacts"]["measurements"] = str(source / "measurements.json")
            (source / "result.json").write_text(json.dumps(document))
            (source / "model.json").write_bytes(self.model.read_bytes())
            (source / "measurements.json").write_bytes((self.source / "measurements.json").read_bytes())
            exported = export_data(source / "result.json", source / "model.json", root / "delivery")
            self.assertFalse(exported["quantitative_usable"])
            with zipfile.ZipFile(exported["path"]) as archive:
                quality = json.loads(archive.read("quality.json"))
                self.assertFalse(quality["quantitative_usable"])
                self.assertTrue(any("diagnostic" in value for value in quality["warnings"]))
                rows = csv.DictReader(io.StringIO(archive.read("observations.csv").decode()))
                self.assertTrue(all(row["quantitative_usable"] == "False" for row in rows))

    def test_upstream_stage_failure_cannot_be_promoted_by_persisted_result(self):
        for field in ("numerical_passed", "data_usable"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source, manifest = self.stage_source(root)
                if field == "numerical_passed":
                    manifest["quality_gate"][field] = False
                else:
                    manifest[field] = False
                manifest_path = source / "simulation-manifest.json"
                manifest_path.write_bytes(canonical(manifest))
                exported = export_data(source / "result.json", source / "model.json", root / "data")
                self.assertFalse(exported["quantitative_usable"])
                with zipfile.ZipFile(exported["path"]) as archive:
                    rows = csv.DictReader(io.StringIO(archive.read("observations.csv").decode()))
                    self.assertTrue(all(row["quantitative_usable"] == "False" for row in rows))
                    included = json.loads(archive.read("source-stage/simulation-manifest.json"))
                    self.assertEqual(included, manifest)
                    self.assertEqual(digest(archive.read("source-stage/simulation-manifest.json")), digest(manifest_path.read_bytes()))
                    for ref in included["artifacts"]:
                        self.assertEqual(digest(archive.read("source-stage/" + ref["path"])), ref["sha256"])
                    self.assertIn("source-stage/plan.json", archive.namelist())
                    self.assertIn("source-stage/completion.json", archive.namelist())
                if field == "numerical_passed":
                    rendered = render_simulation(source / "result.json", source / "model.json", root / "video", budget_seconds=60)
                    self.assertFalse(rendered["numerical_usable"])
                    self.assertFalse(rendered["quality_gate"]["simulation_stage"]["quality_gate"]["numerical_passed"])

    def test_simulation_manifest_tampering_and_path_escape_are_rejected(self):
        for tamper in ("result_hash", "model_hash", "escape", "domain", "schema", "missing_artifact", "manifest_symlink"):
            with self.subTest(tamper=tamper), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source, manifest = self.stage_source(root)
                if tamper == "result_hash":
                    manifest["result"]["sha256"] = "0" * 64
                elif tamper == "model_hash":
                    manifest["model"]["sha256"] = "0" * 64
                elif tamper == "escape":
                    manifest["model"]["path"] = "../model.json"
                elif tamper == "domain":
                    manifest["domain"] = "pcb_thermal"
                elif tamper == "schema":
                    manifest["schema_version"] = "unverified"
                elif tamper == "missing_artifact":
                    (source / "plan.json").unlink()
                manifest_path = source / "simulation-manifest.json"
                manifest_path.write_bytes(canonical(manifest))
                if tamper == "manifest_symlink":
                    other = root / "other.json"
                    other.write_bytes(manifest_path.read_bytes())
                    manifest_path.unlink()
                    manifest_path.symlink_to(other)
                with self.assertRaises(PresentationError):
                    export_data(source / "result.json", source / "model.json", root / "data")
                self.assertFalse((root / "data/data.zip").exists())

    def test_bound_model_and_source_directory_are_enforced(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / "model.json"
            scene = json.loads(self.model.read_text())
            scene["name"] = "different model"
            model.write_bytes(canonical({"schema": "pipeline-model/1", "model": scene, "domain": "physics"}))
            with self.assertRaisesRegex(PresentationError, "bound"):
                export_data(self.result, model, Path(folder) / "delivery")
            scene = json.loads(self.model.read_text())
            model.write_bytes(canonical({"schema": "pipeline-model/1", "model": scene, "domain": "physics"}))
            exported = export_data(self.result, model, Path(folder) / "delivery")
            self.assertTrue(Path(exported["path"]).is_file())
            with self.assertRaisesRegex(PresentationError, "differ"):
                export_data(self.result, model, self.source)

    def test_tiny_budget_and_unconfigured_deep_do_not_publish(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaises(PresentationError):
                export_data(self.result, self.model, root, max_bytes=1024)
            self.assertFalse((root / "data.zip").exists())
            with self.assertRaises(PresentationError) as error:
                render_simulation(self.result, self.model, root, quality="deep")
            self.assertEqual(error.exception.code, "unsupported_render_quality")
            self.assertFalse((root / "simulation.mp4").exists())

    def test_compressed_fallback_retains_large_pinned_source_bytes_and_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, stage = self.stage_source(root)
            # Valid optional metadata enlarges both saved inputs without changing
            # the normalized physics scene or its recorded numerical evidence.
            metadata = "repeated saved provenance metadata; " * (9 * 1024 * 1024 // 35 + 1)
            model = json.loads((source / "model.json").read_text())
            (source / "model.json").write_bytes(json.dumps({
                "schema_version": "pipeline-model/1", "domain": "physics", "model": model,
                "source_fixture_metadata": metadata}, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            result = json.loads((source / "result.json").read_text())
            result["source_fixture_metadata"] = metadata
            (source / "result.json").write_bytes(json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8"))
            for ref in stage["artifacts"]:
                path = source / ref["path"]
                ref.update(bytes=path.stat().st_size, sha256=digest(path.read_bytes()))
            (source / "simulation-manifest.json").write_bytes(canonical(stage))
            expected_model = (source / "model.json").read_bytes()
            expected_result = (source / "result.json").read_bytes()
            before = {path.name: digest(path.read_bytes()) for path in source.iterdir() if path.is_file()}
            budget = 16 * 1024 * 1024
            self.assertGreater(len(expected_model) + len(expected_result), budget)
            with patch("physics_demo.runner.simulate", side_effect=AssertionError("export must not rerun solver")):
                exported = export_data(source / "result.json", source / "model.json", root / "delivery",
                                       max_bytes=budget)
            self.assertLessEqual(exported["bytes"], budget)
            with zipfile.ZipFile(exported["path"]) as archive:
                self.assertIsNone(archive.testzip())
                self.assertGreater(sum(item.file_size for item in archive.infolist()), budget)
                self.assertTrue(all(item.compress_type == zipfile.ZIP_DEFLATED for item in archive.infolist()))
                self.assertEqual(archive.read("model.json"), expected_model)
                self.assertEqual(archive.read("result.json"), expected_result)
                self.assertEqual(archive.read("source-stage/model.json"), expected_model)
                self.assertEqual(archive.read("source-stage/result.json"), expected_result)
                manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(manifest["source_model_sha256"], digest(expected_model))
                self.assertEqual(manifest["source_result_sha256"], digest(expected_result))
                for member in manifest["members"]:
                    data = archive.read(member["path"])
                    self.assertEqual(len(data), member["bytes"])
                    self.assertEqual(digest(data), member["sha256"])
            after = {path.name: digest(path.read_bytes()) for path in source.iterdir() if path.is_file()}
            self.assertEqual(after, before)

    def test_inert_display_glb_and_display_physics_obj_are_exported_byte_exactly(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            assets = root / "modeling-assets"
            assets.mkdir()
            # A minimal valid GLB document and two distinct triangle OBJ assets
            # stand in for the already audited backend artifacts. The exporter
            # must retain bytes without reimporting, extracting or executing them.
            glb_json = canonical({"asset": {"version": "2.0"}, "scene": 0,
                                  "scenes": [{"nodes": [0]}], "nodes": [{"name": "shape fixture"}]})
            glb_json += b" " * ((-len(glb_json)) % 4)
            glb = struct.pack("<4sII", b"glTF", 2, 20 + len(glb_json))
            glb += struct.pack("<I4s", len(glb_json), b"JSON") + glb_json
            display_obj = (b"# preserved display geometry\nv 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\n"
                           b"f 1 3 2\nf 1 2 4\nf 1 4 3\nf 2 3 4\n")
            physics_obj = display_obj.replace(b"display", b"bounded physics").replace(b"v 1 0 0", b"v 0.2 0 0")
            payloads = {"display.glb": glb, "display.obj": display_obj, "simulation.obj": physics_obj}
            inventory = []
            for name, payload in payloads.items():
                path = assets / name
                path.write_bytes(payload)
                inventory.append({"path": str(path), "name": "modeling/shape/" + name,
                                  "sha256": digest(payload)})
            with patch("physics_demo.runner.simulate", side_effect=AssertionError("no solver")), \
                    patch("zipfile.ZipFile.extractall", side_effect=AssertionError("no extraction")):
                exported = export_data(self.result, self.model, root / "delivery", modeling_assets=inventory)
            with zipfile.ZipFile(exported["path"]) as archive:
                self.assertIsNone(archive.testzip())
                manifest = json.loads(archive.read("manifest.json"))
                pins = {item["path"]: item for item in manifest["members"]}
                for name, payload in payloads.items():
                    member = "modeling/shape/" + name
                    self.assertEqual(archive.read(member), payload)
                    self.assertEqual(pins[member]["sha256"], digest(payload))
                    self.assertEqual(pins[member]["bytes"], len(payload))
            for name, payload in payloads.items():
                self.assertEqual((assets / name).read_bytes(), payload)

    def test_corrupted_and_oversized_modeling_assets_never_publish_delivery(self):
        cases = ("corrupted_glb", "corrupted_obj", "oversized_glb", "oversized_obj")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as folder:
                root = Path(folder).resolve()
                extension = ".glb" if case.endswith("glb") else ".obj"
                asset = root / ("shape" + extension)
                original = b"pinned modeling bytes"
                asset.write_bytes(original)
                pin = {"path": str(asset), "name": "modeling/shape" + extension,
                       "sha256": digest(original)}
                if case.startswith("corrupted"):
                    asset.write_bytes(b"changed after receipt")
                else:
                    # Sparse file exceeds the absolute 128 MiB expanded cap;
                    # a bounded exporter must reject it before loading its bytes.
                    with asset.open("r+b") as handle:
                        handle.truncate(128 * 1024 * 1024 + 1)
                with self.assertRaises(PresentationError):
                    export_data(self.result, self.model, root / "delivery", modeling_assets=[pin])
                self.assertFalse((root / "delivery/data.zip").exists())
                self.assertFalse((root / "delivery/data-manifest.json").exists())

    def test_missing_queries_never_promote_display_frames_to_measurements(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            scene = load_scene(ROOT / "examples" / "three_body.json")
            scene["world"].update(duration=.03, dt=.005)
            self.assertTrue(simulate(scene, root / "source", 30, make_video=False)["ok"])
            exported = export_data(root / "source/result.json", root / "source/scene.normalized.json", root / "data")
            with zipfile.ZipFile(exported["path"]) as archive:
                observations = list(csv.DictReader(io.StringIO(archive.read("observations.csv").decode())))
                self.assertEqual(observations, [])
                measurements = json.loads(archive.read("measurements.json"))
                self.assertEqual(measurements["status"], "unavailable")
                self.assertIsNone(measurements["observations"])

    def test_tampered_measurements_are_rejected_without_publishing(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            document = json.loads(self.result.read_text())
            document["artifacts"]["measurements"] = str(source / "measurements.json")
            (source / "result.json").write_text(json.dumps(document))
            (source / "model.json").write_bytes(self.model.read_bytes())
            measurements = json.loads((self.source / "measurements.json").read_text())
            measurements["observations"]["columns"]["x"][0] += 1000
            (source / "measurements.json").write_text(json.dumps(measurements))
            with self.assertRaises(PresentationError):
                export_data(source / "result.json", source / "model.json", root / "data")
            self.assertFalse((root / "data/data.zip").exists())

    def test_real_rerender_decodes_without_calling_solver_or_changing_sources(self):
        before = self.hashes()
        with tempfile.TemporaryDirectory() as folder, patch("physics_demo.runner.simulate", side_effect=AssertionError("solver rerun")):
            rendered = render_simulation(self.result, self.model, folder, budget_seconds=60)
            self.assertFalse(rendered["solver_rerun"])
            self.assertFalse(rendered["measurement_source"])
            self.assertTrue(rendered["media"]["all_frames_decoded"])
            self.assertEqual(rendered["media"]["frame_count"], 90)
            self.assertEqual(rendered["media"]["duration_s"], 3)
            self.assertEqual(rendered["source_result_sha256"], before["result.json"])
            self.assertEqual(digest(Path(rendered["path"]).read_bytes()), rendered["sha256"])
        self.assertEqual(before, self.hashes())

    def test_pcb_data_preserves_grid_coordinates_and_snapshot_values(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            model = json.loads((ROOT / "toolboxes/physics/pcb_thermal_demo.json").read_text())
            model["grid"] = {"nx": 6, "ny": 4}
            model["transient"].update(duration_s=4, time_step_s=1)
            model = validate_pcb_spec(model)
            result = simulate_pcb_thermal(model)
            (source / "model.json").write_bytes(canonical(model))
            (source / "result.json").write_bytes(canonical(result))
            exported = export_data(source / "result.json", source / "model.json", root / "delivery", domain="pcb_thermal")
            self.assertTrue(exported["quantitative_usable"])
            with zipfile.ZipFile(exported["path"]) as archive:
                rows = list(csv.DictReader(io.StringIO(archive.read("thermal-grid.csv").decode())))
                self.assertEqual(len(rows), len(result["snapshots"]) * 24)
                self.assertAlmostEqual(float(rows[0]["x_m"]), model["board"]["width_m"] / 12)
                self.assertEqual(float(rows[-1]["temperature_c"]), result["snapshots"][-1]["temperature_c"][-1][-1])
            before = digest((source / "result.json").read_bytes())
            rendered = render_simulation(source / "result.json", source / "model.json", root / "video",
                                         domain="pcb_thermal", budget_seconds=45)
            self.assertTrue(rendered["media"]["all_frames_decoded"])
            self.assertEqual(digest((source / "result.json").read_bytes()), before)


class ProviderContractTests(unittest.TestCase):
    def test_external_receipt_is_bound_but_never_quantitative(self):
        request = provider_request(provider="commercial-demo", source_result_sha256="a" * 64,
                                   reference_video_sha256="b" * 64, prompt="Warm cinematic lighting",
                                   upload_allowed=True, max_cost_usd=1.5)
        receipt = {"provider": request["provider"], "request_sha256": request["request_sha256"],
                   "sha256": "c" * 64, "bytes": 1234, "media_type": "video/mp4", "cost_usd": .5,
                   "measurement_source": True, "generative": False}
        checked = validate_provider_receipt(request, receipt)
        self.assertFalse(checked["measurement_source"])
        self.assertTrue(checked["generative"])
        self.assertEqual(checked["fidelity"], "illustrative_only")
        receipt["cost_usd"] = 2
        with self.assertRaises(PresentationError):
            validate_provider_receipt(request, receipt)
        with self.assertRaises(PresentationError):
            provider_request(provider="commercial-demo", source_result_sha256="a" * 64,
                             reference_video_sha256="b" * 64, prompt="Light", upload_allowed=False, max_cost_usd=1)


if __name__ == "__main__":
    unittest.main()
