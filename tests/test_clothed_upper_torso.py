"""Safe public contract and one native quality run for the clothed torso recipe."""
from __future__ import annotations

from copy import deepcopy
import json
import tempfile
from pathlib import Path
import unittest

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None

from physics_demo.api import call_tool
from physics_demo.core.meshes import TORSO_MODEL_ASSUMPTIONS, build_mesh, normalize_mesh
from physics_demo.runner import prepare, simulate, validate


ROOT = Path(__file__).resolve().parents[1]


def torso_spec(**changes):
    spec = {
        "type": "clothed_upper_torso",
        "scope": "adult_clothed_non_explicit",
        "width": 0.46,
        "height": 0.55,
        "depth": 0.22,
        "shoulder_scale": 1.08,
        "waist_scale": 0.76,
        "soft_regions": [
            {"center": [-0.115, 0.06], "radii": [0.095, 0.11], "projection": 0.055},
            {"center": [0.115, 0.06], "radii": [0.095, 0.11], "projection": 0.055},
        ],
        "rows": 10,
        "columns": 16,
        "provenance": {
            "source": "image",
            "notes": (
                "Stylized clothed silhouette only; scale, depth, hidden back, and both "
                "front projections are assumed. No identity, age, health, or material inference."
            ),
        },
    }
    spec.update(changes)
    return spec


def torso_scene(mesh, pins):
    return {
        "version": 1,
        "name": "clothed-upper-torso-quality",
        "world": {
            "gravity": [0, -9.81, 0],
            "duration": 0.02,
            "dt": 0.01,
            "output_fps": 10,
            "bounds": {"min": [-2, -2, -2], "max": [2, 2, 2]},
        },
        "budget": {
            "wall_time_s": 60,
            "quality": "preview",
            "backend": "auto",
            "validation": "strict",
        },
        "entities": [{
            "id": "torso",
            "type": "mesh",
            "mesh": mesh,
            "motion": "soft",
            "position": [0, 0, 0],
            "velocity": [0, 0, 0],
            "mass": 1.0,
            "edge_compliance": 1e-6,
            "bending_compliance": 1e-4,
            "volume_compliance": 1e-7,
            "damping": 0.15,
            "friction": 0.35,
            "thickness": 0.01,
            "pinned_vertices": pins,
            "color": [0.65, 0.35, 0.75],
        }],
        "colliders": [],
        "queries": [{
            "id": "volume",
            "type": "series",
            "metric": {"type": "volume_ratio", "entity": "torso"},
        }],
    }


class ClothedUpperTorsoTests(unittest.TestCase):
    def test_closed_connected_outward_surface_and_public_hints(self):
        result = call_tool("physics_mesh", {"spec_json": json.dumps(torso_spec())})
        self.assertTrue(result["ok"], result)
        mesh = json.loads(result["mesh_json"])
        audit = mesh["metadata"]
        self.assertEqual(result["status"], "mesh_ready")
        self.assertEqual(audit["recipe"], "clothed_upper_torso")
        self.assertTrue(audit["closed"])
        self.assertTrue(audit["connected"])
        self.assertTrue(audit["consistent_orientation"])
        self.assertTrue(audit["self_intersection_checked"])
        self.assertGreater(audit["signed_volume"], 0)
        self.assertEqual(
            audit["vertex_count"] - audit["edge_count"] + audit["triangle_count"], 2
        )
        self.assertEqual(audit["model_assumptions"], TORSO_MODEL_ASSUMPTIONS)
        self.assertEqual(result["pin_hints"], audit["pin_hints"])
        self.assertEqual(result["entity_hints"]["motion"], "soft")
        self.assertEqual(
            result["entity_hints"]["pinned_vertices"],
            audit["pin_hints"]["upper_back_seam"],
        )
        self.assertEqual(normalize_mesh(mesh), mesh)

    def test_strict_scope_bounds_and_no_pixel_or_identity_inputs(self):
        invalid = []
        missing_scope = torso_spec()
        missing_scope.pop("scope")
        invalid.append(missing_scope)
        invalid.extend([
            torso_spec(scope="adult_or_unknown"),
            torso_spec(width=0.19),
            torso_spec(depth=1.01),
            torso_spec(columns=18),
            torso_spec(rows=True),
            torso_spec(soft_regions=torso_spec()["soft_regions"][:1]),
            torso_spec(soft_regions=list(reversed(torso_spec()["soft_regions"]))),
            torso_spec(provenance={"source": "image", "notes": " "}),
        ])
        for field in ("image", "url", "path", "identity", "age", "material"):
            value = torso_spec()
            value[field] = "untrusted"
            invalid.append(value)
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_mesh(value)

    @unittest.skipUnless(Draft202012Validator, "optional jsonschema developer dependency")
    def test_scene_schema_and_prepare_preserve_scope_assumptions(self):
        result = call_tool("physics_mesh", {"spec_json": json.dumps(torso_spec())})
        mesh = json.loads(result["mesh_json"])
        scene = torso_scene(mesh, result["entity_hints"]["pinned_vertices"])
        schema = json.loads((ROOT / "agent" / "scene-v1.schema.json").read_text())
        errors = list(Draft202012Validator(schema).iter_errors(scene))
        self.assertEqual(errors, [], "\n".join(str(error) for error in errors[:3]))
        report = validate(scene)
        self.assertTrue(report["ok"], report)
        prepared = prepare(scene)
        self.assertTrue(prepared["ready_to_simulate"], prepared)
        for assumption in TORSO_MODEL_ASSUMPTIONS:
            self.assertTrue(any(assumption in item for item in prepared["assumptions"]))
        self.assertEqual(
            prepared["scene"]["entities"][0]["pinned_vertices"],
            result["entity_hints"]["pinned_vertices"],
        )

    def test_prepare_simulate_and_mesh_quality_gate(self):
        result = call_tool("physics_mesh", {"spec_json": json.dumps(torso_spec())})
        self.assertTrue(result["ok"], result)
        mesh = json.loads(result["mesh_json"])
        scene = torso_scene(mesh, result["entity_hints"]["pinned_vertices"])
        prepared = prepare(scene)
        self.assertTrue(prepared["ok"] and prepared["ready_to_simulate"], prepared)
        with tempfile.TemporaryDirectory(prefix="torso-quality-") as directory:
            summary = simulate(prepared["scene"], directory, make_video=False)
        self.assertTrue(summary["ok"], summary)
        self.assertEqual(summary["plan"]["backend"], "mesh")
        self.assertTrue(summary["quality_gate"]["passed"], summary["quality_gate"])
        self.assertTrue(summary["quality_gate"]["numerical_passed"])
        self.assertTrue(
            summary["quality_gate"]["numerical_checks"]["mesh_frame_geometry_consistent_with_diagnostics"]
        )
        self.assertEqual(summary["diagnostics"]["closed_objects"], 1)
        self.assertEqual(summary["diagnostics"]["inverted_objects"], 0)

    def test_metadata_tampering_cannot_change_fixed_scope(self):
        mesh = build_mesh(torso_spec())
        for mutate in (
            lambda value: value["metadata"]["model_assumptions"].__setitem__(0, "anatomical claim"),
            lambda value: value["metadata"]["pin_hints"]["upper_back_seam"].append(9999),
            lambda value: value["metadata"]["pin_hints"].__setitem__("instruction", [0]),
        ):
            altered = deepcopy(mesh)
            mutate(altered)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                normalize_mesh(altered)


if __name__ == "__main__":
    unittest.main()
