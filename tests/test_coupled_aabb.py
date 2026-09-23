"""Rigid/mesh broad-phase regression cases for changing contact geometry."""
from __future__ import annotations

import ctypes as C
import time
import unittest

from physics_demo import coupled_solver as coupled
from physics_demo.native_backend import CDiagnostics


PLAN = {"effective_spacing": .1, "render_particle_limit": 1, "threads": 1,
        "density_iterations": 1, "divergence_iterations": 1, "max_substeps": 32,
        "coupling_substeps": 1, "coupling_iterations": 1, "mesh_substeps": 1, "mesh_iterations": 1}


def surface(ident: str, vertices: list[list[float]], *, position=(0, 0, 0),
            velocity=(0, 0, 0), motion="static") -> dict:
    return {"id": ident, "type": "mesh", "motion": motion, "position": list(position),
            "velocity": list(velocity), "mass": 1, "thickness": .01, "friction": 0,
            "mesh": {"vertices": vertices, "triangles": [[0, 2, 1]]}}


def scene(center: tuple[float, float, float], surfaces: list[dict], *, duration=.001, dt=.001) -> dict:
    return {"world": {"duration": duration, "dt": dt, "output_fps": 10,
                      "gravity": [0, 0, 0],
                      "bounds": {"min": [-3, -3, -3], "max": [3, 3, 3]}},
            "entities": [{"id": "ball", "type": "rigid_body",
                          "shape": {"type": "sphere", "radius": .1},
                          "position": list(center), "velocity": [0, 0, 0],
                          "angular_velocity": [0, 0, 0], "orientation": [1, 0, 0, 0],
                          "mass": 1, "fixed": False, "friction": 0, "restitution": 0},
                         *surfaces],
            "coupling": {"substeps": 1, "iterations": 1, "friction": 0},
            "colliders": [], "connections": [], "force_fields": [], "queries": [],
            "interactions": {"mutual_gravity": False}}


class CoupledAabbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library, _, _ = coupled._load_library(time.monotonic() + 30)

    def solve(self, definition: dict):
        simulation, _ = coupled._pack(definition, PLAN, time.monotonic() + 30)
        diagnostic, particle_diagnostic, mesh_diagnostic = coupled.Diagnostics(), CDiagnostics(), coupled.mesh.Diagnostics()
        status = self.library.coupled_simulate(C.byref(simulation), C.byref(diagnostic),
                                               C.byref(particle_diagnostic), C.byref(mesh_diagnostic))
        self.assertEqual(status, 0)
        self.assertTrue(diagnostic.completed)
        self.assertTrue(diagnostic.finite)
        return simulation, diagnostic

    def test_sample_position_refreshes_between_two_triangles(self):
        vertices = [[-2, 0, -2], [2, 0, -2], [0, 0, 2]]
        lower = surface("lower", vertices, position=(0, .05, 0))
        upper = surface("upper", vertices, position=(0, .2, 0))
        simulation, diagnostic = self.solve(scene((0, .1, 0), [lower, upper]))
        self.assertEqual(diagnostic.contact_count, 2)
        self.assertAlmostEqual(simulation.rigid[0].body.position.y, .09, places=13)

    def test_contact_just_inside_expanded_triangle_boundary(self):
        vertical = surface("wall", [[0, -2, -2], [0, 2, -2], [0, 0, 2]])
        simulation, diagnostic = self.solve(scene((.11 - 1e-11, 0, 0), [vertical]))
        self.assertEqual(diagnostic.contact_count, 1)
        self.assertAlmostEqual(diagnostic.max_penetration_m, 1e-11, delta=1e-13)
        self.assertAlmostEqual(simulation.rigid[0].body.position.x, .11, places=13)

    def test_kinematic_triangle_uses_current_vertices(self):
        moving = surface("moving", [[-2, 0, -2], [2, 0, -2], [0, 0, 2]],
                         position=(0, -.02, 0), velocity=(0, .2, 0), motion="kinematic")
        simulation, diagnostic = self.solve(scene((0, .1, 0), [moving], duration=.1, dt=.1))
        self.assertEqual(diagnostic.contact_count, 1)
        self.assertAlmostEqual(simulation.rigid[0].body.position.y, .11, places=13)


if __name__ == "__main__":
    unittest.main()
