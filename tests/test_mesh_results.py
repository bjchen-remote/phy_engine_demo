"""Inspect real persisted mesh runs; distrust edited frames, claims and query data."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from physics_demo.api import call_tool
from physics_demo.meshes import build_mesh
from physics_demo.queries import digest
from physics_demo.results import PHYSICS_CLAIMS, physics_claims
from physics_demo.runner import inspect, query, simulate


class MeshResultTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        asset = build_mesh({"type": "box", "size": [0.4, 0.4, 0.4], "subdivisions": 1})
        entities = [
            {"id": "soft", "type": "mesh", "mesh": copy.deepcopy(asset), "position": [-1, 2, 0], "pinned_vertices": [0]},
            {"id": "fixed", "type": "mesh", "mesh": copy.deepcopy(asset), "position": [0, 2, 0], "motion": "static"},
            {"id": "driven", "type": "mesh", "mesh": copy.deepcopy(asset), "position": [1, 2, 0], "motion": "kinematic", "velocity": [0.2, 0, 0]},
        ]
        scene = {"world": {"gravity": [0, -1, 0], "duration": 0.2, "dt": 0.01, "output_fps": 20},
                 "entities": entities, "queries": [
                     {"id": "height", "type": "series", "metric": {"type": "centroid", "entity": "soft", "axis": "y"}},
                     {"id": "volume", "type": "series", "metric": {"type": "volume_ratio", "entity": "soft"}},
                     {"id": "drive", "type": "series", "metric": {"type": "centroid", "entity": "driven", "axis": "x"}},
                 ]}
        result = simulate(scene, cls.root, make_video=False)
        if not result["ok"]:
            raise AssertionError(result)
        cls.original_files = {p.name: p.read_bytes() for p in cls.root.iterdir() if p.is_file()}
        cls.result = json.loads(cls.original_files["result.json"])
        cls.measurements = json.loads(cls.original_files["measurements.json"])

    def setUp(self):
        for name, content in self.original_files.items():
            (self.root / name).write_bytes(content)

    def write_result(self, value):
        (self.root / "result.json").write_text(json.dumps(value), encoding="utf-8")

    def rejected(self, value):
        self.write_result(value)
        response = inspect(self.root)
        self.assertFalse(response["ok"], response)
        self.assertTrue(response.get("errors"), response)
        public = call_tool("physics_inspect", {"result_path": str(self.root)})
        self.assertFalse(public["ok"], public)

    def test_saved_mesh_run_inspects_and_queries_full_macro_samples(self):
        checked = inspect(self.root)
        self.assertTrue(checked["ok"], checked)
        self.assertEqual(checked["runtime_measurement_boundary"], "through_summary_persistence")
        history = query(self.root, "drive")
        self.assertTrue(history["ok"], history)
        self.assertEqual(len(history["series"]["times_s"]), 21)
        self.assertEqual(len(self.result["trajectory"]["frames"]), 5)
        self.assertAlmostEqual(history["series"]["values"][-1], 1.04, places=12)

    def test_object_topology_offsets_identity_and_motion_are_bound(self):
        changes = {"id": "forged", "vertex_start": 1, "vertex_count": 7, "triangles": [],
                   "color": [1, 1, 1], "motion": "static", "closed": False}
        for key, value in changes.items():
            with self.subTest(field=key):
                result = copy.deepcopy(self.result)
                result["trajectory"]["mesh_objects"][0][key] = value
                self.rejected(result)

    def test_mesh_metadata_requires_exact_integer_and_boolean_types(self):
        for key, value in (("vertex_count", 8.0), ("vertex_start", False), ("closed", 1)):
            with self.subTest(field=key):
                result = copy.deepcopy(self.result)
                result["trajectory"]["mesh_objects"][0][key] = value
                self.rejected(result)
        result = copy.deepcopy(self.result)
        result["trajectory"]["mesh_objects"][0]["triangles"][0][0] = False
        self.rejected(result)

    def test_missing_or_malformed_vertex_frames_are_rejected(self):
        for value in (None, [], "vertices", [[0, 0, 0]], [[0, 0, float("nan")]] * 24):
            with self.subTest(value=repr(value)[:40]):
                result = copy.deepcopy(self.result)
                result["trajectory"]["frames"][-1]["m"] = value
                self.rejected(result)

    def test_initial_and_pinned_vertices_are_bound_to_rest_positions(self):
        for frame in (0, -1):
            with self.subTest(frame=frame):
                result = copy.deepcopy(self.result)
                result["trajectory"]["frames"][frame]["m"][0][0] += 0.1
                self.rejected(result)

    def test_static_and_kinematic_vertices_follow_prescribed_motion(self):
        for index in (8, 16):
            with self.subTest(vertex=index):
                result = copy.deepcopy(self.result)
                result["trajectory"]["frames"][-1]["m"][index][0] += 0.1
                self.rejected(result)

    def test_free_vertices_cannot_hide_gross_strain_behind_unchanged_diagnostics(self):
        for value in (1000, -0.6):
            with self.subTest(x=value):
                result = copy.deepcopy(self.result)
                result["trajectory"]["frames"][-1]["m"][1][0] = value
                self.rejected(result)

    def test_sampled_closed_volume_cannot_disagree_with_claimed_diagnostics(self):
        result = copy.deepcopy(self.result)
        points = result["trajectory"]["frames"][-1]["m"]
        origin = list(points[0])
        # Scaling around the pin preserves its prescribed position and keeps all
        # points inside the world, while increasing volume by 33 percent.
        for index in range(1, 8):
            points[index] = [origin[k] + 1.1 * (points[index][k] - origin[k]) for k in range(3)]
        self.rejected(result)

    def test_frame_timestamp_schedule_and_final_endpoint_are_bound(self):
        for index, time in ((0, 0.001), (1, 0.051), (-1, 0.19), (-1, 1)):
            with self.subTest(frame=index, time=time):
                result = copy.deepcopy(self.result)
                result["trajectory"]["frames"][index]["t"] = time
                self.rejected(result)

    def test_mesh_diagnostic_counts_reject_wrong_values_and_container_types(self):
        for key in ("mesh_vertex_count", "mesh_triangle_count", "contact_count", "inverted_objects", "closed_objects"):
            for value in (True, -1, 1.0, [], {}, None):
                with self.subTest(field=key, value=value):
                    result = copy.deepcopy(self.result)
                    result["trajectory"]["diagnostics"][key] = value
                    self.rejected(result)
        for key in ("mesh_vertex_count", "mesh_triangle_count", "closed_objects"):
            result = copy.deepcopy(self.result)
            result["trajectory"]["diagnostics"][key] += 1
            self.rejected(result)

    def test_mesh_diagnostic_scalars_and_quality_ranges_fail_as_structured_data(self):
        for key in ("max_edge_strain", "min_volume_ratio", "max_volume_ratio", "residual_penetration_m",
                    "max_contact_correction_m", "maximum_speed_m_s"):
            for value in (True, -1, [], {}, None, float("nan"), 10 ** 500):
                with self.subTest(field=key, value=repr(value)[:30]):
                    result = copy.deepcopy(self.result)
                    result["trajectory"]["diagnostics"][key] = value
                    self.rejected(result)
        for key, value in (("max_edge_strain", 2), ("min_volume_ratio", 0.5), ("max_volume_ratio", 1.5),
                           ("residual_penetration_m", 1), ("inverted_objects", 1)):
            result = copy.deepcopy(self.result)
            result["trajectory"]["diagnostics"][key] = value
            self.rejected(result)

    def test_fidelity_claims_cannot_be_upgraded_and_old_claims_stay_unchanged(self):
        legacy_claims = physics_claims({"entities": [{"type": "point_mass"}]})
        self.assertEqual(legacy_claims, PHYSICS_CLAIMS)
        self.assertNotIn("mesh", legacy_claims)
        result = copy.deepcopy(self.result)
        result["physics_claims"]["mesh"] = "Calibrated volumetric FEM with guaranteed collision detection."
        self.rejected(result)
        result = copy.deepcopy(self.result)
        result["physics_claims"].pop("mesh")
        self.rejected(result)

    def test_measurement_digest_binds_mesh_macro_samples(self):
        result = copy.deepcopy(self.result)
        result["trajectory"]["observations"]["columns"]["height"][5] += 0.5
        self.write_result(result)
        self.assertFalse(query(self.root, "height")["ok"])
        self.assertFalse(inspect(self.root)["ok"])

    def test_forged_measurements_fail_even_when_claimed_digest_is_recomputed(self):
        measurements = copy.deepcopy(self.measurements)
        measurements["observations"]["columns"]["drive"][-1] = 999
        result = copy.deepcopy(self.result)
        result["measurements_sha256"] = digest(measurements)
        (self.root / "measurements.json").write_text(json.dumps(measurements), encoding="utf-8")
        self.write_result(result)
        self.assertFalse(query(self.root, "drive")["ok"])
        self.assertFalse(inspect(self.root)["ok"])


if __name__ == "__main__":
    unittest.main()
