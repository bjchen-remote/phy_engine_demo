"""Precision and budget contracts for cached mesh intersection geometry."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from physics_demo.core import meshes


class MeshAuditCacheTests(unittest.TestCase):
    def test_full_audit_accepts_adjacent_sheet_and_rejects_folded_overlap(self):
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, -1, 0]]
        triangles = [[0, 1, 2], [1, 0, 3]]
        metadata = meshes.audit_mesh(vertices, triangles)
        self.assertFalse(metadata["closed"])
        self.assertTrue(metadata["self_intersection_checked"])
        vertices[3] = [0.25, 0.25, 0]
        with self.assertRaisesRegex(ValueError, "self-intersect"):
            meshes.audit_mesh(vertices, triangles)

    def test_shared_coplanar_tangent_and_epsilon_contacts(self):
        left = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        cases = [
            ("shared edge only", [[1, -1, 0]], [1, 0, 3], False),
            ("shared edge overlap", [[0.25, 0.25, 0]], [1, 0, 3], True),
            ("shared vertex only", [[-1, 0, 0], [0, -1, 0]], [0, 3, 4], False),
            ("coplanar overlap", [[0.1, 0.1, 0], [0.8, 0.1, 0], [0.1, 0.8, 0]], [3, 4, 5], True),
            ("vertex tangent", [[0, 0, 0], [-1, 0, 0], [0, -1, 0]], [3, 4, 5], True),
            ("within epsilon", [[0.1, 0.1, 0.5e-9], [0.8, 0.1, 0.5e-9], [0.1, 0.8, 0.5e-9]],
             [3, 4, 5], True),
            ("outside epsilon", [[0.1, 0.1, 2e-9], [0.8, 0.1, 2e-9], [0.1, 0.8, 2e-9]],
             [3, 4, 5], False),
        ]
        for label, right, face, expected in cases:
            with self.subTest(label=label):
                vertices = left + right
                self.assertEqual(meshes._intersection_pair(vertices, [[0, 1, 2], face]) is not None,
                                 expected)

    def test_work_and_wall_time_budgets_remain_strict(self):
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, -1, 0]]
        triangles = [[0, 1, 2], [1, 0, 3]]
        with patch.object(meshes, "MAX_PAIR_CHECKS", 1):
            with self.assertRaisesRegex(ValueError, "work budget"):
                meshes._intersection_pair(vertices, triangles)
        with patch.object(meshes.time, "monotonic", side_effect=[0.0, 3.0]):
            with self.assertRaisesRegex(ValueError, "2-second time budget"):
                meshes._intersection_pair(vertices, triangles)


if __name__ == "__main__":
    unittest.main()
