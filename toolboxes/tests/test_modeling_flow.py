"""Offline adapter verification. Fixtures do not claim real model inference."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


TOOLBOXES = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLBOXES))

from modeling_flow.contracts import (FlowError, artifact, digest, file_digest, load_config,
                                    load_request, output_directory, read_json, verify_weights)
from modeling_flow.meshes import audit_mesh, import_obj, orient_outward, scale_mesh, write_obj
from modeling_flow import runner


TETRAHEDRON = {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
               "faces": [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]}


class FakeArray(list):
    def tolist(self):
        return list(self)


class FakeMesh:
    def __init__(self, vertices=None, faces=None, process=False):
        self.vertices = FakeArray(vertices or TETRAHEDRON["vertices"])
        self.faces = FakeArray(faces or TETRAHEDRON["faces"])

    def export(self, path, file_type):
        if file_type != "glb":
            raise AssertionError("Only display GLB export is allowed")
        Path(path).write_bytes(b"glTF-test-fixture")


class ModelingFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="modeling-flow-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        upstream, weights = self.root / "upstream", self.root / "weights"
        upstream.mkdir()
        checkpoint = weights / "hunyuan3d-dit-v2-mini"
        checkpoint.mkdir(parents=True)
        (checkpoint / "config.yaml").write_text("test: only\n")
        header = json.dumps({"model.x": {}, "vae.x": {}, "conditioner.x": {}}).encode("utf-8")
        (checkpoint / "model.fp16.safetensors").write_bytes(len(header).to_bytes(8, "little") + header + b"x")
        self.config = {"schema_version": "modeling-flow-config/1", "upstream_root": str(upstream),
                       "upstream_commit": "a" * 40, "weights_root": str(weights),
                       "checkpoint_id": "tencent/Hunyuan3D-2mini", "checkpoint_revision": "b" * 40,
                       "backend": "hunyuan3d_mlx", "dtype": "float16",
                       "weight_files": [{"path": "hunyuan3d-dit-v2-mini/" + path.name,
                                         "sha256": file_digest(path)} for path in checkpoint.iterdir()]}
        self.image = self.root / "object.png"
        self.image.write_bytes(b"test-image-bytes")
        self.request = {"schema_version": "modeling-flow-request/1", "image_path": str(self.image),
                        "physical_extent_m": 0.2,
                        "material": {"name": "declared polymer", "density_kg_m3": 1000}}

    def fake_runtime(self, mesh=None):
        fake_trimesh = types.SimpleNamespace(Trimesh=FakeMesh)
        return patch.multiple(runner,
                              _load_runtime=Mock(return_value=(Mock(), Mock(), fake_trimesh,
                                                               {"commit": "a" * 40})),
                              _load_image=Mock(return_value=(Mock(), {"foreground_mask": "provided_alpha"})),
                              _model_mesh=Mock(return_value=mesh or FakeMesh()),
                              _versions=Mock(return_value={"mlx": "test-fixture"}))

    def test_config_defaults_to_mps_but_mlx_requires_metal(self):
        configured = load_config(self.config)
        self.assertEqual(configured["device"], "metal")
        self.assertEqual(configured["dtype"], "float16")
        torch_config = {**self.config, "backend": "hunyuan3d_torch"}
        self.assertEqual(load_config(torch_config)["device"], "mps")
        with self.assertRaisesRegex(FlowError, "MLX requires metal"):
            load_config({**self.config, "device": "cpu"})
        with self.assertRaisesRegex(FlowError, "CPU requires float32"):
            load_config({**torch_config, "device": "cpu"})

    def test_config_requires_exact_inventory_and_commit_pins(self):
        for update in ({"checkpoint_revision": "main"}, {"weight_files": []},
                       {"checkpoint_id": "unknown/model"}, {"variant": "fp32"}):
            with self.subTest(update=update), self.assertRaises(FlowError):
                load_config({**self.config, **update})
        tampered = copy.deepcopy(self.config)
        tampered["weight_files"][0]["path"] = "../outside"
        with self.assertRaises(FlowError):
            load_config(tampered)

    def test_runtime_cannot_silently_select_a_new_backend_or_dtype(self):
        for update in ({"backend": "auto"}, {"dtype": "auto"}, {"device": "cuda"}):
            with self.subTest(update=update), self.assertRaises(FlowError):
                load_config({**self.config, **update})

    def test_checkpoint_hash_mismatch_and_missing_conditioner_fail(self):
        verify_weights(load_config(self.config))
        path = Path(self.config["weights_root"]) / "hunyuan3d-dit-v2-mini/config.yaml"
        path.write_text("modified: yes\n")
        with self.assertRaisesRegex(FlowError, "digest mismatch"):
            verify_weights(load_config(self.config))
        tensor_path = path.parent / "model.fp16.safetensors"
        header = json.dumps({"model.x": {}, "vae.x": {}}).encode()
        tensor_path.write_bytes(len(header).to_bytes(8, "little") + header + b"x")
        with self.assertRaisesRegex(FlowError, "conditioner"):
            runner._checkpoint_header(load_config(self.config))

    def test_checkpoint_symlink_cannot_escape_weights_root(self):
        path = Path(self.config["weights_root"]) / self.config["weight_files"][0]["path"]
        path.unlink()
        path.symlink_to(self.image)
        with self.assertRaisesRegex(FlowError, "unsafe"):
            verify_weights(load_config(self.config))

    def test_upstream_pin_rejects_modified_runtime_source(self):
        upstream = Path(self.config["upstream_root"])
        package = upstream / "hy3dmlx"
        package.mkdir()
        source = package / "__init__.py"
        source.write_text("# local fixture\n")
        for args in (["init", "--quiet"], ["add", "hy3dmlx"],
                     ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--quiet", "-m", "fixture"]):
            subprocess.run(["git", "-C", str(upstream), *args], check=True, capture_output=True)
        commit = subprocess.run(["git", "-C", str(upstream), "rev-parse", "HEAD"],
                                check=True, capture_output=True, text=True).stdout.strip()
        config = load_config({**self.config, "upstream_commit": commit})
        self.assertEqual(runner._verify_upstream(config)["commit"], commit)
        source.write_text("# changed\n")
        with self.assertRaisesRegex(FlowError, "local changes"):
            runner._verify_upstream(config)

    def test_request_requires_explicit_scale_and_material(self):
        for field in ("physical_extent_m", "material"):
            request = {key: value for key, value in self.request.items() if key != field}
            with self.subTest(field=field), self.assertRaises(FlowError):
                load_request(request)
        request = load_request(self.request)
        self.assertEqual(request["seed"], 0)
        self.assertEqual(request["num_inference_steps"], 30)
        self.assertEqual(request["octree_resolution"], 128)
        self.assertEqual(request["num_chunks"], 2000)

    def test_request_rejects_nonfinite_unbounded_and_command_fields(self):
        updates = [{"physical_extent_m": float("nan")}, {"physical_extent_m": True},
                   {"physical_extent_m": 10**1000},
                   {"num_inference_steps": 101}, {"num_chunks": 1000000}, {"seed": -1},
                   {"allow_decimation": "yes"}, {"shell": "echo test"}, {"scale_axis": "image_width"}]
        for update in updates:
            with self.subTest(update=update), self.assertRaises(FlowError):
                load_request({**self.request, **update})

    def test_request_rejects_symlink_or_unsupported_image(self):
        linked = self.root / "linked.png"
        linked.symlink_to(self.image)
        for path in (linked, self.root / "missing.png", self.root / "object.svg"):
            with self.subTest(path=path), self.assertRaises(FlowError):
                load_request({**self.request, "image_path": str(path)})

    def test_json_rejects_duplicate_and_nonfinite_values(self):
        path = self.root / "unsafe.json"
        for raw in ('{"seed": 1, "seed": 2}', '{"x": NaN}', '{"x": 1e999}'):
            path.write_text(raw)
            with self.subTest(raw=raw), self.assertRaises(FlowError):
                read_json(path)

    def test_obj_roundtrip_and_negative_indices(self):
        path = self.root / "mesh.obj"
        write_obj(path, TETRAHEDRON)
        self.assertEqual(import_obj(path), TETRAHEDRON)
        text = path.read_text().replace("f 1 3 2", "f -4/-1/-1 -2/-1/-1 -3/-1/-1")
        path.write_text(text + "mtllib ../../credentials.mtl\n")
        self.assertEqual(import_obj(path), TETRAHEDRON)

    def test_obj_rejects_polygons_zero_external_include_and_nan(self):
        path = self.root / "bad.obj"
        vertices = "v 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\n"
        for invalid in ("f 1 2 3 4\n", "f 0 2 3\n", "f 1 2 6\n", "call other.obj\n", "v nan 0 0\n"):
            path.write_text(vertices + invalid)
            with self.subTest(invalid=invalid), self.assertRaises(FlowError):
                import_obj(path)

    def test_obj_byte_line_and_geometry_budgets(self):
        path = self.root / "mesh.obj"
        write_obj(path, TETRAHEDRON)
        with self.assertRaises(FlowError):
            import_obj(path, max_bytes=1)
        with self.assertRaises(FlowError):
            import_obj(path, max_vertices=3)
        path.write_text("#" + "x" * (16 * 1024) + "\n")
        with self.assertRaisesRegex(FlowError, "line exceeds"):
            import_obj(path)

    def test_closed_mesh_audit_and_uniform_metre_scaling(self):
        mesh, scale = scale_mesh(TETRAHEDRON, 0.2)
        audit = audit_mesh(mesh, simulation=True)
        self.assertTrue(audit["simulation_eligible"], audit)
        self.assertAlmostEqual(max(audit["extent"]), 0.2)
        self.assertAlmostEqual(audit["volume"], 0.2**3 / 6)
        self.assertEqual(scale["units"], "m")
        self.assertEqual(audit["self_intersections"], "not_certified")
        self.assertEqual(audit["physical_accuracy"], "not_verified_from_single_image")

    def test_audit_rejects_open_duplicate_degenerate_and_unused_vertices(self):
        cases = []
        opened = copy.deepcopy(TETRAHEDRON)
        opened["faces"].pop()
        cases.append(opened)
        duplicate = copy.deepcopy(TETRAHEDRON)
        duplicate["faces"].append(duplicate["faces"][0])
        cases.append(duplicate)
        degenerate = copy.deepcopy(TETRAHEDRON)
        degenerate["vertices"][3] = [0.5, 0.5, 0]
        cases.append(degenerate)
        unused = copy.deepcopy(TETRAHEDRON)
        unused["vertices"].append([4, 4, 4])
        cases.append(unused)
        for mesh in cases:
            with self.subTest(mesh=mesh):
                self.assertFalse(audit_mesh(mesh, simulation=True)["simulation_eligible"])

    def test_audit_rejects_inconsistent_winding_and_disconnected_solids(self):
        mesh = copy.deepcopy(TETRAHEDRON)
        mesh["faces"][0] = list(reversed(mesh["faces"][0]))
        self.assertGreater(audit_mesh(mesh)["inconsistent_winding_edges"], 0)
        mesh = copy.deepcopy(TETRAHEDRON)
        mesh["vertices"].extend([[x + 3, y, z] for x, y, z in TETRAHEDRON["vertices"]])
        mesh["faces"].extend([[index + 4 for index in face] for face in TETRAHEDRON["faces"]])
        audit = audit_mesh(mesh, simulation=True)
        self.assertEqual(audit["connected_components"], 2)
        self.assertFalse(audit["simulation_eligible"])

    def test_global_orientation_fix_is_disclosed_and_does_not_repair_holes(self):
        mesh = copy.deepcopy(TETRAHEDRON)
        mesh["faces"] = [list(reversed(face)) for face in mesh["faces"]]
        corrected, flipped = orient_outward(mesh)
        self.assertTrue(flipped)
        self.assertGreater(audit_mesh(corrected)["signed_volume"], 0)
        mesh["faces"].pop()
        unchanged, flipped = orient_outward(mesh)
        self.assertFalse(flipped)
        self.assertEqual(unchanged, mesh)

    def test_success_separates_display_physics_and_receipt_hashes(self):
        with self.fake_runtime():
            result = runner.generate(self.config, self.request, self.root / "run")
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["ready_to_simulate"])
        self.assertNotEqual(result["display_obj_path"], result["simulation_mesh_path"])
        display = read_json(result["display_mesh_json_path"])
        self.assertEqual(display["schema_version"], "modeling-flow-display/1")
        self.assertEqual(display["physical_accuracy"], "unverified")
        mesh = read_json(result["simulation_mesh_json_path"])
        self.assertEqual(mesh["units"], "m")
        self.assertEqual(mesh["material"], self.request["material"])
        self.assertEqual(result["simulation_mesh_sha256"], digest({key: mesh[key] for key in ("vertices", "faces")}))
        receipt = read_json(result["receipt_path"])
        self.assertEqual(receipt["image"]["sha256"], file_digest(self.image))
        self.assertEqual(receipt["execution"]["dtype"], "float16")
        self.assertEqual(receipt["execution"]["device"], "metal")
        self.assertEqual(receipt["backend"], "hunyuan3d_mlx")
        self.assertFalse(receipt["execution"]["texture_generation"])
        self.assertFalse(receipt["execution"]["flashvdm"])
        self.assertAlmostEqual(result["mass_estimate_kg"], 1000 * 0.2**3 / 6)
        self.assertNotIn("modeling_receipt", {item["role"] for item in receipt["artifacts"]})
        for item in result["artifacts"]:
            path = Path(result["receipt_path"]).parent / item["path"]
            self.assertEqual(item["sha256"], file_digest(path))

    def test_generated_open_mesh_preserves_display_and_blocks_physics(self):
        opened = FakeMesh(faces=TETRAHEDRON["faces"][:-1])
        with self.fake_runtime(opened):
            result = runner.generate(self.config, self.request, self.root / "run")
        self.assertFalse(result["ok"])
        self.assertEqual(result["code"], "simulation_mesh_rejected")
        self.assertFalse(result["ready_to_simulate"])
        self.assertTrue(Path(result["display_mesh_path"]).is_file())
        self.assertFalse((self.root / "run/simulation.obj").exists())
        receipt = read_json(result["receipt_path"])
        self.assertEqual(receipt["simulation_rejection"]["code"], result["code"])
        self.assertGreater(receipt["simulation_audit"]["boundary_edges"], 0)

    def test_inference_error_returns_no_geometry_fallback(self):
        with self.fake_runtime(), patch.object(runner, "_model_mesh",
                                             side_effect=FlowError("image_inference_failed", "test failure")):
            result = runner.generate(self.config, self.request, self.root / "run")
        self.assertEqual(result["code"], "image_inference_failed")
        self.assertFalse((self.root / "run").exists())

    def test_missing_weights_fails_before_runtime_import(self):
        (Path(self.config["weights_root"]) / "hunyuan3d-dit-v2-mini/model.fp16.safetensors").unlink()
        with patch.object(runner, "_load_runtime") as runtime:
            result = runner.generate(self.config, self.request, self.root / "run")
        runtime.assert_not_called()
        self.assertEqual(result["code"], "weights_missing")

    def test_decimation_is_explicit_and_reaudited(self):
        with self.fake_runtime(), patch.object(runner, "_decimate", return_value=(
                {"vertices": TETRAHEDRON["vertices"], "faces": TETRAHEDRON["faces"][:-1]},
                {"applied": True, "method": "test_reduction"})):
            result = runner.generate(self.config, self.request, self.root / "run")
        self.assertFalse(result["ok"])
        receipt = read_json(result["receipt_path"])
        self.assertTrue(receipt["decimation"]["applied"])
        self.assertGreater(receipt["simulation_audit"]["boundary_edges"], 0)

    def test_decimation_retries_from_source_and_preserves_exact_requested_extent(self):
        config, request = load_config(self.config), load_request(self.request)
        source = Mock()
        source.simplify_quadric_decimation.side_effect = [FakeMesh(faces=TETRAHEDRON["faces"][:-1]), FakeMesh()]
        trimesh = types.SimpleNamespace(Trimesh=Mock(return_value=source))
        def audit(candidate, simulation=False):
            result = audit_mesh(candidate, simulation=simulation)
            if candidate is TETRAHEDRON:
                result["simulation_budget_passed"] = False
            return result
        with patch.object(runner, "audit_mesh", side_effect=audit), \
                patch.object(runner.importlib, "import_module", return_value=Mock()):
            mesh, report = runner._decimate(TETRAHEDRON, trimesh, request)
        self.assertEqual(report["target_faces"], 6000)
        self.assertEqual(len(report["attempts"]), 2)
        self.assertFalse(report["attempts"][0]["audit"]["simulation_eligible"])
        self.assertTrue(report["attempts"][1]["audit"]["simulation_eligible"])
        self.assertAlmostEqual(max(audit_mesh(mesh)["extent"]), request["physical_extent_m"])
        self.assertEqual(source.simplify_quadric_decimation.call_count, 2)

    def test_all_decimation_candidates_rejected_with_audit_details(self):
        request = load_request(self.request)
        source = Mock()
        source.simplify_quadric_decimation.return_value = FakeMesh(faces=TETRAHEDRON["faces"][:-1])
        trimesh = types.SimpleNamespace(Trimesh=Mock(return_value=source))
        def audit(candidate, simulation=False):
            result = audit_mesh(candidate, simulation=simulation)
            if candidate is TETRAHEDRON:
                result["simulation_budget_passed"] = False
            return result
        with patch.object(runner, "audit_mesh", side_effect=audit), \
                patch.object(runner.importlib, "import_module", return_value=Mock()):
            with self.assertRaises(FlowError) as rejected:
                runner._decimate(TETRAHEDRON, trimesh, request)
        self.assertEqual(rejected.exception.code, "simulation_mesh_rejected")
        self.assertEqual(len(rejected.exception.details["attempts"]), 3)

    def test_decimation_budget_failure_is_not_hidden(self):
        request = load_request({**self.request, "allow_decimation": False})
        with patch.object(runner, "audit_mesh", return_value={"simulation_budget_passed": False}):
            with self.assertRaisesRegex(FlowError, "decimation is disabled"):
                runner._decimate(TETRAHEDRON, Mock(), request)
        request["allow_decimation"] = True
        with patch.object(runner, "audit_mesh", return_value={"simulation_budget_passed": False}), \
                patch.object(runner.importlib, "import_module", side_effect=ImportError("missing")):
            with self.assertRaisesRegex(FlowError, "fast-simplification"):
                runner._decimate(TETRAHEDRON, Mock(), request)

    def test_health_reports_installation_without_inference_claim(self):
        with self.fake_runtime():
            result = runner.health(self.config)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["runtime_ready"])
        self.assertFalse(result["inference_verified"])

    def test_mlx_model_call_pins_algorithm_and_disables_optional_approximation(self):
        mx = types.SimpleNamespace(float16="test-fp16", clear_cache=Mock())
        pipe, cls = Mock(), Mock()
        cls.from_pretrained.return_value = pipe
        config, request = load_config(self.config), load_request(self.request)
        image = object()
        runner._model_mesh(mx, cls, config, request, image)
        kwargs = cls.from_pretrained.call_args.kwargs
        self.assertIsNone(kwargs["quantize"])
        self.assertEqual(kwargs["dtype"], "test-fp16")
        self.assertIs(pipe.generate.call_args.args[0], image)
        self.assertFalse(pipe.generate.call_args.kwargs["octree_decode"])
        self.assertFalse(pipe.generate.call_args.kwargs["compile_dit"])
        self.assertEqual(pipe.generate.call_args.kwargs["seed"], request["seed"])
        mx.clear_cache.assert_called_once()

    def test_missing_metal_fails_before_pipeline_load_without_cpu_fallback(self):
        config = load_config(self.config)
        root = Path(config["upstream_root"])
        mx = types.SimpleNamespace(metal=types.SimpleNamespace(is_available=lambda: False),
                                   set_default_device=Mock())
        pipeline = Mock()
        module = types.SimpleNamespace(__file__=str(root / "hy3dmlx/pipeline.py"),
                                       Hunyuan3DShapePipeline=pipeline)
        modules = {"mlx.core": mx, "hy3dmlx.pipeline": module, "trimesh": Mock(), "PIL.Image": Mock()}
        with patch.object(runner, "_verify_upstream", return_value={}), \
                patch.object(runner.importlib, "import_module", side_effect=lambda name: modules[name]):
            with self.assertRaisesRegex(FlowError, "Metal is required"):
                runner._load_runtime(config)
        mx.set_default_device.assert_not_called()
        pipeline.from_pretrained.assert_not_called()

    def test_official_torch_call_uses_local_checkpoint_and_cpu_seed_generator(self):
        config = load_config({**self.config, "backend": "hunyuan3d_torch", "device": "mps"})
        request = load_request(self.request)
        generator = Mock()
        generator.manual_seed.return_value = generator
        torch = types.SimpleNamespace(float16="test-fp16", Generator=Mock(return_value=generator),
                                      mps=types.SimpleNamespace(empty_cache=Mock()))
        pipe, cls = Mock(return_value=[FakeMesh()]), Mock()
        cls.from_pretrained.return_value = pipe
        runner._model_mesh(torch, cls, config, request, object())
        self.assertEqual(cls.from_pretrained.call_args.args[0], config["weights_root"])
        kwargs = cls.from_pretrained.call_args.kwargs
        self.assertEqual(kwargs["device"], "mps")
        self.assertTrue(kwargs["use_safetensors"])
        self.assertEqual(kwargs["subfolder"], "hunyuan3d-dit-v2-mini")
        self.assertEqual(pipe.call_args.kwargs["mc_algo"], "mc")
        self.assertEqual(pipe.call_args.kwargs["output_type"], "trimesh")
        torch.Generator.assert_called_once_with(device="cpu")
        generator.manual_seed.assert_called_once_with(request["seed"])

    def test_outputs_are_immutable_and_require_dedicated_directories(self):
        with self.fake_runtime():
            first = runner.generate(self.config, self.request, self.root / "run")
            before = file_digest(Path(first["receipt_path"]))
            second = runner.generate(self.config, self.request, self.root / "run")
        self.assertEqual(second["code"], "output_exists")
        self.assertEqual(file_digest(Path(first["receipt_path"])), before)
        with self.assertRaises(FlowError):
            output_directory(Path("relative-output"))


if __name__ == "__main__":
    unittest.main()
