"""Render decimation preserves small scene groups without decimating physics."""

from __future__ import annotations

import copy
import time
import unittest

from physics_demo.core import coupled_solver
from physics_demo.core.engine import run_scene as run_scene_python
from physics_demo.core.native_backend import run_scene_native


def _fluid(ident: str, preset: str, center: list[float], size: list[float]) -> dict:
    return {
        "id": ident,
        "type": "fluid",
        "preset": preset,
        "shape": {"type": "box", "center": center, "size": size},
        "velocity": [0, 0, 0],
        "properties": {"viscosity": 0.03, "surface_tension": 0.05},
    }


def _fixture() -> tuple[dict, dict]:
    # At 0.1 m spacing these entities contain 5, 1, and 10 particles.  The
    # previous uniform three-index sample omitted the one-particle honey group.
    scene = {
        "world": {
            "duration": 0.001,
            "dt": 0.001,
            "output_fps": 100,
            "gravity": [0, 0, 0],
            "bounds": {"min": [-5, -5, -5], "max": [5, 5, 5]},
        },
        "entities": [
            _fluid("water-main", "water", [-2, 0, 0], [0.5, 0.1, 0.1]),
            _fluid("honey-tiny", "honey", [0, 0, 0], [0.05, 0.05, 0.05]),
            _fluid("glue-main", "glue", [2, 0, 0], [1.0, 0.1, 0.1]),
        ],
        "colliders": [],
        "force_fields": [],
        "queries": [],
        "connections": [],
        "interactions": {
            "mutual_gravity": False,
            "gravity_G": 1.0,
            "softening": 0.01,
            "water_sand_drag": 0.0,
            "wetting_rate": 0.0,
        },
        "coupling": {"substeps": 1, "iterations": 1, "friction": 0.0},
    }
    plan = {
        "effective_spacing": 0.1,
        "render_particle_limit": 3,
        "output_frames": 2,
        "solver_iterations": 1,
        "density_iterations": 1,
        "divergence_iterations": 1,
        "max_substeps": 2,
        "threads": 1,
        "requested_backend": "native",
        "coupling_substeps": 1,
        "coupling_iterations": 1,
        "mesh_substeps": 1,
        "mesh_iterations": 1,
    }
    return scene, plan


class RenderSelectionTests(unittest.TestCase):
    def assert_representative_output(self, result: dict) -> None:
        self.assertEqual(result["diagnostics"]["particle_count"], 16)
        self.assertEqual(result["diagnostics"]["render_particle_count"], 3)
        self.assertEqual(result["particle_groups"], ["water-main", "honey-tiny", "glue-main"])
        self.assertEqual(result["particle_materials"], ["water", "honey", "glue"])
        self.assertTrue(all(len(frame["p"]) == 3 for frame in result["frames"]))

    def test_python_and_native_real_calls_preserve_small_group(self):
        scene, plan = _fixture()
        original = copy.deepcopy(scene)
        for solver in (run_scene_python, run_scene_native):
            with self.subTest(solver=solver.__module__):
                result = solver(scene, plan, time.monotonic() + 20)
                self.assert_representative_output(result)
                self.assertEqual(scene, original)

    def test_coupled_pack_preserves_small_group_without_decimating_state(self):
        scene, plan = _fixture()
        original = copy.deepcopy(scene)
        simulation, metadata = coupled_solver._pack(scene, plan, time.monotonic() + 20)
        particles = simulation.particles.contents
        self.assertEqual(particles.particle_count, 16)
        self.assertEqual(particles.render_count, 3)
        self.assertEqual(metadata["particle_groups"], ["water-main", "honey-tiny", "glue-main"])
        self.assertEqual(metadata["particle_materials"], ["water", "honey", "glue"])
        self.assertEqual(scene, original)


if __name__ == "__main__":
    unittest.main()
