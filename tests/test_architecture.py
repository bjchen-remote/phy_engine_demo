"""Canonical modules retain legacy module identity and bundled resources."""
from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


LAYERS = {
    "core": "engine math3d colliders force_fields sliders meshes connections_solver "
            "mesh_solver coupled_solver native_backend native_runtime backend",
    "analysis": "queries observers results",
    "io": "schema planning mesh_scene slider_schema connections coupled jsonio video",
}


class ModuleLayerTests(unittest.TestCase):
    def test_legacy_imports_alias_the_actual_canonical_implementation(self):
        for layer, names in LAYERS.items():
            for name in names.split():
                with self.subTest(module=name):
                    legacy = importlib.import_module(f"physics_demo.{name}")
                    canonical = importlib.import_module(f"physics_demo.{layer}.{name}")
                    self.assertIs(legacy, canonical)
                    self.assertEqual(Path(canonical.__file__).parent.name, layer)

    def test_legacy_private_monkeypatches_reach_live_dispatch(self):
        from physics_demo.core import backend, native_backend

        with patch("physics_demo.native_backend._compiler", return_value="patched-compiler"):
            self.assertEqual(native_backend._compiler(), "patched-compiler")
        with patch("physics_demo.backend.run_scene_native", return_value="patched-solver"):
            self.assertEqual(backend.run_scene_native(), "patched-solver")
        with patch("physics_demo.core.backend.run_scene_native", return_value="canonical-patch"):
            legacy = importlib.import_module("physics_demo.backend")
            self.assertEqual(legacy.run_scene_native(), "canonical-patch")

    def test_native_and_renderer_resources_live_beside_their_bridges(self):
        from physics_demo.core import native_backend, coupled_solver
        from physics_demo.io import video

        native = Path(coupled_solver.__file__).with_name("native")
        self.assertEqual(native_backend._native_sources(),
                         (native / "physics_native.c", native / "physics_native.h"))
        for stem in ("physics_native", "mesh_native", "connections_native", "coupled_native"):
            for suffix in (".c", ".h"):
                self.assertTrue((native / (stem + suffix)).is_file())
        self.assertTrue((native / "rigid_math.h").is_file())
        for name in ("video_render_core.h", "video_render_core.m", "video_renderer.m",
                     "video_mjpeg_renderer.m", "video_decode_probe.m"):
            self.assertTrue(Path(video.__file__).with_name(name).is_file())

    def test_reference_state_is_shared_with_legacy_and_native_imports(self):
        from physics_demo.core import engine, native_backend, state
        from physics_demo import engine as legacy

        for name in ("Particle", "GravityBody", "RigidBody"):
            self.assertIs(getattr(engine, name), getattr(state, name))
            self.assertIs(getattr(legacy, name), getattr(state, name))
        self.assertIs(native_backend.GravityBody, state.GravityBody)

    def test_implementation_imports_use_canonical_paths(self):
        legacy_names = {f"physics_demo.{name}"
                        for names in LAYERS.values() for name in names.split()}
        for layer, names in LAYERS.items():
            for name in names.split():
                module = importlib.import_module(f"physics_demo.{layer}.{name}")
                for node in ast.walk(ast.parse(Path(module.__file__).read_text())):
                    if isinstance(node, ast.ImportFrom):
                        with self.subTest(module=module.__name__, line=node.lineno):
                            self.assertEqual(node.level, 0, "implementation imports must be absolute")
                            self.assertNotIn(node.module, legacy_names)
                    elif isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertNotIn(alias.name, legacy_names)

    def test_core_and_systems_do_not_load_example_scenes(self):
        root = Path(__file__).resolve().parents[1] / "physics_demo"
        for layer in ("core", "systems"):
            for source in (root / layer).rglob("*.py"):
                for node in ast.walk(ast.parse(source.read_text())):
                    with self.subTest(source=source, line=getattr(node, "lineno", None)):
                        if isinstance(node, ast.ImportFrom):
                            self.assertNotEqual(node.module, "physics_demo.catalog")
                        elif isinstance(node, ast.Import):
                            self.assertNotIn("physics_demo.catalog", [item.name for item in node.names])
                        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                            self.assertNotIn("examples", Path(node.value).parts,
                                             "simulation implementation must not load example files")

    def test_checkout_catalog_has_every_advertised_example(self):
        from physics_demo import catalog

        root = Path(__file__).resolve().parents[1]
        for name, item in catalog.EXAMPLE_CATALOG.items():
            with self.subTest(example=name):
                self.assertEqual(catalog._example_path(item["file"]), root / "examples" / item["file"])
                self.assertTrue(catalog.example(name)["ok"])
        self.assertTrue(catalog._example_path("mesh_reference_star.png").is_file())

    def test_catalog_uses_packaged_assets_without_a_checkout(self):
        from physics_demo import catalog

        original = catalog.example("three_body")["scene"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            package = root / "physics_demo"
            assets = package / "data" / "examples"
            assets.mkdir(parents=True)
            (assets / "three_body.json").write_text(json.dumps(original))
            with patch.object(catalog, "__file__", str(package / "catalog.py")):
                self.assertEqual(catalog._example_path("three_body.json"), assets / "three_body.json")
                self.assertEqual(catalog.example("three_body")["scene"], original)
                # A source checkout remains authoritative, avoiding two editable
                # copies of the canonical examples in the repository.
                (root / "examples").mkdir()
                source = root / "examples" / "three_body.json"
                modified = {**original, "name": "checkout copy"}
                source.write_text(json.dumps(modified))
                self.assertEqual(catalog._example_path("three_body.json"), source)
                self.assertEqual(catalog.example("three_body")["scene"], modified)


if __name__ == "__main__":
    unittest.main()
