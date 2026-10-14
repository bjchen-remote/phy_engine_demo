"""Bounded surface cleanup fixtures; no image model or optional dependency."""
from __future__ import annotations

import copy
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modeling_flow import surface_cleanup as cleanup


CUBE_FACES = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
              [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2],
              [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5]]


def cube(width=1., center=(0., 0., 0.)):
    signs = [(-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
             (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]
    return {"vertices": [[center[k] + sign[k] * width / 2 for k in range(3)] for sign in signs],
            "faces": copy.deepcopy(CUBE_FACES)}


def plate(width=.006, center=(.65, 0., 0.)):
    x, y, z = center
    half = width / 2
    return {"vertices": [[x - half, y - half, z], [x + half, y - half, z],
                         [x + half, y + half, z], [x - half, y + half, z]],
            "faces": [[0, 1, 2], [0, 2, 3]]}


def combine(*meshes):
    result = {"vertices": [], "faces": []}
    for mesh in meshes:
        offset = len(result["vertices"])
        result["vertices"].extend(copy.deepcopy(mesh["vertices"]))
        result["faces"].extend([[index + offset for index in face] for face in mesh["faces"]])
    return result


def noisy_sphere(subdivisions=2):
    t = (1 + math.sqrt(5)) / 2
    vertices = [[-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0],
                [0, -1, t], [0, 1, t], [0, -1, -t], [0, 1, -t],
                [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1]]
    faces = [[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11],
             [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8],
             [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9],
             [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]]
    def normalize(vertex):
        norm = math.sqrt(sum(value * value for value in vertex))
        return [value / norm for value in vertex]
    vertices = [normalize(vertex) for vertex in vertices]
    for _ in range(subdivisions):
        cache, next_faces = {}, []
        def midpoint(a, b):
            key = min(a, b), max(a, b)
            if key not in cache:
                cache[key] = len(vertices)
                vertices.append(normalize([(vertices[a][k] + vertices[b][k]) / 2 for k in range(3)]))
            return cache[key]
        for a, b, c in faces:
            ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
            next_faces.extend([[a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]])
        faces = next_faces
    vertices = [[value * (1 + .006 * math.sin(index * 2.31)) for value in vertex] for index, vertex in enumerate(vertices)]
    return {"vertices": vertices, "faces": faces}


def noisy_patch(side=9):
    vertices = []
    for y in range(side):
        for x in range(side):
            z = .006 * math.sin(2.17 * x + 1.31 * y) if 0 < x < side - 1 and 0 < y < side - 1 else 0.
            vertices.append([x / (side - 1), y / (side - 1), z])
    faces = []
    for y in range(side - 1):
        for x in range(side - 1):
            a = y * side + x
            faces.extend([[a, a + 1, a + side + 1], [a, a + side + 1, a + side]])
    return {"vertices": vertices, "faces": faces}


def radial_spread(mesh):
    radii = [math.sqrt(sum(value * value for value in vertex)) for vertex in mesh["vertices"]]
    average = math.fsum(radii) / len(radii)
    return math.sqrt(math.fsum((radius - average) ** 2 for radius in radii) / len(radii))


class SurfaceCleanupTests(unittest.TestCase):
    def assert_orientation(self, raw, clean, report):
        mapping = report["original_to_output_vertex_indices"]
        removed = set(report["removed_face_indices"])
        old_faces = [face for index, face in enumerate(raw["faces"]) if index not in removed]
        self.assertEqual(clean["faces"], [[mapping[index] for index in face] for face in old_faces])
        for old_face, new_face in zip(old_faces, clean["faces"]):
            a, b, c = (raw["vertices"][i] for i in old_face)
            before = cleanup._cross(cleanup._sub(b, a), cleanup._sub(c, a))
            if cleanup._length2(before):
                a, b, c = (clean["vertices"][i] for i in new_face)
                after = cleanup._cross(cleanup._sub(b, a), cleanup._sub(c, a))
                self.assertGreater(cleanup._dot(before, after), 0.)

    def test_noisy_closed_sphere_smooths_with_bounded_displacement_and_topology(self):
        source = noisy_sphere()
        untouched = copy.deepcopy(source)
        clean, report = cleanup.clean_surface_mesh(source)
        self.assertEqual(source, untouched)
        self.assertTrue(report["applied"])
        self.assertGreater(report["summary"]["moved_vertices"], 0)
        self.assertLess(radial_spread(clean), radial_spread(source))
        self.assertEqual(report["retained_topology_before"], report["retained_topology_after"])
        self.assertTrue(report["retained_topology_after"]["closed_oriented"])
        self.assertLessEqual(report["smoothing"]["max_displacement_main_extent_ratio"], .012 + 1e-12)
        self.assert_orientation(source, clean, report)
        self.assertTrue(cleanup.validate_surface_cleanup(source, clean, report))

    def test_rough_open_patch_keeps_boundary_one_ring_and_reduces_roughness(self):
        source = noisy_patch()
        clean, report = cleanup.clean_surface_mesh(source)
        frozen = {y * 9 + x for y in range(9) for x in range(9) if x <= 1 or y <= 1 or x >= 7 or y >= 7}
        for index in frozen:
            self.assertEqual(clean["vertices"][index], source["vertices"][index])
        before = math.fsum(vertex[2] ** 2 for vertex in source["vertices"])
        after = math.fsum(vertex[2] ** 2 for vertex in clean["vertices"])
        self.assertLess(after, before)
        self.assertEqual(clean["faces"], source["faces"])
        self.assertGreater(report["smoothing"]["protected_seed_reason_counts"]["boundary"], 0)
        self.assertGreater(report["retained_topology_after"]["boundary_edges"], 0)
        self.assert_orientation(source, clean, report)

    def test_open_flat_and_closed_micro_debris_removed_whole(self):
        for debris in (plate(), cube(.006, (.65, 0., 0.))):
            with self.subTest(faces=len(debris["faces"])):
                source = combine(cube(), debris)
                clean, report = cleanup.clean_surface_mesh(source)
                self.assertEqual(clean, cube())
                self.assertEqual(report["removed_face_indices"], list(range(12, len(source["faces"]))))
                self.assertEqual(report["summary"]["removed_components"], 1)
                self.assertEqual(report["original_to_output_vertex_indices"][:8], list(range(8)))
                self.assertTrue(all(index is None for index in report["original_to_output_vertex_indices"][8:]))
                self.assertTrue(cleanup.validate_surface_cleanup(source, clean, report))

    def test_component_compaction_preserves_original_face_order_and_mapping(self):
        source = combine(plate(), cube())
        source["faces"] = source["faces"][::2] + source["faces"][1::2]
        clean, report = cleanup.clean_surface_mesh(source)
        self.assertEqual(report["original_to_output_vertex_indices"][:4], [None] * 4)
        self.assertEqual(report["original_to_output_vertex_indices"][4:], list(range(8)))
        self.assert_orientation(source, clean, report)

    def test_near_body_button_and_larger_or_dense_accessory_survive(self):
        dense = plate()
        dense["faces"] *= 20
        for accessory in (cube(.006, (.504, 0., 0.)), cube(.02, (.65, 0., 0.)), dense):
            with self.subTest(accessory=accessory):
                source = combine(cube(), accessory)
                clean, report = cleanup.clean_surface_mesh(source)
                self.assertEqual(report["removed_face_indices"], [])
                self.assertEqual(clean, source)

    def test_connected_thin_feature_and_sharp_cube_vertices_are_preserved(self):
        source = cube()
        source["vertices"].append([.001, .001, 2.])
        a, b, c = source["faces"].pop(2)
        source["faces"].extend([[a, b, 8], [b, c, 8], [c, a, 8]])
        clean, report = cleanup.clean_surface_mesh(source)
        self.assertEqual(clean, source)
        self.assertEqual(report["removed_face_indices"], [])
        self.assertGreater(report["smoothing"]["protected_seed_reason_counts"]["crease"], 0)
        self.assertEqual(report["retained_topology_before"], report["retained_topology_after"])

    def test_main_degenerate_and_nonmanifold_faces_are_kept_and_frozen(self):
        source = cube()
        source["vertices"].append([0., -.5, -.5])
        source["faces"].append([0, 1, 8])
        clean, report = cleanup.clean_surface_mesh(source)
        self.assertEqual(clean, source)
        self.assertEqual(report["summary"]["removed_faces"], 0)
        self.assertEqual(report["retained_topology_after"]["zero_area_faces"], 1)
        self.assertGreater(report["smoothing"]["protected_seed_reason_counts"]["degenerate"], 0)
        self.assertGreater(report["smoothing"]["protected_seed_reason_counts"]["nonmanifold"], 0)
        self.assertTrue(cleanup.validate_surface_cleanup(source, clean, report))

    def test_detached_degenerate_debris_may_drop_without_touching_main(self):
        debris = {"vertices": [[.65, 0., 0.], [.651, 0., 0.], [.652, 0., 0.]], "faces": [[0, 1, 2]]}
        source = combine(cube(), debris)
        clean, report = cleanup.clean_surface_mesh(source)
        self.assertEqual(clean, cube())
        self.assertEqual(report["removed_face_indices"], [12])
        self.assertEqual(report["summary"]["removed_open_or_degenerate_components"], 1)

    def test_multi_subject_scene_does_not_delete_secondary_components(self):
        source = combine(cube(), cube(.5, (2., 0., 0.)), plate())
        clean, report = cleanup.clean_surface_mesh(source)
        self.assertEqual(clean, source)
        self.assertEqual(report["skip_reason"], "main_not_dominant")

    def test_cumulative_removal_face_budget_keeps_all_candidates(self):
        debris = [plate(center=(.65 + index * .01, 0., 0.)) for index in range(17)]
        source = combine(cube(), *debris)
        clean, report = cleanup.clean_surface_mesh(source)
        self.assertEqual(clean, source)
        self.assertEqual(report["skip_reason"], "cleanup_budget_exceeded")
        self.assertEqual(report["removed_face_indices"], [])

    def test_analysis_budget_exhaustion_keeps_components_and_reports_incomplete(self):
        source = combine(cube(), plate())
        with patch.object(cleanup, "_gap_passes", side_effect=cleanup._AnalysisBudgetExceeded):
            clean, report = cleanup.clean_surface_mesh(source)
        self.assertEqual(clean, source)
        self.assertFalse(report["analysis_complete"])
        self.assertEqual(report["skip_reason"], "analysis_budget_exceeded")

    def test_unreferenced_vertices_are_never_implicitly_deleted(self):
        source = combine(cube(), plate())
        source["vertices"].append([4., 3., 2.])
        clean, report = cleanup.clean_surface_mesh(source)
        self.assertEqual(clean, source)
        self.assertEqual(report["skip_reason"], "unreferenced_vertices")
        self.assertEqual(report["original_to_output_vertex_indices"], list(range(len(source["vertices"]))))

    def test_scale_and_translation_preserve_normalized_algorithm_and_removal(self):
        source = combine(noisy_sphere(), plate(.003, (1.3, 0., 0.)))
        clean, report = cleanup.clean_surface_mesh(source)
        for scale, offset in ((.001, (0., 0., 0.)), (1000., (123., -54., 7.))):
            transformed = {"vertices": [[vertex[k] * scale + offset[k] for k in range(3)] for vertex in source["vertices"]],
                           "faces": copy.deepcopy(source["faces"])}
            actual, actual_report = cleanup.clean_surface_mesh(transformed)
            self.assertEqual(actual_report["removed_face_indices"], report["removed_face_indices"])
            self.assertEqual(actual["faces"], clean["faces"])
            for expected, vertex in zip(clean["vertices"], actual["vertices"]):
                for axis in range(3):
                    self.assertAlmostEqual((vertex[axis] - offset[axis]) / scale, expected[axis], places=10)
            self.assertAlmostEqual(actual_report["smoothing"]["max_displacement"] / scale,
                                   report["smoothing"]["max_displacement"], places=10)

    def test_triangle_orientation_and_area_guard_detects_flip_and_collapse(self):
        source = {"vertices": [[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]], "faces": [[0, 1, 2]]}
        original = [cleanup._cross(cleanup._sub(source["vertices"][1], source["vertices"][0]),
                                   cleanup._sub(source["vertices"][2], source["vertices"][0]))]
        for vertices in ([[0., 0., 0.], [0., 1., 0.], [1., 0., 0.]],
                         [[0., 0., 0.], [1., 0., 0.], [.5, .01, 0.]]):
            self.assertEqual(cleanup._unsafe_faces(vertices, source["faces"], original), source["faces"])

    def test_surface_operation_budget_keeps_raw_without_building_graph_or_components(self):
        source = noisy_sphere(1)
        limited = {**dict(cleanup.POLICY), "max_surface_vertices": 10}
        with patch.object(cleanup, "POLICY", limited), \
                patch.object(cleanup, "_remove_components") as components, \
                patch.object(cleanup, "_smoothing_graph") as graph:
            clean, report = cleanup.clean_surface_mesh(source)
            self.assertTrue(cleanup.validate_surface_cleanup(source, clean, report))
        components.assert_not_called()
        graph.assert_not_called()
        self.assertEqual(clean, source)
        self.assertFalse(report["applied"])
        self.assertFalse(report["analysis_complete"])
        self.assertEqual(report["skip_reason"], "surface_operation_budget_exceeded")
        self.assertIsNone(report["original_to_output_vertex_indices"])
        self.assertEqual(report["vertex_mapping_encoding"], "identity")
        self.assertEqual(report["smoothing"]["max_displacement"], 0.)

    def test_per_vertex_displacement_respects_local_edge_budget(self):
        source = noisy_sphere()
        clean, report = cleanup.clean_surface_mesh(source)
        adjacency, _, _, _ = cleanup._smoothing_graph(source["vertices"], source["faces"])
        for index in report["moved_original_vertex_indices"]:
            distance = math.sqrt(cleanup._length2(cleanup._sub(clean["vertices"][index], source["vertices"][index])))
            local_length = math.fsum(math.sqrt(cleanup._length2(cleanup._sub(source["vertices"][index], source["vertices"][neighbor])))
                                    for neighbor in adjacency[index]) / len(adjacency[index])
            self.assertLessEqual(distance, .2 * local_length + 1e-12)

    def test_repeated_cleanup_is_byte_deterministic_and_policy_is_immutable(self):
        source = noisy_sphere(1)
        first, first_report = cleanup.clean_surface_mesh(source)
        second, second_report = cleanup.clean_surface_mesh(source)
        self.assertEqual(cleanup._canonical(first), cleanup._canonical(second))
        self.assertEqual(cleanup._canonical(first_report), cleanup._canonical(second_report))
        with self.assertRaises(TypeError):
            cleanup.POLICY["smoothing_pairs"] = 999

    def test_forged_hashes_mapping_displacement_and_policy_do_not_authorize_geometry(self):
        source = noisy_sphere(1)
        clean, report = cleanup.clean_surface_mesh(source)
        for field, value in (("applied", 1), ("policy_id", "other"), ("extra", "unapproved")):
            modified = copy.deepcopy(report)
            modified[field] = value
            with self.subTest(field=field), self.assertRaises(cleanup.SurfaceCleanupError):
                cleanup.validate_surface_cleanup(source, clean, modified)
        for field in ("original_to_output_vertex_indices", "moved_original_vertex_indices"):
            modified = copy.deepcopy(report)
            modified[field] = []
            with self.subTest(field=field), self.assertRaises(cleanup.SurfaceCleanupError):
                cleanup.validate_surface_cleanup(source, clean, modified)
        forged = copy.deepcopy(clean)
        forged["vertices"][0][0] += .00001
        modified = copy.deepcopy(report)
        modified["output_geometry_sha256"] = cleanup._sha(forged)
        with self.assertRaises(cleanup.SurfaceCleanupError):
            cleanup.validate_surface_cleanup(source, forged, modified)
        modified = copy.deepcopy(report)
        modified["smoothing"]["max_displacement"] = 0.
        with self.assertRaises(cleanup.SurfaceCleanupError):
            cleanup.validate_surface_cleanup(source, clean, modified)

    def test_invalid_geometry_and_arbitrary_cleanup_modes_are_rejected(self):
        for source in ({"vertices": [[0., 0., 0.], [1., 0., 0.], [0., float("nan"), 0.]], "faces": [[0, 1, 2]]},
                       {"vertices": [[0., 0., 0.]] * 3, "faces": [[0, 0, 2]]},
                       {"vertices": [[0., 0., 0.]] * 3, "faces": [[0, 1, True]]}):
            with self.assertRaises(cleanup.SurfaceCleanupError):
                cleanup.clean_surface_mesh(source)
        for mode in ("conservative", "none", "largest", {"iterations": 1000}):
            with self.subTest(mode=mode), self.assertRaises(cleanup.SurfaceCleanupError):
                cleanup.clean_surface_mesh(cube(), mode)


if __name__ == "__main__":
    unittest.main()
