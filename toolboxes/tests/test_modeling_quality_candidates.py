"""Analytic mesh quality fixtures; no neural weights or native libraries."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "toolboxes"))
sys.path.insert(0, str(ROOT))
from modeling_flow import candidate_quality as quality
from modeling_flow.contracts import digest


CUBE = {"vertices": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                     [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]],
        "faces": [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
                  [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                  [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]}
QUADS = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4],
         [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]]


def centered_faces_cube(dent=0.0):
    vertices, faces = copy.deepcopy(CUBE["vertices"]), []
    for index, quad in enumerate(QUADS):
        center = [sum(vertices[v][k] for v in quad) / 4 for k in range(3)]
        if index == 1:
            center[2] -= dent
        midpoint = len(vertices)
        vertices.append(center)
        faces.extend([[quad[i], quad[(i + 1) % 4], midpoint] for i in range(4)])
    return {"vertices": vertices, "faces": faces}


def subdivided_cube(divisions):
    """Exact shared lattice vertices; coplanar subdivision changes no surface."""
    vertices, faces, indices = [], [], {}
    for quad in QUADS:
        a, b, _, d = [CUBE["vertices"][v] for v in quad]
        grid = {}
        for i in range(divisions + 1):
            for j in range(divisions + 1):
                key = tuple(a[k] * divisions + (b[k] - a[k]) * i + (d[k] - a[k]) * j for k in range(3))
                if key not in indices:
                    indices[key] = len(vertices)
                    vertices.append([value / divisions for value in key])
                grid[i, j] = indices[key]
        for i in range(divisions):
            for j in range(divisions):
                x, y, z, w = grid[i, j], grid[i + 1, j], grid[i + 1, j + 1], grid[i, j + 1]
                faces.extend([[x, y, z], [x, z, w]])
    return {"vertices": vertices, "faces": faces}


def torus(segments=24, tube=12):
    vertices, faces = [], []
    for i in range(segments):
        u = 2 * math.pi * i / segments
        for j in range(tube):
            v = 2 * math.pi * j / tube
            radius = 0.3 + 0.1 * math.cos(v)
            vertices.append([radius * math.cos(u), radius * math.sin(u), 0.1 * math.sin(v)])
    for i in range(segments):
        for j in range(tube):
            a, b = i * tube + j, ((i + 1) % segments) * tube + j
            c, d = ((i + 1) % segments) * tube + (j + 1) % tube, i * tube + (j + 1) % tube
            faces.extend([[a, b, c], [a, c, d]])
    return {"vertices": vertices, "faces": faces}


def pinched_tetrahedra():
    return {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1],
                         [-1, 0, 0], [0, -1, 0], [0, 0, -1]],
            "faces": [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3],
                      [0, 4, 5], [0, 6, 4], [0, 5, 6], [4, 6, 5]]}


class CandidateQualityTests(unittest.TestCase):
    def setUp(self):
        # Engine pins are host state; these developer tests use their own repo.
        self.environment = patch.dict(os.environ)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        os.environ.pop("PHYSICS_PIPELINE_PHYSICS_ROOT", None)

    def test_identical_cube_is_deterministic_content_bound_and_preserved(self):
        source, candidate = copy.deepcopy(CUBE), copy.deepcopy(CUBE)
        before = copy.deepcopy((source, candidate))
        first = quality.evaluate_candidate(source, candidate, scale={"units": "m", "physical_extent_m": 1, "scale_axis": "max"})
        second = quality.evaluate_candidate(source, candidate, scale={"units": "m", "physical_extent_m": 1, "scale_axis": "max"})
        self.assertEqual(first, second)
        self.assertEqual((source, candidate), before)
        self.assertEqual(first["status"], "accepted", first)
        self.assertEqual(first["source_sha256"], digest(quality.validate_mesh(source)))
        self.assertEqual(first["candidate_sha256"], first["source_sha256"])
        self.assertEqual(first["engine_audit"]["status"], "passed")
        self.assertEqual(len(first["engine_audit"]["module_sha256"]), 64)
        self.assertTrue(first["scale_check"]["declared_extent_verified"])
        self.assertFalse(first["sampled_distance"]["certified_hausdorff"])
        self.assertLess(first["sampled_distance"]["max_source_extent_ratio"], 1e-14)
        self.assertEqual(first["thin_structure"], "not_verified")
        self.assertEqual(first["aperture"], "not_verified")
        self.assertFalse(first["scene_ready"])

    def test_liquid_purpose_is_only_closed_geometry_qualification(self):
        report = quality.evaluate_candidate(CUBE, CUBE, purpose="liquid_volume")
        self.assertTrue(report["accepted"], report)
        self.assertEqual(report["scale_check"]["units"], "not_verified")
        self.assertEqual(report["physical_accuracy"], "not_verified")
        self.assertNotIn("particles", report)

    def test_surface_numeric_volume_floor_uses_verified_metre_scale(self):
        tiny = copy.deepcopy(CUBE)
        tiny["vertices"] = [[value * 1e-6 for value in point] for point in tiny["vertices"]]
        scale = {"units": "m", "physical_extent_m": 1e-6, "scale_axis": "max"}
        report = quality.evaluate_candidate(tiny, tiny, scale=scale)
        self.assertEqual(report["code"], "candidate_numeric_scale_rejected")
        self.assertEqual(report["numeric_scale"]["status"], "rejected")
        coordinate_only = quality.evaluate_candidate(tiny, tiny)
        self.assertTrue(coordinate_only["accepted"], coordinate_only)
        self.assertEqual(coordinate_only["numeric_scale"]["status"], "not_verified")

    def test_liquid_geometry_pass_does_not_apply_surface_solver_floor(self):
        tiny = copy.deepcopy(CUBE)
        tiny["vertices"] = [[value * 1e-6 for value in point] for point in tiny["vertices"]]
        report = quality.evaluate_candidate(tiny, tiny, purpose="liquid_volume",
                                            scale={"units": "m", "physical_extent_m": 1e-6})
        self.assertTrue(report["accepted"], report)
        self.assertEqual(report["numeric_scale"]["status"], "not_applicable")

    def test_torus_handle_survives_and_filling_it_is_rejected(self):
        ring = torus()
        report = quality.evaluate_candidate(ring, ring)
        self.assertTrue(report["accepted"], report)
        self.assertEqual(report["candidate_audit"]["euler_characteristic"], 0)
        changed = quality.evaluate_candidate(ring, CUBE)
        self.assertEqual(changed["code"], "candidate_topology_changed")

    def test_open_source_and_candidate_are_rejected_without_repair(self):
        open_cube = {**copy.deepcopy(CUBE), "faces": copy.deepcopy(CUBE["faces"][:-1])}
        self.assertEqual(quality.evaluate_candidate(open_cube, CUBE)["code"], "source_topology_rejected")
        self.assertEqual(quality.evaluate_candidate(CUBE, open_cube)["code"], "candidate_topology_rejected")

    def test_pinched_vertex_reaches_engine_and_is_rejected(self):
        mesh = pinched_tetrahedra()
        report = quality.evaluate_candidate(mesh, mesh)
        self.assertEqual(report["status"], "rejected")
        self.assertEqual(report["code"], "candidate_nonmanifold_or_disconnected")
        self.assertEqual(report["engine_audit"]["status"], "rejected")

    def test_geometrically_crossed_cube_reaches_engine_and_is_rejected(self):
        mesh = copy.deepcopy(CUBE)
        mesh["vertices"][6] = [-0.5, 0.5, 0.5]
        report = quality.evaluate_candidate(mesh, mesh)
        self.assertEqual(report["candidate_audit"]["boundary_edges"], 0)
        self.assertGreater(report["candidate_audit"]["signed_volume"], 0)
        self.assertEqual(report["code"], "candidate_self_intersection")

    def test_inward_candidate_is_rejected_without_global_face_reversal(self):
        mesh = {**copy.deepcopy(CUBE), "faces": [[a, c, b] for a, b, c in CUBE["faces"]]}
        before = copy.deepcopy(mesh)
        self.assertEqual(quality.evaluate_candidate(CUBE, mesh)["code"], "candidate_nonpositive_volume")
        self.assertEqual(mesh, before)

    def test_volume_loss_with_unchanged_bounding_box_is_rejected(self):
        source, candidate = centered_faces_cube(), centered_faces_cube(dent=0.1)
        report = quality.evaluate_candidate(source, candidate)
        self.assertEqual(report["scale_check"]["bbox_extent_change_source_extent_ratio"], 0)
        self.assertGreater(report["volume_change"]["relative_absolute_change"], 0.02)
        self.assertEqual(report["code"], "candidate_volume_changed")

    def test_local_distortion_below_volume_limit_exceeds_sampled_distance(self):
        report = quality.evaluate_candidate(centered_faces_cube(), centered_faces_cube(dent=0.04))
        self.assertLess(report["volume_change"]["relative_absolute_change"], 0.02)
        self.assertEqual(report["code"], "candidate_sampled_distance_exceeded")
        self.assertEqual(report["engine_audit"]["status"], "passed")
        self.assertAlmostEqual(report["sampled_distance"]["max_source_extent_ratio"], 0.04)

    def test_scale_and_frame_are_checked_without_changing_coordinates(self):
        self.assertEqual(quality.evaluate_candidate(CUBE, CUBE, scale={"units": "m", "physical_extent_m": 2})["code"], "declared_scale_mismatch")
        self.assertEqual(quality.evaluate_candidate(CUBE, CUBE, scale={"units": "model_unit", "physical_extent_m": 1})["code"], "invalid_physical_scale")
        self.assertEqual(quality.evaluate_candidate(CUBE, CUBE, scale={"units": "m", "physical_extent_m": 10 ** 1000})["code"], "invalid_physical_scale")
        shifted = copy.deepcopy(CUBE)
        shifted["vertices"] = [[x + 0.05, y, z] for x, y, z in shifted["vertices"]]
        self.assertEqual(quality.evaluate_candidate(CUBE, shifted)["code"], "candidate_scale_changed")

    def test_high_resolution_coplanar_source_uses_bvh_and_remains_exact(self):
        source = subdivided_cube(24)  # 6,912 faces, same exact unit cube surface.
        report = quality.evaluate_candidate(source, CUBE)
        self.assertTrue(report["accepted"], report)
        self.assertLess(report["sampled_distance"]["max_source_extent_ratio"], 1e-14)
        checks = report["sampled_distance"]["work"]["point_triangle_checks"]
        self.assertLess(checks, 32 * len(source["faces"]) // 10)

    def test_aabb_nearest_distance_matches_analytic_cube_distances(self):
        budget = quality._Budget(dict(quality.POLICY))
        index = quality._SurfaceIndex(CUBE, budget, [[0, 0, 0], [1, 1, 1]])
        for point, expected in (([2, 0.5, 0.5], 1), ([-1, -1, -1], 3), ([0.5, 0.5, 0.5], 0.25), ([1, 0.3, 0.2], 0)):
            self.assertAlmostEqual(index.nearest2(point), expected)

    def test_distance_budget_exhaustion_is_unknown_and_never_acceptance(self):
        limited = dict(quality.POLICY, max_point_triangle_checks=1)
        with patch.object(quality, "POLICY", limited):
            report = quality.evaluate_candidate(CUBE, CUBE)
        self.assertEqual(report["status"], "budget_exhausted")
        self.assertFalse(report["accepted"])
        self.assertEqual(report["code"], "distance_audit_budget_exhausted")
        self.assertEqual(report["sampled_distance"]["status"], "not_verified")
        self.assertEqual(report["sampled_distance"]["work"]["point_triangle_checks"], 1)

    def test_actual_engine_work_budget_exhaustion_is_not_self_intersection(self):
        from physics_demo.core import meshes as engine
        shifted = copy.deepcopy(CUBE)
        shifted["vertices"] = [[x + 0.0017, y, z] for x, y, z in shifted["vertices"]]
        with patch.object(engine, "MAX_PAIR_CHECKS", 0):
            report = quality.evaluate_candidate(shifted, shifted)
        self.assertEqual(report["status"], "budget_exhausted")
        self.assertEqual(report["code"], "engine_audit_budget_exhausted")
        self.assertEqual(report["engine_audit"]["status"], "not_verified")

    def test_input_capacity_failure_is_explicit_and_source_is_preserved(self):
        before = copy.deepcopy(CUBE)
        with patch.object(quality, "POLICY", dict(quality.POLICY, max_source_faces=11)):
            report = quality.evaluate_candidate(CUBE, CUBE)
        self.assertEqual(report["status"], "budget_exhausted")
        self.assertEqual(report["code"], "source_capacity_exceeded")
        self.assertEqual(CUBE, before)

    def test_missing_engine_and_unknown_purpose_are_unsupported(self):
        with patch.object(quality.importlib, "import_module", side_effect=ImportError("no physics package")):
            report = quality.evaluate_candidate(CUBE, CUBE)
        self.assertEqual(report["status"], "unsupported")
        self.assertEqual(report["code"], "engine_auditor_unavailable")
        self.assertEqual(quality.evaluate_candidate(CUBE, CUBE, "tetrahedral_fem")["status"], "unsupported")

    def test_invalid_geometry_is_rejected_with_actionable_code(self):
        invalid = copy.deepcopy(CUBE)
        invalid["vertices"][0][0] = math.nan
        self.assertEqual(quality.evaluate_candidate(CUBE, invalid)["code"], "invalid_candidate_mesh")

    def test_host_pin_bootstrap_works_in_clean_isolated_subprocess(self):
        with tempfile.TemporaryDirectory(prefix="candidate-pinned-engine-") as directory:
            root = Path(directory)
            package = root / "physics_release" / "physics_demo"
            (package / "core").mkdir(parents=True)
            (package / "__init__.py").write_text("")
            (package / "core" / "__init__.py").write_text("")
            for name in ("meshes.py", "math3d.py"):
                shutil.copyfile(ROOT / "physics_demo" / "core" / name, package / "core" / name)
            code = ("import sys,json;sys.dont_write_bytecode=True;sys.path.insert(0," + repr(str(ROOT / "toolboxes"))
                    + ");from modeling_flow.candidate_quality import evaluate_candidate;mesh=" + repr(CUBE)
                    + ";print(json.dumps(evaluate_candidate(mesh,mesh)))")
            environment = dict(os.environ, PHYSICS_PIPELINE_PHYSICS_ROOT=str(root), PYTHONDONTWRITEBYTECODE="1")
            completed = subprocess.run([sys.executable, "-I", "-c", code], cwd=directory, env=environment,
                                       capture_output=True, text=True, timeout=15, check=True)
            report = json.loads(completed.stdout)
            self.assertEqual(report["status"], "accepted", report)
            self.assertEqual(report["engine_audit"]["module_sha256"], quality.file_digest(package / "core" / "meshes.py"))

    def test_loaded_engine_cannot_switch_to_host_pin(self):
        from physics_demo.core import meshes  # Load this test repository's engine.
        with tempfile.TemporaryDirectory(prefix="candidate-other-engine-") as directory:
            path = Path(directory) / "physics_release" / "physics_demo" / "core"
            path.mkdir(parents=True)
            (path / "meshes.py").write_text("raise RuntimeError('must not import this engine')\n")
            with patch.dict(os.environ, {"PHYSICS_PIPELINE_PHYSICS_ROOT": directory}):
                report = quality.evaluate_candidate(CUBE, CUBE)
        self.assertEqual(report["status"], "unsupported")
        self.assertIn("different physics package", report["errors"][0]["message"])


if __name__ == "__main__":
    unittest.main()
