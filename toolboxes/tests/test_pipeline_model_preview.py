"""Independent package/export checks using explicit real triangle fixtures.

Native rendering, stage subprocesses, ZIP contents and component composition
are exercised. No neural inference, public download or QQ delivery is claimed.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from build_pipeline import build, build_module
from pipeline import bundle
from modeling_flow.meshes import write_obj


ROOT = Path(__file__).resolve().parents[1]
NATIVE_AVAILABLE = (sys.platform == "darwin" and shutil.which("clang")
                    and all(importlib.util.find_spec(name) for name in ("numpy", "trimesh", "PIL"))
                    and (shutil.which("ffmpeg") or Path("/opt/homebrew/bin/ffmpeg").is_file()))


def write(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False), encoding="utf-8")


def load_adapter():
    spec = importlib.util.spec_from_file_location("_preview_export_adapter_test", ROOT / "pipeline/adapter.py")
    adapter = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"bundle": bundle}):
        spec.loader.exec_module(adapter)
    return adapter


ADAPTER = load_adapter()
_spec = importlib.util.spec_from_file_location("_preview_export_test", ROOT / "pipeline/model_preview.py")
EXPORT = importlib.util.module_from_spec(_spec)
with mock.patch.dict(sys.modules, {"bundle": bundle}):
    _spec.loader.exec_module(EXPORT)


class PreviewModuleCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.engine = self.root / "engine-contract-fixture"
        (self.engine / "physics_release/physics_demo").mkdir(parents=True)
        (self.engine / "physics_release/physics_demo/runner.py").write_text("# explicit composition fixture\n")
        (self.engine / "toolbox_adapter.py").write_text("# explicit composition fixture\n")
        write(self.engine / "toolbox.json", {"schema_version": 1, "id": "physics", "version": "1.4.7",
              "entrypoint": "toolbox_adapter.py", "capabilities": ["explicit composition test fixture"],
              "agent_api": {"operations": ["physics_prepare", "physics_simulate"]}})

    def test_independent_legacy_role_disables_only_preview_operations(self):
        for role in ("modeling", "rendering"):
            with self.subTest(role=role):
                legacy = self.root / ("legacy-" + role)
                metadata = build_module(role, legacy)
                # A capability declaration is the compatibility boundary. This
                # explicit fixture has the older role contract, no preview bit.
                metadata.pop("model_preview")
                metadata["version"] = "0.2.0"
                write(legacy / "module.json", metadata)
                toolbox = bundle.read_json(legacy / "toolbox.json")
                write(legacy / "toolbox.json", {**toolbox, "version": "0.2.0"})
                package = self.root / ("mixed-" + role)
                lock = build(self.engine, package, {role: legacy})
                self.assertEqual(bundle.verify_bundle(package), lock)
                capabilities = ADAPTER.capabilities(lock, {"provider_notice": "explicit test fixture"})
                self.assertFalse(capabilities["model_preview"]["available"])
                self.assertTrue(capabilities["image_modeling"]["configured"])
                operations = bundle.read_json(package / "toolbox.json")["agent_api"]["operations"]
                self.assertIn("physics_prepare", operations)
                self.assertIn("physics_simulate", operations)
                self.assertIn("modeling_from_image", operations)
                self.assertNotIn("modeling_preview_from_image", operations)
                self.assertNotIn("modeling_preview_render", operations)

    def test_lock_cannot_invent_preview_capability(self):
        package = self.root / "modern"
        lock = build(self.engine, package)
        self.assertTrue(ADAPTER.capabilities(lock, {})["model_preview"]["available"])
        lock["modules"]["rendering"].pop("model_preview")
        write(package / "bundle-lock.json", lock)
        with self.assertRaisesRegex(ValueError, "preview capability mismatch"):
            bundle.verify_bundle(package)


@unittest.skipUnless(NATIVE_AVAILABLE, "real native package/export checks need Mac runtime dependencies")
class NativePreviewPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package_temp = tempfile.TemporaryDirectory(prefix="native-preview-package-")
        cls.package = Path(cls.package_temp.name).resolve() / "package"
        cls.lock = build(ROOT / "physics", cls.package)

    @classmethod
    def tearDownClass(cls):
        cls.package_temp.cleanup()

    def setUp(self):
        import trimesh
        from PIL import Image
        self.temp = tempfile.TemporaryDirectory(prefix="native-preview-export-")
        self.addCleanup(self.temp.cleanup)
        self.job = Path(self.temp.name).resolve()
        (self.job / "inputs").mkdir()
        (self.job / "work").mkdir()
        self.image = self.job / "inputs/reference.png"
        Image.new("RGB", (32, 32), (120, 100, 180)).save(self.image)
        self.source_receipt = self.job / "inputs/reference-source.json"
        write(self.source_receipt, {"fixture": True, "source": "explicit real triangle test; no public download"})
        config = self.job / "work/modeling-runtime/config.json"
        write(config, {"fixture": True, "schema_version": "modeling-flow-config/1"})
        self.task = {"schema_version": 1, "task_id": "explicit-native-model-preview-test",
            "request": {"input_images": [{"id": "fixture-image", "path": "inputs/reference.png",
                "sha256": bundle.sha256(self.image), "source_receipt": {
                    "path": "inputs/reference-source.json", "sha256": bundle.sha256(self.source_receipt)}}]},
            "limits": {"wall_time_seconds": 60, "max_output_bytes": 16 * 1024 * 1024, "network": False},
            "delivery": {"data_attachments": True, "max_data_bytes": 16 * 1024 * 1024},
            "modeling_runtime": {"config_path": "work/modeling-runtime/config.json",
                "config_sha256": bundle.sha256(config), "provider_notice": "Explicit test provider; no Tencent affiliation."}}
        write(self.job / "toolbox-pin.json", {"id": "physics-pipeline", "version": "0.3.0",
                                               "digest": bundle.digest_tree(self.package)})
        self.vertices = [[-.8, -.5, -.5], [.3, -.5, -.5], [-.5, .8, -.3], [-.7, -.4, .9],
                         [1.1, .1, -.3], [1.7, .1, -.3], [1.2, .7, -.2], [1.15, .1, .4],
                         [-.7, -.5, -.5], [-.6, -.5, -.5]]
        tetra = [[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]]
        self.faces = tetra + [[index + 4 for index in face] for face in tetra] + [[0, 8, 9]]
        self.trimesh = trimesh

    def seal_model(self, seed=0):
        request = {"schema_version": "modeling-flow-preview-request/1", "image_path": str(self.image),
                   "scale_axis": "max", "seed": seed}
        reference = ADAPTER.object_hash({"request": request, "image_sha256": bundle.sha256(self.image),
            "runtime_sha256": self.task["modeling_runtime"]["config_sha256"], "module": self.lock["modules"]["modeling"]})
        directory = self.job / "work/modeling-images" / reference
        assets = directory / "assets"
        assets.mkdir(parents=True)
        write(directory / "request.json", request)
        mesh = {"schema_version": "modeling-flow-display/1", "units": "model_unit",
                "vertices": self.vertices, "faces": self.faces,
                "image_sha256": bundle.sha256(self.image), "physical_accuracy": "unverified",
                "fixture": "post-inference triangles, not neural inference"}
        write(assets / "display_mesh.json", mesh)
        write_obj(assets / "display.obj", {"vertices": self.vertices, "faces": self.faces}, "model_unit")
        self.trimesh.Trimesh(vertices=self.vertices, faces=self.faces, process=False).export(
            str(assets / "display.glb"), file_type="glb")
        receipt = {"schema_version": "modeling-flow-receipt/1", "purpose": "model_preview",
            "display_only": True, "simulation_performed": False, "numerical_usable": False,
            "configuration_file_sha256": self.task["modeling_runtime"]["config_sha256"],
            "image": {"sha256": bundle.sha256(self.image)}, "fixture": True,
            "artifacts": [{"path": path.name, "sha256": bundle.sha256(path), "bytes": path.stat().st_size}
                          for path in assets.iterdir()]}
        write(assets / "receipt.json", receipt)
        public = {"ok": True, "result_kind": "model-preview", "ready_to_preview": True,
                  "ready_to_simulate": False, "model_ref": reference}
        write(directory / "response.json", {"public_result": public, "artifact_pins": [
            {"path": path.relative_to(self.job).as_posix(), "sha256": bundle.sha256(path)} for path in assets.iterdir()]})
        return reference, assets

    def render(self, reference):
        with mock.patch.dict(sys.modules, {"bundle": bundle}), mock.patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": "1"}):
            return EXPORT.render_preview(self.package, self.package / "engine", self.job, self.task,
                self.lock, {"model_ref": reference, "width": 256, "height": 256, "fps": 6,
                            "duration_seconds": 1}, ADAPTER.atomic, ADAPTER.object_hash, ADAPTER.stage)

    def delivery(self):
        paths = list((self.job / "work/modeling-preview").glob("*/*/delivery.json"))
        self.assertEqual(len(paths), 1)
        return paths[0]

    def test_real_stage_exports_exact_mesh_reference_and_every_pinned_license(self):
        reference, assets = self.seal_model()
        before = {path.name: bundle.sha256(path) for path in assets.iterdir()}
        result = self.render(reference)
        self.assertEqual(result["model_ref"], reference)
        self.assertTrue(result["full_geometry_retained"])
        self.assertFalse(result["simulation_performed"])
        self.assertEqual(before, {path.name: bundle.sha256(path) for path in assets.iterdir()})
        manifest = bundle.read_json(self.job / "artifacts/result-manifest.json")
        self.assertEqual(manifest["provenance"]["model_ref"], reference)
        proof = bundle.read_json(self.job / manifest["provenance"]["render_receipt"]["path"])
        self.assertEqual(proof["original_vertices"], len(self.vertices))
        self.assertEqual(proof["original_triangles"], len(self.faces))
        self.assertTrue(proof["media"]["all_frames_decoded"])
        self.assertEqual(proof["source_mesh_sha256"], before["display_mesh.json"])
        with zipfile.ZipFile(result["data_path"]) as archive:
            self.assertIsNone(archive.testzip())
            declarations = json.loads(archive.read("archive-manifest.json"))["members"]
            self.assertEqual({item["path"] for item in declarations}, set(archive.namelist()) - {"archive-manifest.json"})
            for item in declarations:
                self.assertEqual(hashlib.sha256(archive.read(item["path"])).hexdigest(), item["sha256"])
            for filename, expected in before.items():
                self.assertEqual(hashlib.sha256(archive.read("model/" + filename)).hexdigest(), expected)
            self.assertEqual(archive.read("reference/reference.png"), self.image.read_bytes())
            self.assertEqual(archive.read("reference/source-receipt.json"), self.source_receipt.read_bytes())
            licenses = self.package / "modules/modeling/modeling_flow/third_party"
            actual_licenses = {name for name in archive.namelist() if name.startswith("licenses/")}
            self.assertEqual(actual_licenses, {"licenses/" + path.name for path in licenses.iterdir()})
            self.assertIn("licenses/PyMeshLab-GPL-3.0.txt", actual_licenses)
            for path in licenses.iterdir():
                self.assertEqual(archive.read("licenses/" + path.name), path.read_bytes())

    def test_changed_original_model_fails_before_render(self):
        reference, assets = self.seal_model()
        with (assets / "display_mesh.json").open("a") as handle:
            handle.write("\n")
        with mock.patch.object(ADAPTER, "stage") as stage, self.assertRaisesRegex(ValueError, "original model was modified"):
            self.render(reference)
        stage.assert_not_called()

    def test_changed_cached_video_is_rejected(self):
        reference, _ = self.seal_model()
        result = self.render(reference)
        with Path(result["video_path"]).open("ab") as handle:
            handle.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "saved preview delivery was modified"):
            self.render(reference)

    def test_same_pixels_in_two_admitted_images_bind_the_requested_path(self):
        reference, _ = self.seal_model()
        second = self.job / "inputs/identical-pixels.png"
        shutil.copyfile(self.image, second)
        self.task["request"]["input_images"].append({"id": "second-image", "path": "inputs/identical-pixels.png",
                                                    "sha256": bundle.sha256(second)})
        self.render(reference)
        provenance = bundle.read_json(self.job / "artifacts/result-manifest.json")["provenance"]
        self.assertEqual(provenance["image"]["id"], "fixture-image")
        self.assertEqual(provenance["image"]["path"], "inputs/reference.png")

    def test_model_request_change_rejects_the_old_model_reference(self):
        reference, _ = self.seal_model()
        request_path = self.job / "work/modeling-images" / reference / "request.json"
        request = bundle.read_json(request_path)
        request["seed"] = 1234
        write(request_path, request)
        with self.assertRaisesRegex(ValueError, "model reference does not match"):
            self.render(reference)

    def test_cached_public_reference_cannot_be_forged(self):
        reference, _ = self.seal_model()
        self.render(reference)
        path = self.delivery()
        data = bundle.read_json(path)
        data["public_result"]["model_ref"] = "0" * 64
        write(path, data)
        with self.assertRaises(ValueError):
            self.render(reference)

    def test_distinct_models_from_one_image_keep_their_own_delivery(self):
        first, _ = self.seal_model(0)
        first_result = self.render(first)
        second, _ = self.seal_model(42)
        self.assertNotEqual(first, second)
        second_result = self.render(second)
        self.assertNotEqual(first_result["video_path"], second_result["video_path"])
        self.assertNotEqual(first_result["data_path"], second_result["data_path"])
        # Re-selecting an unchanged first model should not become a tamper
        # error merely because another valid model was displayed in between.
        cached = self.render(first)
        self.assertEqual(cached["model_ref"], first)
        self.assertEqual(cached["video_path"], first_result["video_path"])
        self.assertEqual(bundle.read_json(self.job / "artifacts/result-manifest.json")["provenance"]["model_ref"], first)


if __name__ == "__main__":
    unittest.main()
