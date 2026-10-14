"""Independent conservative display cleanup proofs, without optional ML deps."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modeling_flow import display_cleanup as cleanup


FACES = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
         [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2],
         [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5]]


def cube(width=1.0, center=(0.0, 0.0, 0.0), size=None):
    size = size or (width,) * 3
    signs = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
             (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
    return {"vertices": [[center[k] + s[k] * size[k] / 2 for k in range(3)] for s in signs],
            "faces": copy.deepcopy(FACES)}


def combine(*meshes):
    result = {"vertices": [], "faces": []}
    for mesh in meshes:
        offset = len(result["vertices"])
        result["vertices"].extend(copy.deepcopy(mesh["vertices"]))
        result["faces"].extend([[i + offset for i in face] for face in mesh["faces"]])
    return result


def plate(width=.006, center=(.65, 0, 0)):
    x, y, z = center
    half = width / 2
    return {"vertices": [[x - half, y - half, z], [x + half, y - half, z],
                         [x + half, y + half, z], [x - half, y + half, z]],
            "faces": [[0, 1, 2], [0, 2, 3]]}


def subdivide(mesh):
    result = {"vertices": copy.deepcopy(mesh["vertices"]), "faces": []}
    for a, b, c in mesh["faces"]:
        center = [sum(mesh["vertices"][i][k] for i in (a, b, c)) / 3 for k in range(3)]
        index = len(result["vertices"])
        result["vertices"].append(center)
        result["faces"].extend([[a, b, index], [b, c, index], [c, a, index]])
    return result


def canonical_sha(value):
    return hashlib.sha256((json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False) + "\n").encode()).hexdigest()


class DisplayCleanupTests(unittest.TestCase):
    def test_detached_dust_removed_as_a_whole_exact_submesh(self):
        original = combine(cube(), cube(.006, (.65, 0, 0)))
        before = copy.deepcopy(original)
        cleaned, report = cleanup.clean_display_mesh(original)
        self.assertEqual(original, before)
        self.assertEqual(cleaned, cube())
        self.assertTrue(report["applied"])
        self.assertTrue(report["analysis_complete"])
        self.assertIsNone(report["skip_reason"])
        self.assertEqual(report["removed_face_indices"], list(range(12, 24)))
        self.assertEqual(report["summary"]["removed_components"], 1)
        self.assertEqual(report["summary"]["removed_vertices"], 8)
        self.assertEqual(report["raw_geometry_sha256"], canonical_sha(original))
        self.assertEqual(report["output_geometry_sha256"], canonical_sha(cleaned))
        self.assertEqual(report["removed_face_indices_sha256"], canonical_sha(list(range(12, 24))))
        self.assertTrue(cleanup.validate_cleanup(original, cleaned, report))

    def test_retain_face_order_and_compact_sorted_original_vertex_indices(self):
        original = combine(cube(.006, (.65, 0, 0)), cube())
        original["faces"] = original["faces"][::2] + original["faces"][1::2]
        cleaned, report = cleanup.clean_display_mesh(original)
        removed = set(report["removed_face_indices"])
        retained = [f for i, f in enumerate(original["faces"]) if i not in removed]
        used = sorted({v for f in retained for v in f})
        self.assertEqual(cleaned["vertices"], [original["vertices"][i] for i in used])
        self.assertEqual([[used[i] for i in f] for f in cleaned["faces"]], retained)
        self.assertTrue(cleanup.validate_cleanup(original, cleaned, report))

    def test_none_keeps_source_and_does_not_mutate_policy(self):
        source = combine(cube(), cube(.006, (.65, 0, 0)))
        cleaned, report = cleanup.clean_display_mesh(source, "none")
        self.assertEqual(cleaned, source)
        self.assertFalse(report["applied"])
        self.assertEqual(report["mode"], "none")
        self.assertEqual(report["skip_reason"], "mode_none")
        with self.assertRaises(TypeError):
            cleanup.POLICY["component_faces_max"] = 999
        report["policy"]["component_faces_max"] = 999
        self.assertEqual(cleanup.POLICY["component_faces_max"], 32)

    def test_legitimate_large_small_and_dense_accessories_survive(self):
        accessories = [cube(.1, (.65, 0, 0)), cube(.02, (.65, 0, 0)),
                       subdivide(cube(.006, (.65, 0, 0)))]
        for accessory in accessories:
            with self.subTest(faces=len(accessory["faces"])):
                source = combine(cube(), accessory)
                cleaned, report = cleanup.clean_display_mesh(source)
                self.assertEqual(cleaned, source)
                self.assertFalse(report["applied"])

    def test_open_plate_and_large_thin_closed_shell_survive(self):
        for accessory in (plate(), cube(center=(.65, 0, 0), size=(.04, .04, .00000001))):
            with self.subTest(accessory=accessory):
                source = combine(cube(), accessory)
                cleaned, report = cleanup.clean_display_mesh(source)
                self.assertEqual(cleaned, source)
                self.assertFalse(report["applied"])

    def test_near_body_tiny_button_survives_exact_surface_gate(self):
        source = combine(cube(), cube(.006, (.504, 0, 0)))
        cleaned, report = cleanup.clean_display_mesh(source)
        self.assertEqual(cleaned, source)
        self.assertFalse(report["applied"])
        self.assertIsNone(report["skip_reason"])
        self.assertTrue(cleanup.validate_cleanup(source, cleaned, report))

    def test_overlapping_disconnected_component_survives(self):
        source = combine(cube(), cube(.006, (.5, 0, 0)))
        cleaned, report = cleanup.clean_display_mesh(source)
        self.assertEqual(cleaned, source)
        self.assertFalse(report["applied"])

    def test_dominant_body_required_for_multi_object_scene(self):
        source = combine(cube(), cube(.5, (2, 0, 0)), cube(.006, (.65, 0, 0)))
        cleaned, report = cleanup.clean_display_mesh(source)
        self.assertEqual(cleaned, source)
        self.assertEqual(report["skip_reason"], "main_not_dominant")
        self.assertLess(report["summary"]["main_area_ratio"], .95)

    def test_cumulative_area_budget_overflow_keeps_every_candidate(self):
        fragments = [cube(.006, (.65 + .02 * (i % 20), .02 * (i // 20), 0))
                     for i in range(200)]
        source = combine(cube(), *fragments)
        cleaned, report = cleanup.clean_display_mesh(source)
        self.assertEqual(cleaned, source)
        self.assertEqual(report["skip_reason"], "cleanup_budget_exceeded")
        self.assertTrue(report["analysis_complete"])
        self.assertEqual(report["removed_face_indices"], [])
        self.assertEqual(report["summary"]["removed_components"], 0)

    def test_cumulative_absolute_volume_budget_overflow_keeps_every_candidate(self):
        # A broad thin body makes tiny dust volumes significant while their
        # cumulative area stays below its independent budget.
        body = cube(size=(1., 1., .001))
        fragments = [cube(.001, (.65 + .01 * (i % 20), .01 * (i // 20), 0))
                     for i in range(101)]
        source = combine(body, *fragments)
        components = cleanup._components(source["vertices"], source["faces"])
        area = sum(c["area"] for c in components)
        volume = sum(c["absolute_volume"] for c in components)
        dust_area = sum(c["area"] for c in components[1:])
        dust_volume = sum(c["absolute_volume"] for c in components[1:])
        self.assertLess(dust_area / area, cleanup.POLICY["total_removed_area_ratio_max"])
        self.assertGreater(dust_volume / volume, cleanup.POLICY["total_removed_absolute_volume_ratio_max"])
        cleaned, report = cleanup.clean_display_mesh(source)
        self.assertEqual(cleaned, source)
        self.assertEqual(report["skip_reason"], "cleanup_budget_exceeded")
        self.assertEqual(report["summary"]["removed_components"], 0)

    def test_fixed_operation_budget_and_incomplete_analysis_fail_closed(self):
        tri = ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.))
        boxes = [cleanup._box(tri)]
        root = cleanup._Node([0], boxes, [(0., 0., 0.)])
        with self.assertRaises(cleanup._AnalysisBudgetExceeded):
            cleanup._gap_passes([tri], root, [tri], .1,
                                {"nodes": cleanup.POLICY["max_bvh_node_visits"], "pairs": 0})
        source = combine(cube(), cube(.006, (.65, 0, 0)))
        with patch.object(cleanup, "_gap_passes", side_effect=cleanup._AnalysisBudgetExceeded):
            cleaned, report = cleanup.clean_display_mesh(source)
        self.assertEqual(cleaned, source)
        self.assertFalse(report["analysis_complete"])
        self.assertFalse(report["applied"])
        self.assertEqual(report["skip_reason"], "analysis_budget_exceeded")

    def test_unreferenced_vertex_keeps_source_without_implicit_compaction(self):
        source = combine(cube(), cube(.006, (.65, 0, 0)))
        source["vertices"].append([3., 4., 5.])
        cleaned, report = cleanup.clean_display_mesh(source)
        self.assertEqual(cleaned, source)
        self.assertEqual(report["skip_reason"], "unreferenced_vertices")

    def test_single_open_triangle_and_degenerate_triangle_are_kept(self):
        for source in ({"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "faces": [[0, 1, 2]]},
                       {"vertices": [[0, 0, 0], [1, 0, 0], [2, 0, 0]], "faces": [[0, 1, 2]]}):
            cleaned, report = cleanup.clean_display_mesh(source)
            self.assertEqual(cleaned, source)
            self.assertFalse(report["applied"])
            self.assertTrue(cleanup.validate_cleanup(source, cleaned, report))

    def test_canonical_report_rejects_tampered_policy_counts_indices_and_extra_fields(self):
        source = combine(cube(), cube(.006, (.65, 0, 0)))
        cleaned, report = cleanup.clean_display_mesh(source)
        changes = []
        modified = copy.deepcopy(report)
        modified["summary"]["removed_components"] += 1
        changes.append(modified)
        modified = copy.deepcopy(report)
        modified["policy"]["component_faces_max"] = 64
        modified["policy_sha256"] = canonical_sha(modified["policy"])
        changes.append(modified)
        modified = copy.deepcopy(report)
        modified["removed_face_indices"] = modified["removed_face_indices"][1:]
        modified["removed_face_indices_sha256"] = canonical_sha(modified["removed_face_indices"])
        changes.append(modified)
        modified = copy.deepcopy(report)
        modified["applied"] = 1
        changes.append(modified)
        modified = copy.deepcopy(report)
        modified["extra"] = "unapproved"
        changes.append(modified)
        for modified in changes:
            with self.subTest(modified=modified), self.assertRaises(cleanup.DisplayCleanupError):
                cleanup.validate_cleanup(source, cleaned, modified)

    def test_geometry_hashes_do_not_authorise_coordinate_or_face_changes(self):
        source = combine(cube(), cube(.006, (.65, 0, 0)))
        cleaned, report = cleanup.clean_display_mesh(source)
        for kind in ("coordinate", "winding", "whole_source"):
            modified = copy.deepcopy(cleaned)
            if kind == "coordinate":
                modified["vertices"][0][0] += .0000001
            elif kind == "winding":
                modified["faces"][0].reverse()
            else:
                modified = copy.deepcopy(source)
            forged = copy.deepcopy(report)
            forged["output_geometry_sha256"] = canonical_sha(modified)
            with self.subTest(kind=kind), self.assertRaises(cleanup.DisplayCleanupError):
                cleanup.validate_cleanup(source, modified, forged)

    def test_invalid_mesh_and_mode_are_rejected(self):
        for source in ({"vertices": [[0, 0, 0]] * 3, "faces": [[0, 0, 2]]},
                       {"vertices": [[0, 0, 0], [1, 0, 0], [float("nan"), 1, 0]], "faces": [[0, 1, 2]]}):
            with self.assertRaises(cleanup.DisplayCleanupError):
                cleanup.clean_display_mesh(source)
        with self.assertRaises(cleanup.DisplayCleanupError):
            cleanup.clean_display_mesh(cube(), "largest_component")


class SurfaceDistanceTests(unittest.TestCase):
    def test_true_surface_intersection_parallel_gap_and_coplanar_gap(self):
        a = ((0., 0., 0.), (1., 0., 0.), (0., 1., 0.))
        parallel = tuple((x, y, z + 2) for x, y, z in a)
        crossing = ((.25, .25, -1.), (.25, .25, 1.), (.5, .25, 0.))
        coplanar = tuple((x + 2, y, z) for x, y, z in a)
        for b, expected in ((parallel, 4.), (crossing, 0.), (coplanar, 1.)):
            with self.subTest(b=b):
                self.assertAlmostEqual(cleanup._triangle_distance2(a, b), expected)
                self.assertAlmostEqual(cleanup._triangle_distance2(b, a), expected)

    def test_edge_to_edge_minimum_is_not_vertex_only(self):
        # The two long perpendicular edges cross in XY but have Z separation;
        # their vertices are all much farther away than the true surface gap.
        a = ((-2., 0., 0.), (2., 0., 0.), (0., -.1, 0.))
        b = ((0., -2., .1), (0., 2., .1), (.1, 0., .1))
        self.assertAlmostEqual(cleanup._triangle_distance2(a, b), .01)
        self.assertGreater(min(cleanup._length2(cleanup._sub(x, y)) for x in a for y in b), .01)

    def test_degenerate_main_triangle_falls_back_to_segments(self):
        degenerate = ((0., 0., 0.), (1., 0., 0.), (2., 0., 0.))
        self.assertAlmostEqual(cleanup._point_triangle_distance2((.5, 0., 1.), degenerate), 1.)


if __name__ == "__main__":
    unittest.main()
