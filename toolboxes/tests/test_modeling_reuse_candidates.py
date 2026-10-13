"""Shape reuse must remain unavailable without an actual verified GPU route."""
from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


TOOLBOXES = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLBOXES))

from modeling_flow.contracts import FlowError
from modeling_flow.shape_reuse import reuse_shape, shape_reuse_capability


PINNED_CONFIG = {
    "backend": "hunyuan3d_mlx",
    "upstream_commit": "292331f4d26ddb80b9dcea6bcb5629ff82f12b82",
    "checkpoint_id": "tencent/Hunyuan3D-2mini",
    "checkpoint_revision": "f90a0f7df7d5e6f71109cf333f6a95a0ae3194a6",
    "model_subfolder": "hunyuan3d-dit-v2-mini",
}


class ForbiddenPath:
    def __fspath__(self):
        raise AssertionError("Shape reuse must not evaluate a request or output path")


class ShapeReuseTests(unittest.TestCase):
    def test_known_pins_are_recognized_without_claiming_support(self):
        config = copy.deepcopy(PINNED_CONFIG)
        capability = shape_reuse_capability(config)
        self.assertEqual(capability["schema_version"], "modeling-flow-shape-reuse-capability/1")
        self.assertEqual(capability["status"], "unsupported")
        self.assertEqual(capability["code"], "shape_reuse_unverified")
        self.assertTrue(capability["runtime_pins_recognized"])
        self.assertIs(capability["supported"], False)
        self.assertIs(capability["inference_verified"], False)
        self.assertEqual(config, PINNED_CONFIG)

    def test_default_model_subfolder_matches_the_existing_config_default(self):
        config = {key: value for key, value in PINNED_CONFIG.items() if key != "model_subfolder"}
        self.assertTrue(shape_reuse_capability(config)["runtime_pins_recognized"])

    def test_different_or_missing_pins_do_not_advertise_an_alternate_route(self):
        changes = ({"backend": "hunyuan3d_torch"}, {"upstream_commit": "a" * 40},
                   {"checkpoint_revision": "b" * 40}, {"checkpoint_id": "unknown/model"},
                   {"model_subfolder": "hunyuan3d-dit-v2-mini-turbo"}, {"backend": []})
        for change in changes:
            with self.subTest(change=change):
                capability = shape_reuse_capability({**PINNED_CONFIG, **change})
                self.assertEqual(capability["code"], "shape_reuse_unsupported_runtime")
                self.assertFalse(capability["runtime_pins_recognized"])
                self.assertIs(capability["supported"], False)
                self.assertIs(capability["inference_verified"], False)
        self.assertFalse(shape_reuse_capability({})["runtime_pins_recognized"])

    def test_config_paths_are_rejected_without_file_access(self):
        for value in ("/not-opened/config.json", Path("/not-opened/config.json"), ForbiddenPath()):
            with self.subTest(value=type(value).__name__), patch("builtins.open", side_effect=AssertionError("file access")):
                with self.assertRaises(FlowError) as error:
                    shape_reuse_capability(value)
                self.assertEqual(error.exception.code, "invalid_config")

    def test_reuse_fails_before_neural_imports_or_path_operations(self):
        config = {**PINNED_CONFIG, "upstream_root": ForbiddenPath(), "weights_root": ForbiddenPath(),
                  "inference_verified": True, "shape_reuse_verified": True}
        request = {"latent_path": ForbiddenPath(), "cache_path": ForbiddenPath(),
                   "inference_verified": True}
        with patch("builtins.open", side_effect=AssertionError("file access")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("artifact creation")), \
                patch("importlib.import_module", side_effect=AssertionError("dependency loading")):
            with self.assertRaises(FlowError) as error:
                reuse_shape(config, request, ForbiddenPath())
        self.assertEqual(error.exception.code, "shape_reuse_unverified")
        capability = error.exception.details["shape_reuse"]
        self.assertIs(capability["supported"], False)
        self.assertIs(capability["inference_verified"], False)

    def test_rejected_request_creates_no_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="shape-reuse-test-") as name:
            root = Path(name)
            output = root / "must-not-exist"
            missing_cache = root / "missing-latents.safetensors"
            with self.assertRaises(FlowError) as error:
                reuse_shape(PINNED_CONFIG, {"latent_path": str(missing_cache)}, str(output))
            self.assertEqual(error.exception.code, "shape_reuse_unverified")
            self.assertEqual(list(root.iterdir()), [])

    def test_import_and_capability_check_do_not_load_neural_packages(self):
        script = "\n".join((
            "import sys",
            f"sys.path.insert(0, {str(TOOLBOXES)!r})",
            "from modeling_flow.shape_reuse import shape_reuse_capability",
            f"result = shape_reuse_capability({PINNED_CONFIG!r})",
            "assert result['supported'] is False",
            "blocked = ('mlx', 'torch', 'hy3dmlx', 'hy3dgen', 'numpy', 'trimesh', 'safetensors', 'PIL')",
            "assert not any(name.split('.')[0] in blocked for name in sys.modules)",
        ))
        result = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
