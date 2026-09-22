"""Public-tool and whole-scene hostile inputs must fail as structured data."""
from __future__ import annotations

import copy
import json
import unittest

from physics_demo.api import call_tool
from physics_demo.meshes import build_mesh
from physics_demo.runner import prepare, validate


class MeshAdversarialInputs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.asset = build_mesh({"type": "box", "subdivisions": 1})

    def scene(self):
        return {"entities": [{"id": "soft", "type": "mesh", "mesh": copy.deepcopy(self.asset),
                              "position": [0, 1, 0]}],
                "world": {"duration": 0.1, "dt": 0.01, "output_fps": 10}}

    def rejected(self, scene, functions=(validate, prepare)):
        for function in functions:
            with self.subTest(function=function.__name__):
                result = function(scene)
                self.assertIs(result["ok"], False, result)
                self.assertIsInstance(result.get("errors"), list, result)
                self.assertTrue(result["errors"], result)
                self.assertTrue(all(isinstance(e.get("code"), str) and isinstance(e.get("message"), str)
                                    for e in result["errors"]), result)

    def test_minimal_mesh_build_validate_prepare_round_trip(self):
        modeled = call_tool("physics_mesh", {"spec_json": json.dumps({"type": "box", "subdivisions": 1})})
        self.assertTrue(modeled["ok"], modeled)
        scene = self.scene()
        scene["entities"][0]["mesh"] = json.loads(modeled["mesh_json"])
        prepared = prepare(scene)
        self.assertTrue(prepared["ok"], prepared)
        again = prepare(json.loads(prepared["scene_json"]))
        self.assertTrue(again["ok"], again)
        self.assertEqual(again["scene_json"], prepared["scene_json"])
        self.assertEqual(prepared["plan"]["mesh_vertices"], 8)

    def test_mesh_scalar_types_and_nonfinite_values(self):
        invalid = ["1", [], {}, True, None, float("nan"), float("inf"), 10 ** 500]
        for key in ("mass", "edge_compliance", "bending_compliance", "volume_compliance", "damping", "friction", "thickness"):
            for value in invalid:
                with self.subTest(field=key, value=repr(value)[:30]):
                    scene = self.scene()
                    scene["entities"][0][key] = value
                    self.rejected(scene)

    def test_mesh_vectors_reject_malformed_components_and_shapes(self):
        for key in ("position", "velocity", "color"):
            for value in (None, True, "0,0,0", {}, [], [0, 0], [0, 0, []], [0, 0, True],
                          [0, 0, float("nan")], [0, 0, 10 ** 500]):
                with self.subTest(field=key, value=repr(value)[:30]):
                    scene = self.scene()
                    scene["entities"][0][key] = value
                    self.rejected(scene)

    def test_invalid_motion_and_pin_identifiers_never_escape_validation(self):
        for key, values in (("motion", [None, True, [], {}, "rigid", 1]),
                            ("pinned_vertices", [None, True, "0", {}, [-1], [8], [0, 0], [True], [0.0], [[0]], [{}]])):
            for value in values:
                with self.subTest(field=key, value=value):
                    scene = self.scene()
                    scene["entities"][0][key] = value
                    self.rejected(scene)

    def test_settings_types_ranges_and_unknown_fields(self):
        for settings in (None, [], True, "high", {"iterations": 0}, {"iterations": 33},
                         {"iterations": 1.0}, {"iterations": []}, {"iterations": float("nan")},
                         {"substeps": 0}, {"substeps": True}, {"substeps": {}},
                         {"substeps": 10 ** 500}, {"automatic_decimation": True}):
            with self.subTest(settings=settings):
                scene = self.scene()
                scene["mesh_settings"] = settings
                self.rejected(scene)

    def test_settings_on_particle_scene_are_not_ignored(self):
        self.rejected({"entities": [{"type": "point_mass"}], "mesh_settings": {"iterations": 2}})

    def test_mesh_asset_and_metadata_types(self):
        for value in (None, [], True, "shape.obj", {}, {"vertices": []}, {"triangles": []}):
            with self.subTest(asset=value):
                scene = self.scene()
                scene["entities"][0]["mesh"] = value
                self.rejected(scene)
        for value in (None, [], True, {"recipe": []}, {"units": "centimetres"},
                      {"provenance": {"source": []}}, {"provenance": {"source": "image", "notes": []}},
                      {"provenance": {"source": "imagined", "notes": ""}}, {"safe_to_skip_audit": True}):
            with self.subTest(metadata=value):
                scene = self.scene()
                scene["entities"][0]["mesh"]["metadata"] = value
                self.rejected(scene)

    def test_raw_vertex_and_triangle_index_types_through_scene(self):
        for key, values in (("vertices", [None, True, {}, [], [[0, 0, float("nan")]]]),
                            ("triangles", [None, True, {}, [], [[True, 1, 2]], [[0.0, 1, 2]],
                                           [[-1, 1, 2]], [[10 ** 500, 1, 2]], [[[], 1, 2]]])):
            for value in values:
                with self.subTest(field=key, value=repr(value)[:30]):
                    scene = self.scene()
                    scene["entities"][0]["mesh"][key] = value
                    self.rejected(scene)

    def test_aliases_cycles_and_deep_containers_are_rejected(self):
        scene = self.scene()
        scene["entities"][0]["position"] = scene["entities"][0]["mesh"]["vertices"][0]
        self.rejected(scene)
        scene = self.scene()
        scene["entities"][0]["mesh"]["metadata"]["provenance"]["notes"] = scene
        self.rejected(scene)
        scene = self.scene()
        value = []
        for _ in range(40):
            value = [value]
        scene["mesh_settings"] = value
        self.rejected(scene)

    def test_mixed_physics_and_unsupported_backend_are_explicit_failures(self):
        for kind in ("point_mass", "fluid", "granular", "slider"):
            with self.subTest(kind=kind):
                scene = self.scene()
                scene["entities"].append({"id": "other", "type": kind})
                self.rejected(scene)
        scene = self.scene()
        scene["interactions"] = {"mutual_gravity": True}
        self.rejected(scene)
        scene = self.scene()
        scene["budget"] = {"backend": "python"}
        self.rejected(scene)

    def test_static_motion_and_pin_contract(self):
        scene = self.scene()
        scene["entities"][0].update({"motion": "static", "velocity": [1, 0, 0]})
        self.rejected(scene)
        scene = self.scene()
        scene["entities"][0].update({"motion": "kinematic", "pinned_vertices": [0]})
        self.rejected(scene)

    def test_frame_budget_rejects_without_silent_topology_reduction(self):
        scene = self.scene()
        asset = build_mesh({"type": "box", "subdivisions": 8})
        scene["entities"][0]["mesh"] = asset
        scene["world"].update({"duration": 30, "dt": 0.05, "output_fps": 60})
        scene["mesh_settings"] = {"substeps": 1, "iterations": 1}
        self.assertTrue(validate(scene)["ok"])
        result = prepare(scene)
        self.assertFalse(result["ok"], result)
        self.assertIn("mesh_budget_exceeded", {e["code"] for e in result["errors"]})
        self.assertEqual(result["plan"]["mesh_vertices"], len(asset["vertices"]))

    def test_mesh_tool_rejects_ambiguous_json_and_non_string_specs(self):
        for arguments in ({}, [], None, {"spec_json": {}}, {"spec_json": True},
                          {"spec_json": '{"type":"box","type":"raw"}'},
                          {"spec_json": '{"type":"box","size":[NaN,1,1]}'},
                          {"spec_json": '{"type":"box","size":[1e999,1,1]}'},
                          {"spec_json": '"not a recipe"'}, {"spec_json": "["},
                          {"spec_json": '{"type":"box","path":"../../some-file"}'}):
            with self.subTest(arguments=arguments):
                result = call_tool("physics_mesh", arguments)
                self.assertFalse(result["ok"], result)
                self.assertTrue(result.get("errors"), result)

    def test_mesh_tool_enforces_raw_geometry_and_input_size_caps(self):
        spec = {"type": "raw", "vertices": [[0, 0, 0] for _ in range(4097)], "triangles": [[0, 1, 2]]}
        result = call_tool("physics_mesh", {"spec_json": json.dumps(spec)})
        self.assertFalse(result["ok"], result)
        result = call_tool("physics_mesh", {"spec_json": " " * 1_000_001})
        self.assertFalse(result["ok"], result)

    def test_prepare_budget_override_rejects_hostile_values(self):
        for value in (True, [], {}, "60", float("nan"), float("inf"), 10 ** 500, -1):
            with self.subTest(value=repr(value)[:30]):
                result = prepare(self.scene(), value)
                self.assertFalse(result["ok"], result)
                self.assertTrue(result.get("errors"), result)


if __name__ == "__main__":
    unittest.main()
