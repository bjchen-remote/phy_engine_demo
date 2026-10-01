"""Real post-inference display artifacts; the inference fixture is explicit.

These tests exercise image decoding, checkpoint inventory, full triangle
normalization/audit, OBJ round-trip and real GLB export. They substitute an
explicit in-memory inference result, and do not claim to test neural inference.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from modeling_flow import runner
from modeling_flow.contracts import FlowError, PREVIEW_REQUEST_SCHEMA, file_digest, load_request


DEPENDENCIES_AVAILABLE = all(importlib.util.find_spec(name) for name in ("numpy", "trimesh", "PIL"))


class PreviewRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.image = Path(self.temp.name).resolve() / "input.png"
        self.image.write_bytes(b"contract-check-only")
        self.request = {"schema_version": PREVIEW_REQUEST_SCHEMA, "image_path": str(self.image)}

    def test_preview_needs_neither_material_nor_physical_dimensions(self):
        parsed = load_request(self.request)
        self.assertNotIn("material", parsed)
        self.assertNotIn("physical_extent_m", parsed)
        self.assertFalse(parsed["allow_decimation"])
        self.assertEqual(parsed["scale_axis"], "max")

    def test_preview_rejects_material_and_geometry_reduction(self):
        for update in ({"material": {"name": "guessed", "density_kg_m3": 1000}},
                       {"allow_decimation": True}):
            with self.subTest(update=update), self.assertRaises(FlowError):
                load_request({**self.request, **update})

    def test_explicit_dimensions_remain_an_optional_user_input(self):
        parsed = load_request({**self.request, "physical_extent_m": .16})
        self.assertEqual(parsed["physical_extent_m"], .16)
        self.assertNotIn("material", parsed)
        for value in (True, 0, -1, float("nan"), 10001):
            with self.subTest(value=value), self.assertRaises(FlowError):
                load_request({**self.request, "physical_extent_m": value})


@unittest.skipUnless(DEPENDENCIES_AVAILABLE, "real GLB/PNG post-inference checks need numpy, trimesh and Pillow")
class RealPreviewArtifactsTests(unittest.TestCase):
    def setUp(self):
        import trimesh
        from PIL import Image
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.upstream, self.weights = self.root / "explicit-fixture-upstream", self.root / "weights"
        self.upstream.mkdir()
        checkpoint = self.weights / "hunyuan3d-dit-v2-mini"
        checkpoint.mkdir(parents=True)
        (checkpoint / "config.yaml").write_text("explicit-inference-fixture: true\n")
        header = json.dumps({"model.fixture": {}, "vae.fixture": {}, "conditioner.fixture": {}}).encode()
        (checkpoint / "model.fp16.safetensors").write_bytes(len(header).to_bytes(8, "little") + header + b"fixture")
        self.config = {"schema_version": "modeling-flow-config/1", "upstream_root": str(self.upstream),
                       "upstream_commit": "a" * 40, "weights_root": str(self.weights),
                       "checkpoint_id": "tencent/Hunyuan3D-2mini", "checkpoint_revision": "b" * 40,
                       "backend": "hunyuan3d_mlx", "dtype": "float16",
                       "weight_files": [{"path": "hunyuan3d-dit-v2-mini/" + path.name,
                                         "sha256": file_digest(path)} for path in checkpoint.iterdir()]}
        self.image = self.root / "actual-pixels.png"
        Image.new("RGBA", (32, 32), (160, 100, 50, 200)).save(self.image)
        self.request = {"schema_version": PREVIEW_REQUEST_SCHEMA, "image_path": str(self.image)}
        self.vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
                         [2, 0, 0], [3, 0, 0], [2, 1, 0], [2, 0, 1],
                         [.1, 0, 0], [.2, 0, 0]]
        tetra = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]
        self.faces = tetra + [[index + 4 for index in face] for face in tetra] + [[0, 8, 9]]
        self.inference_fixture = trimesh.Trimesh(vertices=self.vertices, faces=self.faces, process=False)
        self.trimesh = trimesh

    def generate(self, request):
        # Only neural runtime loading/inference are substituted. Artifact and
        # topology code, byte receipts, image parsing and GLB export are real.
        with mock.patch.object(runner, "_load_runtime", return_value=(
                None, None, self.trimesh, {"repository": "explicit-test-fixture",
                                         "commit": "a" * 40, "fixture": True})), \
             mock.patch.object(runner, "_model_mesh", return_value=self.inference_fixture), \
             mock.patch.object(runner, "_decimate", side_effect=AssertionError("preview must not decimate")) as decimate, \
             mock.patch.object(runner, "_verify_decimator_runtime", side_effect=AssertionError("preview needs no decimator")) as runtime:
            result = runner.generate(self.config, request, self.root / "output")
            decimate.assert_not_called()
            runtime.assert_not_called()
        self.assertTrue(result["ok"], result)
        return result, json.loads(Path(result["display_mesh_json_path"]).read_bytes()), \
            json.loads(Path(result["receipt_path"]).read_bytes())

    def test_full_multicomponent_geometry_exported_in_model_units(self):
        result, display, receipt = self.generate(self.request)
        self.assertTrue(result["ready_to_preview"])
        self.assertFalse(result["ready_to_simulate"])
        self.assertEqual(display["units"], "model_unit")
        self.assertEqual(display["scale"]["normalized_extent"], 1)
        self.assertIsNone(display["scale"]["physical_extent_m"])
        self.assertEqual(display["faces"], self.faces)
        self.assertEqual(len(display["vertices"]), len(self.vertices))
        self.assertEqual(display["audit"]["connected_components"], 2)
        self.assertEqual(display["audit"]["degenerate_faces"], 1)
        self.assertEqual(display["audit"]["faces"], 9)
        self.assertFalse(receipt["decimation"]["applied"])
        self.assertEqual(receipt["simulation_preparation"], "not_requested")
        self.assertFalse(receipt["simulation_performed"])
        self.assertFalse(receipt["numerical_usable"])
        self.assertIsNone(receipt["material"])
        self.assertIsNone(receipt["mass_estimate_kg"])
        self.assertFalse((self.root / "output" / "simulation_mesh.json").exists())
        self.assertFalse((self.root / "output" / "simulation.obj").exists())
        obj = Path(result["display_obj_path"]).read_text()
        self.assertEqual(sum(line.startswith("f ") for line in obj.splitlines()), 9)
        glb = self.trimesh.load(result["display_mesh_path"], process=False)
        exported = list(glb.geometry.values()) if hasattr(glb, "geometry") else [glb]
        self.assertEqual(sum(len(mesh.faces) for mesh in exported), 9)
        for artifact in result["artifacts"]:
            self.assertEqual(file_digest(self.root / "output" / artifact["path"]), artifact["sha256"])

    def test_supplied_metres_affect_scale_only_without_guessed_material(self):
        result, display, receipt = self.generate({**self.request, "physical_extent_m": .16})
        self.assertEqual(display["units"], "m")
        self.assertEqual(display["faces"], self.faces)
        self.assertEqual(display["audit"]["connected_components"], 2)
        self.assertAlmostEqual(max(display["audit"]["extent"]), .16)
        self.assertEqual(receipt["scale"]["physical_extent_m"], .16)
        self.assertIsNone(receipt["material"])
        self.assertIsNone(receipt["mass_estimate_kg"])
        self.assertFalse(receipt["ready_to_simulate"])
        self.assertIn("Dimensions are explicitly supplied.", receipt["assumptions"])


if __name__ == "__main__":
    unittest.main()
