from __future__ import annotations

import json
import math
import tempfile
import time
import unittest
from pathlib import Path
import subprocess
from unittest.mock import patch

from physics_demo.colliders import project_colliders
from physics_demo.engine import run_scene
from physics_demo.native_backend import NativeBackendUnavailable, _compile_library
from physics_demo.runner import estimate, simulate, validate


def _timed_force_scene(entity_type: str, backend: str) -> dict:
    if entity_type == "point_mass":
        entity = {
            "id": "subject",
            "type": "point_mass",
            "mass": 1.0,
            "position": [0, 0, 0],
            "velocity": [0, 0, 0],
            "fixed": False,
        }
    else:
        entity = {
            "id": "subject",
            "type": "granular",
            "shape": {"type": "box", "center": [0, 0, 0], "size": [0.1, 0.1, 0.1]},
            "spacing": 0.2,
            "velocity": [0, 0, 0],
            "properties": {"friction": 0, "cohesion": 0},
        }
    return {
        "version": 1,
        "name": f"timed-force-{entity_type}-{backend}",
        "world": {
            "gravity": [0, 0, 0],
            "duration": 0.05,
            "dt": 0.05,
            "output_fps": 20,
            "bounds": {"min": [-1, -1, -1], "max": [1, 1, 1]},
        },
        "budget": {"wall_time_s": 60, "quality": "preview", "backend": backend},
        "entities": [entity],
        "colliders": [],
        "force_fields": [{
            "id": "short-pulse",
            "type": "uniform",
            "targets": ["subject"],
            "start_time": 0.01,
            "end_time": 0.03,
            "acceleration": [10, 0, 0],
        }],
        "interactions": {"mutual_gravity": False},
    }


class PhysicsRegressionTests(unittest.TestCase):
    def test_native_compiler_probe_failures_are_structured(self):
        with patch(
            "physics_demo.native_backend.subprocess.run",
            side_effect=subprocess.TimeoutExpired(["cc", "--version"], 0.1),
        ):
            with self.assertRaises(NativeBackendUnavailable):
                _compile_library(0.1)
        empty = subprocess.CompletedProcess(["cc", "--version"], 0, stdout="", stderr="")
        with patch("physics_demo.native_backend.subprocess.run", return_value=empty):
            with self.assertRaises(NativeBackendUnavailable):
                _compile_library(1.0)
        unique_version = subprocess.CompletedProcess(
            ["cc", "--version"], 0, stdout="physics-demo-timeout-probe-unique\n", stderr=""
        )
        with patch(
            "physics_demo.native_backend.subprocess.run",
            side_effect=[
                unique_version,
                subprocess.TimeoutExpired(["cc", "physics_native.c"], 0.1),
            ],
        ):
            with self.assertRaises(NativeBackendUnavailable):
                _compile_library(0.1)

        missing_output_version = subprocess.CompletedProcess(
            ["cc", "--version"], 0, stdout="physics-demo-missing-output-unique\n", stderr=""
        )
        false_success = subprocess.CompletedProcess(["cc", "physics_native.c"], 0, stdout="", stderr="")
        with patch(
            "physics_demo.native_backend.subprocess.run",
            side_effect=[missing_output_version, false_success],
        ):
            with self.assertRaisesRegex(NativeBackendUnavailable, "produced no loadable library"):
                _compile_library(1.0)

    def test_translated_box_inside_projection_matches_reference_and_native(self):
        spacing = 0.2
        radius = 0.46 * spacing
        center = [5.0, 3.0, -2.0]
        half_x = 0.5 + radius
        previous = [center[0] - half_x + 1.0e-6, center[1], center[2]]
        expected_x = center[0] - half_x

        reference = [7.0, center[1], center[2]]
        correction = project_colliders(
            reference,
            radius,
            [{"type": "box", "center": center, "size": [1, 2, 2], "friction": 0}],
            previous,
        )
        self.assertGreater(correction, 2.0)
        self.assertAlmostEqual(reference[0], expected_x, places=9)

        scene = {
            "version": 1,
            "name": "translated-box-inside-projection",
            "world": {
                "gravity": [0, 0, 0],
                "duration": 0.01,
                "dt": 0.01,
                "output_fps": 60,
                "bounds": {"min": [0, 0, -5], "max": [10, 6, 1]},
            },
            "budget": {"wall_time_s": 10, "quality": "preview", "backend": "native"},
            "entities": [{
                "id": "particle",
                "type": "granular",
                "shape": {"type": "box", "center": previous, "size": [0.1, 0.1, 0.1]},
                "spacing": spacing,
                "velocity": [200, 0, 0],
                "properties": {"friction": 0, "cohesion": 0},
            }],
            "colliders": [{
                "id": "translated-box",
                "type": "box",
                "center": center,
                "size": [1, 2, 2],
                "friction": 0,
            }],
            "interactions": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            result = simulate(scene, directory, 10, make_video=False)
            full = json.loads((Path(directory) / "result.json").read_text(encoding="utf-8"))
        self.assertTrue(result["ok"], result)
        self.assertAlmostEqual(full["trajectory"]["frames"][-1]["p"][0][0], expected_x, places=5)

    def test_timed_force_window_integrates_only_actual_overlap(self):
        expected_positions = {"point_mass": 0.006, "granular": 0.008}
        for backend in ("python", "native"):
            for entity_type, expected_x in expected_positions.items():
                with self.subTest(backend=backend, entity_type=entity_type), tempfile.TemporaryDirectory() as directory:
                    result = simulate(
                        _timed_force_scene(entity_type, backend),
                        directory,
                        60,
                        make_video=False,
                    )
                    full = json.loads((Path(directory) / "result.json").read_text(encoding="utf-8"))
                    self.assertTrue(result["ok"], result)
                    final_frame = full["trajectory"]["frames"][-1]
                    final_x = final_frame["g"][0][0] if entity_type == "point_mass" else final_frame["p"][0][0]
                    self.assertAlmostEqual(final_x, expected_x, places=5)
                    self.assertEqual(result["diagnostics"]["steps"], 1)
                    self.assertEqual(result["diagnostics"]["substeps"], 3)
                    if backend == "native" and entity_type == "granular":
                        self.assertAlmostEqual(
                            result["diagnostics"]["maximum_particle_speed_m_s"], 0.2, places=8
                        )

    def test_python_dense_neighbor_work_honors_deadline(self):
        raw = {
            "version": 1,
            "name": "python-deadline-dense-neighbors",
            "world": {
                "gravity": [0, -9.81, 0],
                "duration": 1.0,
                "dt": 0.05,
                "output_fps": 20,
                "bounds": {"min": [-2, -2, -2], "max": [2, 2, 2]},
            },
            "budget": {"wall_time_s": 10, "quality": "preview", "backend": "python"},
            "entities": [{
                "id": "dense-water",
                "type": "fluid",
                "shape": {"type": "box", "center": [0, 0, 0], "size": [0.55, 0.55, 0.55]},
                "spacing": 0.05,
                "velocity": [0, 0, 0],
                "properties": {"viscosity": 0.03, "surface_tension": 0.05},
            }],
            "colliders": [],
            "interactions": {},
        }
        validation = validate(raw)
        self.assertTrue(validation["ok"], validation)
        planned = estimate(raw, 10)
        self.assertTrue(planned["ok"], planned)
        plan = planned["plan"]
        plan["effective_spacing"] = 0.05
        plan["solver_iterations"] = 6
        plan["render_particle_limit"] = 32

        started = time.monotonic()
        result = run_scene(validation["scene"], plan, started + 0.02)
        elapsed = time.monotonic() - started
        self.assertGreaterEqual(result["diagnostics"]["particle_count"], 1000)
        self.assertFalse(result["diagnostics"]["completed"])
        self.assertLess(elapsed, 0.25)

    def test_python_closed_system_conserved_is_a_threshold_result(self):
        scene = _timed_force_scene("point_mass", "python")
        scene["force_fields"] = []
        validation = validate(scene)
        plan = estimate(scene, 10)["plan"]
        result = run_scene(validation["scene"], plan, time.monotonic() + 5)
        diagnostics = result["diagnostics"]
        self.assertTrue(diagnostics["nbody_invariants_applicable"])
        self.assertTrue(diagnostics["nbody_invariants_conserved"])
        self.assertIsNotNone(diagnostics["nbody_momentum_tolerance"])


if __name__ == "__main__":
    unittest.main()
