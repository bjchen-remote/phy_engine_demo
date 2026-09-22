"""Analytic topology/volume checks and hostile inline-geometry boundaries."""
from __future__ import annotations

import copy
import math
import random
import unittest
from unittest.mock import patch

from physics_demo.meshes import MAX_VERTICES, audit_mesh, build_mesh, meshes_intersect, normalize_mesh
import physics_demo.meshes as mesh_module


class MeshGeometryTests(unittest.TestCase):
    def test_subdivided_box_exact_volume_area_and_euler_characteristic(self):
        mesh = build_mesh({"type": "box", "size": [6, 3, 2], "subdivisions": 3})
        data = mesh["metadata"]
        self.assertAlmostEqual(data["signed_volume"], 36)
        self.assertAlmostEqual(data["surface_area"], 72)
        self.assertEqual(data["vertex_count"] - data["edge_count"] + data["triangle_count"], 2)
        self.assertEqual(data["boundary_edges"], 0)
        self.assertEqual(data["bounds"], [[-3, -1.5, -1], [3, 1.5, 1]])
        self.assertTrue(data["self_intersection_checked"])

    def test_ellipsoid_volume_converges_to_analytic_value(self):
        exact = 4 * math.pi * 2 * 0.5 * 0.3 / 3
        coarse = build_mesh({"type": "ellipsoid", "radii": [2, 0.5, 0.3], "segments": 8, "rings": 4})
        fine = build_mesh({"type": "ellipsoid", "radii": [2, 0.5, 0.3], "segments": 24, "rings": 12})
        coarse_error = abs(coarse["metadata"]["signed_volume"] - exact)
        fine_error = abs(fine["metadata"]["signed_volume"] - exact)
        self.assertLess(fine_error, coarse_error / 5)
        self.assertLess(fine_error / exact, 0.03)

    def test_dense_ellipsoid_completes_full_audit_under_original_work_cap(self):
        asset = build_mesh({"type": "ellipsoid", "radii": [0.52, 0.56, 0.52], "segments": 64, "rings": 32})
        self.assertEqual(asset["metadata"]["vertex_count"], 1986)
        self.assertEqual(asset["metadata"]["triangle_count"], 3968)
        self.assertTrue(asset["metadata"]["self_intersection_checked"])
        self.assertEqual(mesh_module.MAX_PAIR_CHECKS, 250_000)
        self.assertEqual(mesh_module.MAX_AUDIT_SECONDS, 2.0)

    def test_bvh_agrees_with_exhaustive_pairs_on_spatially_shuffled_triangles(self):
        rng = random.Random(90210)
        for trial in range(8):
            vertices, faces = [], []
            for _ in range(24):
                center = [rng.uniform(-2, 2) for _ in range(3)]
                faces.append(list(range(len(vertices), len(vertices) + 3)))
                vertices.extend([[center[k] + rng.uniform(-0.4, 0.4) for k in range(3)] for _ in range(3)])
            if trial % 2:
                vertices[-3:] = copy.deepcopy(vertices[:3])
            rng.shuffle(faces)
            expected = any(mesh_module._triangles_intersect([vertices[v] for v in faces[i]],
                           [vertices[v] for v in faces[j]], [])
                           for i in range(len(faces)) for j in range(i + 1, len(faces)))
            actual = mesh_module._intersection_pair(vertices, faces)
            self.assertEqual(actual is not None, expected)

    def test_torus_has_handle_and_close_analytic_volume(self):
        mesh = build_mesh({"type": "torus"})
        data = mesh["metadata"]
        self.assertEqual(data["vertex_count"] - data["edge_count"] + data["triangle_count"], 0)
        self.assertAlmostEqual(data["signed_volume"] / (2 * math.pi ** 2 * 0.7 * 0.2 ** 2), 1, delta=0.06)

    def test_lathe_cylinder_and_cone_volumes(self):
        base_area = 16 * math.sin(2 * math.pi / 16) / 2
        cylinder = build_mesh({"type": "lathe", "profile": [[1, -1], [1, 1]]})
        cone = build_mesh({"type": "lathe", "profile": [[0, -1], [1, 1]]})
        self.assertAlmostEqual(cylinder["metadata"]["signed_volume"], 2 * base_area)
        self.assertAlmostEqual(cone["metadata"]["signed_volume"], 2 * base_area / 3)

    def test_concave_image_contour_preserves_explicit_inferred_depth(self):
        contour = [[0, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2]]
        provenance = {"source": "image", "notes": "Visible L silhouette; hidden depth assumed to be 0.2 m."}
        for points in (contour, list(reversed(contour))):
            mesh = build_mesh({"type": "extrusion", "contour": points, "depth": 0.2, "provenance": provenance})
            self.assertAlmostEqual(mesh["metadata"]["signed_volume"], 0.6)
            self.assertEqual(mesh["metadata"]["provenance"], provenance)

    def test_open_cloth_is_audited_but_cannot_pass_closed_requirement(self):
        mesh = build_mesh({"type": "cloth", "size": [2, 3], "subdivisions": 3})
        self.assertFalse(mesh["metadata"]["closed"])
        self.assertIsNone(mesh["metadata"]["signed_volume"])
        self.assertAlmostEqual(mesh["metadata"]["surface_area"], 6)
        self.assertEqual(mesh["metadata"]["boundary_edges"], 12)
        with self.assertRaisesRegex(ValueError, "closed"):
            audit_mesh(mesh["vertices"], mesh["triangles"], require_closed=True)

    def test_normalization_reaudits_metadata_and_is_idempotent(self):
        asset = build_mesh({"type": "box", "provenance": {"source": "imagined", "notes": "Back face and depth imagined."}})
        original = copy.deepcopy(asset)
        asset["metadata"]["signed_volume"] = 1000000
        asset["metadata"]["self_intersection_checked"] = False
        normalized = normalize_mesh(asset)
        self.assertEqual(normalized, original)
        self.assertEqual(normalize_mesh(normalized), normalized)
        normalized["vertices"][0][0] = 999
        self.assertNotEqual(normalized["vertices"][0], asset["vertices"][0])

    def test_small_translated_volume_avoids_origin_cancellation(self):
        asset = build_mesh({"type": "box", "size": [0.001, 0.001, 0.001], "subdivisions": 1})
        shifted = [[v + 999 for v in vertex] for vertex in asset["vertices"]]
        data = audit_mesh(shifted, asset["triangles"])
        self.assertAlmostEqual(data["signed_volume"] / 1e-9, 1, places=8)

    def test_invalid_numbers_indices_duplicates_and_degeneracy(self):
        base = build_mesh({"type": "box", "subdivisions": 1})
        mutations = [
            lambda m: m["vertices"][0].__setitem__(0, float("nan")),
            lambda m: m["vertices"][0].__setitem__(0, 10 ** 500),
            lambda m: m["vertices"][0].__setitem__(0, True),
            lambda m: m["triangles"][0].__setitem__(0, True),
            lambda m: m["triangles"][0].__setitem__(0, 0.0),
            lambda m: m["triangles"][0].__setitem__(0, -1),
            lambda m: m["triangles"][0].__setitem__(0, 1000),
            lambda m: m["triangles"].append(list(m["triangles"][0])),
            lambda m: m["triangles"][0].__setitem__(0, m["triangles"][0][1]),
            lambda m: m["vertices"].append(list(m["vertices"][0])),
            lambda m: m["vertices"].append([5, 5, 5]),
            lambda m: m["metadata"].__setitem__("arbitrary_command", "echo bad"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                mesh = copy.deepcopy(base)
                mutate(mesh)
                with self.assertRaises(ValueError):
                    normalize_mesh(mesh)
        with self.assertRaisesRegex(ValueError, "degenerate"):
            audit_mesh([[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[0, 1, 2]])

    def test_raw_closed_inward_winding_is_rejected_explicitly(self):
        mesh = build_mesh({"type": "box", "subdivisions": 1})
        mesh["triangles"] = [list(reversed(face)) for face in mesh["triangles"]]
        with self.assertRaisesRegex(ValueError, "outward"):
            build_mesh({"type": "raw", "vertices": mesh["vertices"], "triangles": mesh["triangles"]})
        mesh["triangles"][0].reverse()
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            normalize_mesh(mesh)

    def test_disconnected_nonmanifold_and_self_intersection_rejected(self):
        with self.assertRaisesRegex(ValueError, "connected"):
            audit_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [-1, 0, 1], [0, -1, 1]], [[0, 1, 2], [3, 4, 5]])
        with self.assertRaisesRegex(ValueError, "non-manifold"):
            audit_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [0, -1, 0]], [[0, 1, 2], [1, 0, 3], [0, 1, 4]])
        with self.assertRaisesRegex(ValueError, "self-intersect"):
            audit_mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], [[0, 1, 2], [1, 0, 3]])
        with self.assertRaisesRegex(ValueError, "self-intersect"):
            audit_mesh([[0, 0, 0], [1e-5, 0, 0], [0, 1e-5, 0], [1e-5, 1e-5, 0]], [[0, 1, 2], [1, 0, 3]])
        box = build_mesh({"type": "box", "subdivisions": 1})
        box["vertices"][0] = [0.7, 0, 0]
        with self.assertRaisesRegex(ValueError, "self-intersect"):
            normalize_mesh(box)

    def test_resource_caps_and_intersection_budget_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "exceeds"):
            audit_mesh([[0, 0, 0]] * (MAX_VERTICES + 1), [[0, 1, 2]])
        for spec in ({"type": "ellipsoid", "segments": 10 ** 100},
                     {"type": "box", "subdivisions": True},
                     {"type": "cloth", "subdivisions": 1000}):
            with self.assertRaises(ValueError):
                build_mesh(spec)
        with patch("physics_demo.meshes.MAX_PAIR_CHECKS", 1):
            with self.assertRaisesRegex(ValueError, "budget"):
                build_mesh({"type": "box", "size": [1.01, 1.02, 1.03]})
        with patch("physics_demo.meshes.time.monotonic", side_effect=[0, 3]):
            with self.assertRaisesRegex(ValueError, "time budget"):
                build_mesh({"type": "box", "size": [1.04, 1.05, 1.06]})

    def test_content_cache_never_trusts_or_shares_caller_metadata(self):
        mesh = build_mesh({"type": "box", "size": [0.33, 0.44, 0.55], "subdivisions": 1})
        metadata = audit_mesh(mesh["vertices"], mesh["triangles"])
        metadata["bounds"][0][0] = -1234
        self.assertEqual(audit_mesh(mesh["vertices"], mesh["triangles"])["bounds"], mesh["metadata"]["bounds"])
        mesh["vertices"][0] = [0.7, 0, 0]
        with self.assertRaisesRegex(ValueError, "self-intersect"):
            normalize_mesh(mesh)

    def test_recipe_and_provenance_boundaries(self):
        specs = [
            {"type": "extrusion", "contour": [[0, 0], [1, 1], [0, 1], [1, 0]]},
            {"type": "extrusion", "contour": [[0, 0], [1, 0], [2, 0], [0, 1]]},
            {"type": "torus", "major_radius": 0.2, "minor_radius": 0.4},
            {"type": "lathe", "profile": [[1, 1], [1, 0]]},
            {"type": "lathe", "profile": [[1, 0], [0, 1], [1, 2]]},
            {"type": "box", "provenance": {"source": "image"}},
            {"type": "box", "provenance": {"source": "imagined", "notes": " "}},
            {"type": "box", "path": "/some/arbitrary/file.obj"},
            {"type": "python", "code": "1 + 1"},
        ]
        for spec in specs:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                build_mesh(spec)

    def test_cross_object_contact_and_closed_containment(self):
        box = build_mesh({"type": "box", "subdivisions": 1})
        tiny = build_mesh({"type": "box", "size": [0.1, 0.1, 0.1], "subdivisions": 1})
        self.assertFalse(meshes_intersect(box, [0, 0, 0], box, [2, 0, 0]))
        self.assertTrue(meshes_intersect(box, [0, 0, 0], box, [1, 0, 0]))
        self.assertTrue(meshes_intersect(box, [0, 0, 0], box, [0.75, 0.25, 0.1]))
        self.assertTrue(meshes_intersect(box, [0, 0, 0], tiny, [0, 0, 0]))
        self.assertTrue(meshes_intersect(tiny, [0, 0, 0], box, [0, 0, 0]))

    def test_torus_hole_and_open_cloth_have_no_inferred_solid_interior(self):
        torus = build_mesh({"type": "torus"})
        tiny = build_mesh({"type": "box", "size": [0.1, 0.1, 0.1], "subdivisions": 1})
        self.assertFalse(meshes_intersect(torus, [0, 0, 0], tiny, [0, 0, 0]))
        self.assertTrue(meshes_intersect(torus, [0, 0, 0], tiny, [0.7, 0, 0]))
        cloth = build_mesh({"type": "cloth", "size": [0.1, 0.1], "subdivisions": 1})
        self.assertFalse(meshes_intersect(cloth, [0, 0, 0], cloth, [0, 1, 0]))
        self.assertTrue(meshes_intersect(tiny, [0, 0, 0], cloth, [0, 0, 0]))


if __name__ == "__main__":
    unittest.main()
