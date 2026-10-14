"""Closed-mesh volume admission, accelerated sampling and reproducible receipts."""
from __future__ import annotations

import copy
import math
import unittest
from unittest.mock import patch

from physics_demo.core.meshes import build_mesh, _contains
from physics_demo.core import volume_sampling as sampling


def shape(mesh=None, center=None):
    return {"type": "mesh_volume", "mesh": mesh or build_mesh(
        {"type": "box", "size": [.2, .2, .2], "subdivisions": 1}),
        "center": [0., 0., 0.] if center is None else center, "units": "m",
        "conversion": "instant", "conservation": "volume", "reference_density_kg_m3": 1000}


class VolumeSamplingTests(unittest.TestCase):
    def setUp(self):
        sampling._SAMPLE_CACHE.clear()

    def test_analytic_box_count_mass_and_spacing(self):
        result = sampling.sample_mesh_volume(shape(), .02)
        points, receipt = result["points"], result["receipt"]
        self.assertEqual(len(points), 1000)
        self.assertEqual(receipt["N"], len(points))
        self.assertAlmostEqual(receipt["source_volume_m3"], .008)
        self.assertAlmostEqual(receipt["represented_volume_m3"], .008)
        self.assertAlmostEqual(receipt["represented_mass_kg"], 8.)
        self.assertLess(receipt["relative_volume_error"], 1e-12)
        self.assertEqual(receipt["component_coverage"]["sampled_lattice_components"], 1)
        self.assertEqual(len({tuple(p) for p in points}), 1000)
        xs = sorted({p[0] for p in points})
        for a, b in zip(xs, xs[1:]):
            self.assertAlmostEqual(b - a, .02)
        self.assertLess(receipt["triangle_queries"], receipt["candidate_cells"] * 12)

    def test_tetrahedron_volume_and_inside_points(self):
        # Outward faces for x,y,z>=0 and x+y+z<=.2.
        mesh = {"vertices": [[0, 0, 0], [.2, 0, 0], [0, .2, 0], [0, 0, .2]],
                "triangles": [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]}
        result = sampling.sample_mesh_volume(shape(mesh), .02)
        self.assertEqual(result["receipt"]["N"], 165)
        self.assertAlmostEqual(result["receipt"]["source_volume_m3"], .2 ** 3 / 6)
        self.assertLess(result["receipt"]["relative_volume_error"], .02)
        for point in result["points"]:
            self.assertGreaterEqual(min(point), 0)
            self.assertLessEqual(sum(point), .2 + 1e-14)

    def test_torus_preserves_hole_and_refinement_improves_volume(self):
        mesh = build_mesh({"type": "torus", "major_radius": .08, "minor_radius": .025,
                           "segments": 16, "tube_segments": 8})
        coarse = sampling.sample_mesh_volume(shape(mesh), .01)
        fine = sampling.sample_mesh_volume(shape(mesh), .005)
        self.assertEqual(coarse["receipt"]["N"], 892)
        self.assertEqual(fine["receipt"]["N"], 6968)
        self.assertLess(fine["receipt"]["relative_volume_error"], coarse["receipt"]["relative_volume_error"])
        for point in coarse["points"]:
            self.assertGreater(math.hypot(point[0], point[2]), .05)
        for point in coarse["points"][::31]:
            self.assertTrue(_contains(mesh["vertices"], mesh["triangles"], point))
        self.assertLess(coarse["receipt"]["triangle_queries"], 6000)

    def test_rotated_mesh_matches_independent_solid_angle_reference(self):
        mesh = shape()["mesh"]
        a, b = .37, .21
        mesh["vertices"] = [[math.cos(b) * (math.cos(a) * x + math.sin(a) * z) - math.sin(b) * y + .017,
                             math.sin(b) * (math.cos(a) * x + math.sin(a) * z) + math.cos(b) * y - .039,
                             -math.sin(a) * x + math.cos(a) * z + .012]
                            for x, y, z in mesh["vertices"]]
        result = sampling.sample_mesh_volume(shape(mesh), .01)
        lo = [min(p[k] for p in mesh["vertices"]) for k in range(3)]
        hi = [max(p[k] for p in mesh["vertices"]) for k in range(3)]
        counts = [math.ceil((b - a) / .01) for a, b in zip(lo, hi)]
        expected = sum(_contains(mesh["vertices"], mesh["triangles"],
                                 [lo[0] + (i + .5) * .01, lo[1] + (j + .5) * .01, lo[2] + (k + .5) * .01])
                       for i in range(counts[0]) for j in range(counts[1]) for k in range(counts[2]))
        self.assertEqual(result["receipt"]["N"], expected)
        self.assertEqual(expected, 8025)

    def test_scanline_shared_edge_and_tangent_boundary(self):
        # Two x-normal faces meeting on a projected diagonal must contribute
        # exactly one crossing, even when a ray hits the shared edge or vertex.
        a = (((0., 0.), (1., 0.), (1., 1.)), (0., 0., 0.), 1., 1)
        b = (((0., 0.), (1., 1.), (0., 1.)), (0., 0., 0.), 1., 1)
        exit_a = (a[0], (1., 1., 1.), 1., -1)
        exit_b = (b[0], (1., 1., 1.), 1., -1)
        triangles = [a, b, exit_a, exit_b]
        root = sampling._build_bvh(triangles)
        self.assertEqual(sampling._intervals(root, triangles, .5, .5, [0, 0]), [(0., 1.)])
        self.assertEqual(sampling._intervals(root, triangles, 1., .5, [0, 0]), [(0., 1.)])
        self.assertEqual(sampling._intervals(root, triangles, -.01, .5, [0, 0]), [])
        self.assertEqual(sampling._intervals(root, triangles, 1.01, .5, [0, 0]), [])

    def test_thin_bridge_disconnection_is_rejected_despite_small_volume_error(self):
        contour = [[-.15, -.05], [-.05, -.05], [-.05, -.003], [.05, -.003],
                   [.05, -.05], [.15, -.05], [.15, .05], [.05, .05],
                   [.05, .003], [-.05, .003], [-.05, .05], [-.15, .05]]
        mesh = build_mesh({"type": "extrusion", "contour": contour, "depth": .1})
        with self.assertRaisesRegex(sampling.VolumeSamplingError, "feature_coverage"):
            sampling.sample_mesh_volume(shape(mesh), .01)
        fine = sampling.sample_mesh_volume(shape(mesh), .005)
        self.assertEqual(fine["receipt"]["component_coverage"]["sampled_lattice_components"], 1)

    def test_thin_appendage_cannot_disappear_behind_volume_and_connectivity_gates(self):
        contour = [[-.1, -.1], [.1, -.1], [.1, -.003], [.2, -.003],
                   [.2, .003], [.1, .003], [.1, .1], [-.1, .1]]
        mesh = build_mesh({"type": "extrusion", "contour": contour, "depth": .1})
        # At .01 m the body gives 4000 connected particles and only 1.48%
        # volume error, but the whole .1 m appendage has no particles.
        with self.assertRaisesRegex(sampling.VolumeSamplingError, "feature_coverage"):
            sampling.sample_mesh_volume(shape(mesh), .01)
        self.assertFalse(sampling._SAMPLE_CACHE)
        fine = sampling.sample_mesh_volume(shape(mesh), .005)
        self.assertTrue(any(point[0] > .1 for point in fine["points"]))
        coverage = fine["receipt"]["source_surface_coverage"]
        self.assertEqual(coverage["samples"], len(mesh["vertices"]) + len(mesh["triangles"]))
        self.assertEqual(coverage["status"], "passed")
        self.assertLessEqual(coverage["maximum_nearest_particle_distance_spacing_ratio"], 2.)
        self.assertFalse(coverage["certified_continuous_surface_coverage"])

    def test_source_coverage_budgets_apply_to_fresh_and_cached_samples(self):
        for name in ("MAX_COVERAGE_CELL_VISITS", "MAX_COVERAGE_POINT_CHECKS"):
            sampling._SAMPLE_CACHE.clear()
            with patch.object(sampling, name, 1):
                with self.assertRaisesRegex(sampling.VolumeSamplingError, "coverage_budget"):
                    sampling.sample_mesh_volume(shape(), .02)
            self.assertFalse(sampling._SAMPLE_CACHE)
            sampling.sample_mesh_volume(shape(), .02)
            with patch.object(sampling, name, 1):
                with self.assertRaisesRegex(sampling.VolumeSamplingError, "coverage_budget"):
                    sampling.sample_mesh_volume(shape(), .02)

    def test_cached_source_coverage_cannot_bypass_distance_tolerance(self):
        sampling.sample_mesh_volume(shape(), .02)
        with patch.object(sampling, "MAX_SOURCE_COVERAGE_DISTANCE_SPACING_RATIO", .1):
            with self.assertRaisesRegex(sampling.VolumeSamplingError, "feature_coverage"):
                sampling.sample_mesh_volume(shape(), .02)

    def test_translation_changes_only_world_points_and_particle_hash(self):
        first = sampling.sample_mesh_volume(shape(), .02)
        shifted = sampling.sample_mesh_volume(shape(center=[1., 2., -3.]), .02)
        self.assertEqual(first["receipt"]["source_mesh_sha256"], shifted["receipt"]["source_mesh_sha256"])
        self.assertNotEqual(first["receipt"]["particle_sha256"], shifted["receipt"]["particle_sha256"])
        self.assertEqual(first["receipt"]["N"], shifted["receipt"]["N"])
        for a, b in zip(first["points"], shifted["points"]):
            for k, delta in enumerate((1, 2, -3)):
                self.assertAlmostEqual(b[k] - a[k], delta)

    def test_deterministic_receipt_and_no_shared_mutable_cache_state(self):
        original = sampling.sample_mesh_volume(shape(), .02)
        expected = copy.deepcopy(original)
        original["points"][0][0] = 999
        original["receipt"]["N"] = -1
        again = sampling.sample_mesh_volume(shape(), .02)
        self.assertEqual(again, expected)
        sampling._SAMPLE_CACHE.clear()
        self.assertEqual(sampling.sample_mesh_volume(shape(), .02), expected)

    def test_metadata_claims_never_authorize_or_change_hash(self):
        source = shape()
        expected = sampling.sample_mesh_volume(source, .02)
        source["mesh"]["metadata"] = {"closed": False, "signed_volume": -100,
                                         "self_intersection_checked": False, "provenance": {"notes": "untrusted"}}
        actual = sampling.sample_mesh_volume(source, .02)
        self.assertEqual(actual, expected)
        qualified = sampling.qualify_mesh(source["mesh"])
        self.assertTrue(qualified["metadata"]["closed"])
        self.assertAlmostEqual(qualified["metadata"]["signed_volume"], .008)

    def test_open_source_rejected_even_with_forged_closed_metadata(self):
        source = shape()
        source["mesh"]["triangles"].pop()
        source["mesh"]["metadata"] = {"closed": True, "signed_volume": .008}
        with self.assertRaisesRegex(ValueError, "closed mesh"):
            sampling.sample_mesh_volume(source, .02)

    def test_self_intersection_rejected(self):
        source = shape()
        source["mesh"]["vertices"][0] = [.2, .02, .01]
        with self.assertRaisesRegex(ValueError, "self-intersect"):
            sampling.sample_mesh_volume(source, .02)

    def test_inward_source_requires_explicit_face_reversal(self):
        source = shape()
        source["mesh"]["triangles"] = [[a, c, b] for a, b, c in source["mesh"]["triangles"]]
        with self.assertRaisesRegex(ValueError, "positive outward"):
            sampling.sample_mesh_volume(source, .02)

    def test_source_geometry_limits(self):
        source = shape()
        source["mesh"]["vertices"] *= 513
        with self.assertRaisesRegex(ValueError, "4096 vertices"):
            sampling.qualify_mesh(source["mesh"])
        source = shape()
        source["mesh"]["triangles"] *= 683
        with self.assertRaisesRegex(ValueError, "8192 triangles"):
            sampling.qualify_mesh(source["mesh"])

    def test_candidate_budget_refuses_without_truncating(self):
        with self.assertRaisesRegex(sampling.VolumeSamplingError, "candidate_budget"):
            sampling.sample_mesh_volume(shape(), .001)
        self.assertFalse(sampling._SAMPLE_CACHE)

    def test_triangle_and_node_work_budgets(self):
        for name in ("MAX_TRIANGLE_QUERIES", "MAX_NODE_VISITS"):
            sampling._SAMPLE_CACHE.clear()
            with patch.object(sampling, name, 1):
                with self.assertRaisesRegex(sampling.VolumeSamplingError, "query_budget"):
                    sampling.sample_mesh_volume(shape(), .02)
        self.assertFalse(sampling._SAMPLE_CACHE)

    def test_cache_does_not_bypass_work_budgets(self):
        sampling.sample_mesh_volume(shape(), .02)
        with patch.object(sampling, "MAX_TRIANGLE_QUERIES", 1):
            with self.assertRaisesRegex(sampling.VolumeSamplingError, "query_budget"):
                sampling.sample_mesh_volume(shape(), .02)
        with patch.object(sampling, "MAX_CANDIDATE_CELLS", 1):
            with self.assertRaisesRegex(sampling.VolumeSamplingError, "candidate_budget"):
                sampling.sample_mesh_volume(shape(), .02)

    def test_volume_error_is_hard_gate(self):
        with self.assertRaisesRegex(sampling.VolumeSamplingError, "volume_error"):
            sampling.sample_mesh_volume(shape(), .03)
        self.assertFalse(sampling._SAMPLE_CACHE)
        valid = sampling.sample_mesh_volume(shape(), .025)
        self.assertLessEqual(valid["receipt"]["relative_volume_error"], .05)

    def test_explicit_units_conversion_density_and_center_required(self):
        for field in ("units", "conversion", "conservation", "reference_density_kg_m3", "center"):
            source = shape()
            del source[field]
            with self.assertRaises(ValueError):
                sampling.sample_mesh_volume(source, .02)
        for field, value in (("units", "model_unit"), ("conversion", "melting"),
                             ("conservation", "mass"), ("reference_density_kg_m3", 1200),
                             ("center", [float("nan"), 0, 0])):
            source = shape(); source[field] = value
            with self.assertRaises(ValueError):
                sampling.sample_mesh_volume(source, .02)
        for spacing in (0, -1, True, float("nan"), float("inf"), .5001, 10 ** 400):
            with self.assertRaises(ValueError):
                sampling.sample_mesh_volume(shape(), spacing)

    def test_source_receipt_pair_is_inert_and_validated(self):
        source = shape()
        expected = sampling.sample_mesh_volume(source, .02)
        source.update(source_ref="a" * 64, receipt_sha256="b" * 64)
        self.assertEqual(sampling.sample_mesh_volume(source, .02), expected)
        del source["receipt_sha256"]
        with self.assertRaisesRegex(ValueError, "paired SHA-256"):
            sampling.sample_mesh_volume(source, .02)

    def test_cache_is_bounded_by_entries_and_total_points(self):
        with patch.object(sampling, "MAX_CACHE_ENTRIES", 2), patch.object(sampling, "MAX_CACHE_POINTS", 1500):
            for x in range(4):
                sampling.sample_mesh_volume(shape(center=[x, 0, 0]), .02)
                self.assertLessEqual(len(sampling._SAMPLE_CACHE), 2)
                self.assertLessEqual(sum(len(v["points"]) for v in sampling._SAMPLE_CACHE.values()), 1500)


if __name__ == "__main__":
    unittest.main()
