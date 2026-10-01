"""Numerical micro-component policy and real native mesh reduction regressions."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from modeling_flow.contracts import FlowError
from modeling_flow.meshes import audit_mesh
from modeling_flow.reduction import numerical_component_cleanup, topology_decimate
from modeling_flow import runner


CUBE = {"vertices": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                     [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
        "faces": [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
                  [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                  [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]}


def box(size=(1, 1, 1), offset=(0, 0, 0), reverse=False):
    return {"vertices": [[point[axis] * size[axis] + offset[axis] for axis in range(3)]
                         for point in CUBE["vertices"]],
            "faces": [[a, c, b] if reverse else [a, b, c] for a, b, c in CUBE["faces"]]}


def combine(*meshes):
    vertices, faces = [], []
    for mesh in meshes:
        faces.extend([[index + len(vertices) for index in face] for face in mesh["faces"]])
        vertices.extend(copy.deepcopy(mesh["vertices"]))
    return {"vertices": vertices, "faces": faces}


HAS_ARRAYS = all(importlib.util.find_spec(name) for name in ("numpy", "scipy"))
HAS_NATIVE = HAS_ARRAYS and all(importlib.util.find_spec(name) for name in ("pymeshlab", "trimesh"))


@unittest.skipUnless(HAS_ARRAYS, "Pinned modeling numpy/scipy runtime required")
class NumericalComponentTests(unittest.TestCase):
    def test_only_micro_component_removed_with_reproducible_loss_receipt(self):
        original = combine(box(), box((0.0005,) * 3, (0.2,) * 3, reverse=True))
        untouched = copy.deepcopy(original)
        cleaned, report = numerical_component_cleanup(original)
        self.assertEqual(original, untouched)
        self.assertEqual(cleaned, box())
        self.assertTrue(report["applied"])
        self.assertEqual(report["source_components"], 2)
        self.assertEqual(report["remaining_components"], 1)
        self.assertEqual(len(report["removed_components"]), 1)
        fragment = report["removed_components"][0]
        self.assertEqual((fragment["vertices"], fragment["faces"]), (8, 12))
        self.assertLess(fragment["signed_volume_m3"], 0)
        self.assertAlmostEqual(fragment["extent_ratio"], 0.0005)
        self.assertGreater(report["removed_area_ratio"], 0)
        self.assertGreater(report["removed_absolute_volume_ratio"], 0)
        self.assertTrue(report["display_geometry_preserved"])
        self.assertTrue(audit_mesh(cleaned, simulation=True)["simulation_eligible"])

    def test_significant_independent_object_and_internal_cavity_survive(self):
        for other in (box((0.1,) * 3, (0.3,) * 3),
                      box((0.01,) * 3, (0.3,) * 3, reverse=True)):
            original = combine(box(), other)
            cleaned, report = numerical_component_cleanup(original)
            self.assertEqual(cleaned, original)
            self.assertFalse(report["applied"])
            self.assertEqual(report["remaining_components"], 2)
            self.assertFalse(audit_mesh(cleaned, simulation=True)["simulation_eligible"])

    def test_large_thin_surfaces_are_not_decided_by_volume_or_face_count(self):
        original = combine(box(), box((0.2, 0.2, 1e-12), (0.1, 0.1, 0.3)))
        cleaned, report = numerical_component_cleanup(original)
        self.assertEqual(cleaned, original)
        self.assertFalse(report["applied"])

    def test_large_relative_volume_prevents_cleanup_even_with_tiny_extent_and_area(self):
        original = combine(box((1, 1, 1e-6)), box((0.0009, 0.0009, 0.9e-6), (0.2, 0.2, 0)))
        cleaned, report = numerical_component_cleanup(original)
        self.assertEqual(cleaned, original)
        self.assertFalse(report["applied"])

    def test_external_micro_object_is_preserved(self):
        original = combine(box(), box((1e-5,) * 3, (2, 0.2, 0.2)))
        cleaned, report = numerical_component_cleanup(original)
        self.assertEqual(cleaned, original)
        self.assertFalse(report["applied"])

    def test_aggregate_micro_loss_cannot_exceed_budget(self):
        original = combine(box(), *(box((0.0009,) * 3, (0.1 + i * 0.02, 0.2, 0.2)) for i in range(20)))
        cleaned, report = numerical_component_cleanup(original)
        self.assertEqual(cleaned, original)
        self.assertFalse(report["applied"])
        self.assertEqual(report["reason"], "aggregate_cleanup_budget_exceeded")

    def test_no_dominant_surface_is_never_replaced_with_largest_component(self):
        original = combine(box(), box(offset=(2, 0, 0)))
        cleaned, report = numerical_component_cleanup(original)
        self.assertEqual(cleaned, original)
        self.assertEqual(report["reason"], "no_dominant_single_surface")

    def test_significant_components_reject_before_native_reduction(self):
        original = combine(box(), box((0.1,) * 3, (0.3,) * 3))
        with patch.object(runner, "topology_decimate") as decimate:
            with self.assertRaises(FlowError) as rejected:
                runner._decimate(original, Mock(), {"allow_decimation": True})
        decimate.assert_not_called()
        self.assertEqual(rejected.exception.code, "simulation_mesh_rejected")
        self.assertEqual(rejected.exception.details["prepared_audit"]["connected_components"], 2)
        self.assertFalse(rejected.exception.details["numerical_cleanup"]["applied"])

    def test_within_budget_cleanup_is_disclosed_and_opt_out_is_respected(self):
        original = combine(box(), box((0.0005,) * 3, (0.2,) * 3))
        mesh, report = runner._decimate(original, Mock(), {"allow_decimation": True})
        self.assertTrue(report["numerical_cleanup"]["applied"])
        self.assertFalse(report["applied"])
        self.assertEqual(mesh, box())
        with patch.object(runner, "numerical_component_cleanup") as cleanup:
            with self.assertRaises(FlowError):
                runner._decimate(original, Mock(), {"allow_decimation": False})
        cleanup.assert_not_called()


@unittest.skipUnless(HAS_NATIVE, "Pinned modeling PyMeshLab runtime required")
class NativeReductionTests(unittest.TestCase):
    def test_real_sphere_reduction_preserves_original_vertex_placements_and_engine_gate(self):
        import trimesh
        from physics_demo.core.meshes import audit_mesh as engine_audit
        source = trimesh.creation.icosphere(subdivisions=5, radius=0.1)
        original = {"vertices": source.vertices.tolist(), "faces": source.faces.tolist()}
        reduced = topology_decimate(original, 6000)
        report = audit_mesh(reduced, simulation=True)
        self.assertTrue(report["simulation_eligible"], report)
        self.assertEqual(report["euler_characteristic"], audit_mesh(original)["euler_characteristic"])
        source_vertices = {tuple(point) for point in original["vertices"]}
        self.assertTrue(all(tuple(point) in source_vertices for point in reduced["vertices"]))
        self.assertTrue(engine_audit(reduced["vertices"], reduced["faces"], require_closed=True)["self_intersection_checked"])

    def test_real_torus_retains_handle_and_rejects_disconnected_parts(self):
        import trimesh
        source = trimesh.creation.torus(major_radius=0.08, minor_radius=0.025,
                                      major_sections=128, minor_sections=64)
        original = {"vertices": source.vertices.tolist(), "faces": source.faces.tolist()}
        reduced = topology_decimate(original, 6000)
        report = audit_mesh(reduced, simulation=True)
        self.assertTrue(report["simulation_eligible"], report)
        self.assertEqual(report["euler_characteristic"], 0)
        self.assertEqual(report["connected_components"], 1)


if __name__ == "__main__":
    unittest.main()
