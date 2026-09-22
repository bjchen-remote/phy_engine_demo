"""Public mesh tool, schema agreement and hostile-input boundary tests."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None

from physics_demo.api import call_tool
from physics_demo.meshes import build_mesh
from physics_demo.runner import prepare, validate


ROOT = Path(__file__).resolve().parents[1]


class MeshContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.asset = build_mesh({"type": "box", "subdivisions": 1})
        cls.schema = json.loads((ROOT / "agent/scene-v1.schema.json").read_text())
        cls.validator = Draft202012Validator(cls.schema) if Draft202012Validator else None

    def scene(self, asset=None):
        return {
            "version": 1, "name": "mesh-contract",
            "world": {"gravity": [0, -9.81, 0], "duration": 0.5, "dt": 0.01,
                      "output_fps": 12, "bounds": {"min": [-4, 0, -4], "max": [4, 4, 4]}},
            "budget": {"wall_time_s": 60, "quality": "preview", "backend": "auto"},
            "entities": [{"id": "body", "type": "mesh", "mesh": deepcopy(asset or self.asset),
                          "position": [0, 1.5, 0], "velocity": [0, 0, 0]}],
            "colliders": [],
        }

    def assert_schema(self, scene, valid):
        if self.validator:
            errors = list(self.validator.iter_errors(scene))
            self.assertEqual(not errors, valid, "\n".join(str(e) for e in errors[:2]))

    def test_recipes_return_audited_assets_accepted_by_scene_contract(self):
        recipes = [
            {"type": "box", "subdivisions": 1},
            {"type": "ellipsoid", "segments": 8, "rings": 4},
            {"type": "torus", "segments": 8, "tube_segments": 6},
            {"type": "lathe", "profile": [[0, -0.5], [0.4, 0], [0, 0.5]], "segments": 8},
            {"type": "extrusion", "contour": [[-0.4, -0.4], [0.4, -0.4], [0.1, 0], [0.4, 0.4], [-0.4, 0.4]]},
            {"type": "cloth", "subdivisions": 2},
            {"type": "raw", "vertices": self.asset["vertices"], "triangles": self.asset["triangles"]},
        ]
        for recipe in recipes:
            with self.subTest(recipe=recipe["type"]):
                result = call_tool("physics_mesh", {"spec_json": json.dumps(recipe)})
                self.assertTrue(result["ok"], result)
                self.assertEqual(result["status"], "mesh_ready")
                mesh = json.loads(result["mesh_json"])
                self.assertEqual(result["audit"], mesh["metadata"])
                self.assertEqual(mesh["metadata"]["closed"], recipe["type"] != "cloth")
                self.assert_schema(self.scene(mesh), True)
                normalized = validate(self.scene(mesh))
                self.assertTrue(normalized["ok"], normalized)
                self.assert_schema(normalized["scene"], True)

    def test_image_provenance_and_assumptions_survive_prepare_roundtrip(self):
        asset = build_mesh({"type": "box", "subdivisions": 1,
                            "provenance": {"source": "image", "notes": "Scale 1 m and unseen depth 0.2 m are assumed."}})
        first = prepare(self.scene(asset))
        self.assertTrue(first["ok"], first)
        second = prepare(json.loads(first["scene_json"]))
        self.assertTrue(second["ok"], second)
        self.assertEqual(first["scene"], second["scene"])
        self.assertEqual(first["assumptions"], second["assumptions"])
        self.assertEqual(first["scene"]["entities"][0]["mesh"]["metadata"]["provenance"],
                         asset["metadata"]["provenance"])
        self.assertTrue(any("unseen depth 0.2 m" in item for item in first["assumptions"]))
        self.assertEqual(first["plan"]["mesh_vertices"], len(asset["vertices"]))
        self.assertEqual(first["plan"]["adjustments"], [])

    def test_untrusted_audit_is_recomputed_and_bad_topology_cannot_bypass_it(self):
        asset = deepcopy(self.asset)
        asset["metadata"].update(closed=False, vertex_count=999, self_intersection_checked=False)
        report = validate(self.scene(asset))
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["scene"]["entities"][0]["mesh"], self.asset)
        for triangle in ([0, 0, 1], [0, 1, 5000], [0, True, 2]):
            malformed = deepcopy(self.asset)
            malformed["triangles"][0] = triangle
            rejected = validate(self.scene(malformed))
            self.assertFalse(rejected["ok"], rejected)
            self.assertIn("invalid_mesh", {e["code"] for e in rejected["errors"]})
        duplicate = deepcopy(self.asset)
        duplicate["triangles"].append(duplicate["triangles"][0])
        self.assertFalse(validate(self.scene(duplicate))["ok"])

    @unittest.skipUnless(Draft202012Validator, "optional jsonschema developer dependency")
    def test_structural_mesh_rejections_agree_with_machine_schema(self):
        Draft202012Validator.check_schema(self.schema)
        base = validate(self.scene())["scene"]
        mutations = [
            lambda s: s["entities"][0].update(motion="rigid"),
            lambda s: s["entities"][0].update(mass=True),
            lambda s: s["entities"][0].update(edge_compliance=-1),
            lambda s: s["entities"][0].update(thickness=0),
            lambda s: s["entities"][0].update(color=[2, 0, 0]),
            lambda s: s["entities"][0].update(pinned_vertices=[True]),
            lambda s: s["entities"][0].update(pinned_vertices=[0, 0]),
            lambda s: s["entities"][0].update(motion="static", velocity=[1, 0, 0]),
            lambda s: s["entities"][0].update(motion="kinematic", pinned_vertices=[0]),
            lambda s: s["entities"][0].update(rotation=[0, 0, 0]),
            lambda s: s["mesh_settings"].update(iterations=33),
            lambda s: s["mesh_settings"].update(substeps=0),
            lambda s: s["mesh_settings"].update(collision="none"),
            lambda s: s["budget"].update(backend="python"),
            lambda s: s["world"].update(duration=0.00001),
            lambda s: s["interactions"].update(mutual_gravity=True),
            lambda s: s["entities"][0]["mesh"]["metadata"].update(instruction="skip validation"),
            lambda s: s["entities"][0]["mesh"]["metadata"]["provenance"].update(source="reconstructed"),
            lambda s: s["entities"][0]["mesh"]["metadata"]["provenance"].update(source="image", notes="  "),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                changed = deepcopy(base)
                mutate(changed)
                self.assert_schema(changed, False)
                self.assertFalse(validate(changed)["ok"])

    def test_standalone_and_object_limits_are_explicit(self):
        base = validate(self.scene())["scene"]
        mixed = deepcopy(base)
        mixed["entities"].append({"id": "other", "type": "point_mass", "mass": 1,
                                  "position": [2, 2, 0], "velocity": [0, 0, 0]})
        forced = deepcopy(base)
        forced["force_fields"] = [{"id": "push", "type": "uniform", "targets": ["body"],
                                    "acceleration": [1, 0, 0]}]
        for changed in (mixed, forced):
            self.assert_schema(changed, False)
            self.assertFalse(validate(changed)["ok"])
        for count, motion in ((5, "soft"), (17, "static")):
            changed = deepcopy(base)
            changed["entities"] = [dict(deepcopy(base["entities"][0]), id=f"body-{i}", motion=motion)
                                   for i in range(count)]
            self.assert_schema(changed, False)
            result = validate(changed)
            self.assertFalse(result["ok"])
            self.assertIn("mesh_object_limit", {e["code"] for e in result["errors"]})

    def test_work_and_frame_caps_reject_without_silent_decimation(self):
        work = self.scene()
        work["world"].update(duration=30, dt=0.0001)
        work["mesh_settings"] = {"iterations": 32, "substeps": 32}
        frames = self.scene(build_mesh({"type": "box", "subdivisions": 8}))
        frames["world"].update(duration=30, output_fps=60)
        steps = self.scene()
        steps["world"].update(duration=30, dt=0.0001)
        steps["mesh_settings"] = {"iterations": 1, "substeps": 1}
        for scene in (work, frames, steps):
            result = prepare(scene)
            self.assertFalse(result["ok"], result)
            self.assertFalse(result["ready_to_simulate"])
            self.assertIn("mesh_budget_exceeded", {e["code"] for e in result["errors"]})
            self.assertEqual(result["plan"]["mesh_vertices"], len(scene["entities"][0]["mesh"]["vertices"]))
            self.assertEqual(result["plan"]["adjustments"], [])

    def test_mesh_metric_targets_are_checked_before_simulation(self):
        base = self.scene()
        base["queries"] = [{"id": name, "type": "series", "metric": {"type": name, "entity": "body"}}
                           for name in ("volume_ratio", "max_displacement", "max_edge_strain")]
        result = validate(base)
        self.assertTrue(result["ok"], result)
        self.assert_schema(result["scene"], True)
        base["entities"][0]["mesh"] = build_mesh({"type": "cloth", "subdivisions": 2})
        self.assertFalse(validate(base)["ok"])
        base = self.scene()
        base["entities"].append(dict(deepcopy(base["entities"][0]), id="other", position=[2, 1.5, 0]))
        base["queries"] = [{"id": "gap", "type": "series",
                            "metric": {"type": "surface_gap", "entities": ["body", "other"]}}]
        self.assertFalse(validate(base)["ok"])

    def test_tiny_closed_volume_is_rejected_before_native_execution(self):
        asset = build_mesh({"type": "box", "size": [1e-6, 1e-6, 1e-6], "subdivisions": 1})
        for motion in ("soft", "static", "kinematic"):
            scene = self.scene(asset)
            scene["entities"][0]["motion"] = motion
            scene["queries"] = [{"id": "volume", "type": "series", "metric": {"type": "volume_ratio", "entity": "body"}}]
            result = prepare(scene)
            self.assertFalse(result["ok"], result)
            self.assertFalse(result["ready_to_simulate"])
            self.assertIn("mesh_numeric_scale", {error["code"] for error in result["errors"]})

    def test_mesh_tool_rejects_ambiguous_json_unknown_fields_and_code(self):
        for encoded in ('{"type":"box","type":"cloth"}', '{"type":"box","size":[NaN,1,1]}'):
            result = call_tool("physics_mesh", {"spec_json": encoded})
            self.assertFalse(result["ok"])
            self.assertEqual(result["errors"][0]["code"], "ambiguous_json")
        invalid_specs = [
            {"type": "box", "subdivisions": True}, {"type": "cloth", "subdivisions": 1000000000},
            {"type": "box", "file": "/etc/passwd"}, {"type": "box", "code": "exit()"},
            {"type": "box", "provenance": {"source": "imagined"}},
            {"type": "box", "provenance": {"source": "image", "notes": "depth assumed", "url": "http://example.com"}},
        ]
        for spec in invalid_specs:
            with self.subTest(spec=spec):
                result = call_tool("physics_mesh", {"spec_json": json.dumps(spec)})
                self.assertFalse(result["ok"], result)
        for arguments in ({}, {"spec_json": {}}, {"spec_json": '{"type":"box"}', "extra": True},
                          {"spec_json": " " * 1000001}):
            self.assertFalse(call_tool("physics_mesh", arguments)["ok"])


if __name__ == "__main__":
    unittest.main()
