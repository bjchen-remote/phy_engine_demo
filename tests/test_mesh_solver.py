"""Analytic oracles, contact failure cases, and timestep refinement for mesh XPBD."""
from __future__ import annotations

import ctypes
import math
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from physics_demo.mesh_solver import Diagnostics, Simulation, _load_library, _topology, run_scene_mesh
from physics_demo.native_backend import NativeResourceLimitError, _compiler


def body(identifier="soft", **changes):
    mesh = {"vertices": [[0, .4, 0], [0, -.4, 0], [.4, 0, 0], [0, 0, .4], [-.4, 0, 0], [0, 0, -.4]],
            "triangles": [[0, 3, 2], [0, 4, 3], [0, 5, 4], [0, 2, 5],
                          [1, 2, 3], [1, 3, 4], [1, 4, 5], [1, 5, 2]]}
    return {"id": identifier, "type": "mesh", "mesh": mesh, "position": [0, 2, 0],
            "velocity": [0, 0, 0], "motion": "soft", "mass": 1, "damping": 0,
            "friction": 0, "thickness": .01, **changes}


def scene(entities=None, duration=.4, dt=.01, gravity=(0, -10, 0), fps=20, colliders=None):
    entities = entities or [body()]
    return {"entities": entities, "world": {"duration": duration, "dt": dt, "gravity": list(gravity),
        "output_fps": fps, "bounds": {"min": [-20, -20, -20], "max": [20, 20, 20]}},
        "colliders": colliders or [], "queries": [
            {"id": "height", "type": "series", "metric": {"type": "centroid", "entity": entities[0]["id"], "axis": "y"}},
            {"id": "speed", "type": "series", "metric": {"type": "speed", "entity": entities[0]["id"]}},
            {"id": "volume", "type": "series", "metric": {"type": "volume_ratio", "entity": entities[0]["id"]}},
            {"id": "displacement", "type": "series", "metric": {"type": "max_displacement", "entity": entities[0]["id"]}},
            {"id": "strain", "type": "series", "metric": {"type": "max_edge_strain", "entity": entities[0]["id"]}}]}


def run(definition, **settings):
    return run_scene_mesh(definition, {"mesh_substeps": 4, "mesh_iterations": 8, **settings}, time.monotonic()+20)


class MeshSolverTests(unittest.TestCase):
    def test_public_prepare_and_native_solver_agree_on_iteration_limit(self):
        from physics_demo.runner import prepare

        definition = scene(duration=.01)
        definition["mesh_settings"] = {"iterations": 32, "substeps": 1}
        prepared = prepare(definition)
        self.assertTrue(prepared.get("ready_to_simulate"), prepared.get("errors"))
        self.assertEqual(prepared["plan"]["mesh_iterations"], 32)
        result = run_scene_mesh(prepared["scene"], prepared["plan"], time.monotonic()+10)
        self.assertTrue(result["diagnostics"]["completed"])
        self.assertEqual(result["diagnostics"]["native_status"], "ok")

    def test_freefall_velocity_and_first_order_refinement(self):
        coarse = run(scene(dt=.02))
        fine = run(scene(dt=.01))
        exact_height = 2-.5*10*.4**2
        coarse_error = abs(coarse["observations"]["columns"]["height"][-1]-exact_height)
        fine_error = abs(fine["observations"]["columns"]["height"][-1]-exact_height)
        self.assertAlmostEqual(coarse_error/fine_error, 2, places=8)
        self.assertLess(fine_error, .006)
        self.assertAlmostEqual(fine["observations"]["columns"]["speed"][-1], 4, places=9)
        self.assertLess(fine["diagnostics"]["max_edge_strain"], 1e-10)
        self.assertTrue(fine["diagnostics"]["completed"])

    def test_full_state_queries_do_not_depend_on_video_fps(self):
        low = run(scene(fps=5))
        high = run(scene(fps=60))
        self.assertEqual(low["observations"], high["observations"])
        self.assertNotEqual(len(low["frames"]), len(high["frames"]))
        self.assertEqual(len(low["observations"]["times"]), 41)
        self.assertAlmostEqual(low["observations"]["columns"]["volume"][-1], 1, places=10)

    def test_pinned_vertex_stays_fixed_and_surface_deforms(self):
        result = run(scene([body(pinned_vertices=[0])], duration=.3))
        self.assertEqual(result["frames"][0]["m"][0], result["frames"][-1]["m"][0])
        self.assertNotEqual(result["frames"][0]["m"][1], result["frames"][-1]["m"][1])
        self.assertGreater(result["observations"]["columns"]["strain"][-1], 1e-7)
        self.assertLess(result["diagnostics"]["max_edge_strain"], .1)

    def test_edge_compliance_changes_extension(self):
        stiff = run(scene([body(pinned_vertices=[0], edge_compliance=1e-8)], duration=.3))
        soft = run(scene([body(pinned_vertices=[0], edge_compliance=.01, bending_compliance=.1,
                                    volume_compliance=.1)], duration=.3))
        self.assertGreater(soft["observations"]["columns"]["strain"][-1],
                           stiff["observations"]["columns"]["strain"][-1] * 10)

    def test_plane_impact_and_volume(self):
        result = run(scene(duration=1, colliders=[{"type": "plane", "normal": [0, 1, 0], "offset": 0, "friction": .3}]))
        self.assertGreater(result["diagnostics"]["contact_count"], 0)
        self.assertGreaterEqual(min(p[1] for p in result["frames"][-1]["m"]), .009999)
        self.assertLess(result["diagnostics"]["residual_penetration_m"], 1e-9)
        self.assertGreater(result["diagnostics"]["min_volume_ratio"], .9)
        self.assertLess(result["diagnostics"]["max_volume_ratio"], 1.1)

    def test_open_cloth_has_no_volume_constraint(self):
        cloth = body(mesh={"vertices": [[-.5, 0, -.5], [.5, 0, -.5], [.5, 0, .5], [-.5, 0, .5]],
                           "triangles": [[0, 2, 1], [0, 3, 2]]}, pinned_vertices=[0, 1])
        definition = scene([cloth]); definition["queries"] = []
        result = run(definition)
        self.assertEqual(result["diagnostics"]["closed_objects"], 0)
        self.assertFalse(result["mesh_objects"][0]["closed"])
        self.assertLess(result["frames"][-1]["m"][2][1], result["frames"][0]["m"][2][1])

    def test_swept_triangle_prevents_thin_sheet_tunneling(self):
        sheet = body("floor", motion="static", position=[0, 0, 0],
                     mesh={"vertices": [[-5, 0, -5], [5, 0, -5], [0, 0, 5]], "triangles": [[0, 2, 1]]})
        result = run(scene([body(position=[0, 2, 0], velocity=[0, -100, 0]), sheet],
                           duration=.04, dt=.04, gravity=[0, 0, 0]), mesh_substeps=1)
        self.assertGreater(result["diagnostics"]["contact_count"], 0)
        self.assertGreaterEqual(min(p[1] for p in result["frames"][-1]["m"][:6]), .01999)

    def test_kinematic_mesh_pushes_soft_body(self):
        plate = body("plate", motion="kinematic", position=[0, 0, 0], velocity=[0, 2, 0],
                     mesh={"vertices": [[-5, 0, -5], [5, 0, -5], [0, 0, 5]], "triangles": [[0, 2, 1]]})
        result = run(scene([body(position=[0, 1, 0]), plate], duration=.5, gravity=[0, 0, 0]))
        self.assertGreater(result["observations"]["columns"]["height"][-1], 1.3)
        self.assertGreater(result["diagnostics"]["contact_count"], 0)
        self.assertAlmostEqual(result["frames"][-1]["m"][-1][1], 1, places=6)

    def test_kinematic_tip_contacts_soft_triangle_interior(self):
        cloth = body("cloth", position=[0, 0, 0],
                     mesh={"vertices": [[-2, 0, -2], [2, 0, -2], [0, 0, 2]], "triangles": [[0, 2, 1]]})
        tip = body("tip", motion="kinematic", position=[0, 1, 0], velocity=[0, -4, 0],
                   mesh={"vertices": [[-.1, 0, -.1], [.1, 0, -.1], [0, 0, .1]], "triangles": [[0, 2, 1]]})
        definition = scene([cloth, tip], duration=.5, gravity=[0, 0, 0])
        definition["queries"] = []
        result = run(definition)
        # None of the cloth vertices enter the small tip's face. Contact must
        # move the soft triangle through the kinematic vertex's barycentrics.
        self.assertGreater(result["diagnostics"]["contact_count"], 0)
        self.assertLess(sum(p[1] for p in result["frames"][-1]["m"][:3])/3, -.5)

    def test_tiny_positive_frame_interval_has_initial_and_terminal_frames(self):
        result = run(scene(duration=.01, fps=1e-12))
        self.assertEqual([frame["t"] for frame in result["frames"]], [0, .01])

    def test_fast_contact_frames_use_substep_geometry(self):
        from physics_demo.meshes import build_mesh

        mesh = build_mesh({"type": "ellipsoid", "radii": [.45, .45, .45], "segments": 32, "rings": 16})
        soft = body(mesh=mesh, position=[0, 1.2, 0], edge_compliance=1e-4,
                    damping=.15, friction=.35, thickness=.015)
        definition = scene([soft], duration=.6, dt=1/90, fps=12, gravity=[0, -9.81, 0],
                           colliders=[{"type": "plane", "normal": [0, 1, 0], "offset": 0, "friction": .4}])
        result = run(definition, mesh_iterations=6)
        edges = [edge for edge in _topology(soft, 0)[0] if not edge.bending]
        frame_strain = max(abs(math.dist(frame["m"][edge.a], frame["m"][edge.b])/edge.rest-1)
                           for frame in result["frames"] for edge in edges)
        # Interpolating whole macro-step endpoints previously created 19%
        # display strain although no solver substep exceeded 14%.
        self.assertLessEqual(frame_strain, result["diagnostics"]["max_edge_strain"] + 1e-4)

    def test_analytic_colliders_stop_fast_surface_crossing(self):
        colliders = [
            {"type": "sphere", "center": [0, 0, 0], "radius": .5, "friction": 0},
            {"type": "box", "center": [0, 0, 0], "size": [3, .1, 3], "friction": 0},
            {"type": "capsule", "a": [-2, 0, 0], "b": [2, 0, 0], "radius": .5, "friction": 0},
        ]
        for collider in colliders:
            with self.subTest(collider=collider["type"]):
                result = run(scene([body(velocity=[0, -100, 0])], duration=.04, dt=.04,
                                   gravity=[0, 0, 0], colliders=[collider]), mesh_substeps=1)
                self.assertGreater(result["diagnostics"]["contact_count"], 0)
                self.assertGreater(min(p[1] for p in result["frames"][-1]["m"]), .05)
                self.assertLess(result["diagnostics"]["residual_penetration_m"], 1e-9)

    def test_decimal_frame_and_terminal_step_roundoff(self):
        result = run(scene(duration=.3, dt=.0083333333, fps=10))
        self.assertEqual(len(result["frames"]), 4)
        self.assertEqual(result["frames"][-1]["t"], .3)
        self.assertEqual(result["diagnostics"]["steps"], 36)
        self.assertEqual(result["diagnostics"]["substeps"], 144)
        self.assertAlmostEqual(result["observations"]["times"][-1], .3, places=7)

    def test_two_soft_bodies_share_contact_impulse(self):
        left = body("left", position=[-.7, 1, 0], velocity=[2, 0, 0])
        right = body("right", position=[.7, 1, 0], velocity=[-2, 0, 0])
        result = run(scene([left, right], duration=.35, gravity=[0, 0, 0]))
        final = result["frames"][-1]["m"]
        left_x = sum(p[0] for p in final[:6])/6
        right_x = sum(p[0] for p in final[6:])/6
        self.assertLess(left_x, -.2)
        self.assertGreater(right_x, .2)
        self.assertAlmostEqual(left_x+right_x, 0, delta=1e-6)
        self.assertGreater(result["diagnostics"]["contact_count"], 0)

    def test_unequal_mass_contact_preserves_center_of_mass_motion(self):
        left = body("left", position=[-.7, 1, 0], velocity=[2, 0, 0], mass=2)
        right = body("right", position=[.7, 1, 0], velocity=[-2, 0, 0], mass=1)
        result = run(scene([left, right], duration=.35, gravity=[0, 0, 0]))
        final = result["frames"][-1]["m"]
        left_x = sum(p[0] for p in final[:6])/6
        right_x = sum(p[0] for p in final[6:])/6
        # Initial COM=-0.7/3; total momentum=2; COM(t)=-0.7/3+2t/3.
        self.assertAlmostEqual((2*left_x+right_x)/3, 0, delta=1e-6)
        self.assertGreater(right_x, .5)

    def test_nonfinite_and_capacity_inputs_fail_before_native_allocation(self):
        definition = scene();definition["entities"][0]["mass"] = float("nan")
        with self.assertRaises(NativeResourceLimitError):
            run(definition)
        definition = scene(fps=10000000)
        with self.assertRaises(NativeResourceLimitError):
            run(definition)

    def test_native_rejects_bad_abi_without_accessing_pointers(self):
        library, _, _ = _load_library(time.monotonic()+20)
        simulation, diagnostics = Simulation(abi=99), Diagnostics()
        status = library.mesh_simulate(ctypes.byref(simulation), ctypes.byref(diagnostics))
        self.assertEqual(status, 1)

    def test_native_memory_bounds_and_allocation_cleanup_under_sanitizers(self):
        source = Path(__file__).with_name("mesh_native_safety.c")
        with tempfile.TemporaryDirectory(prefix="physics-mesh-safety-") as directory:
            executable = Path(directory) / "mesh-native-safety"
            subprocess.run([_compiler(), "-std=c11", "-Wall", "-Wextra", "-Werror", "-O1", "-g",
                            "-fsanitize=address,undefined,float-cast-overflow", "-fno-omit-frame-pointer",
                            str(source), "-lm", "-o", str(executable)], check=True, capture_output=True, timeout=30)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("9 allocation failures", result.stdout)

    def test_deadline_cannot_return_success(self):
        _load_library(time.monotonic()+20)
        definition = scene(duration=100, dt=.0004, fps=.01)
        # Leave enough room for library lookup on cold CI runners while still
        # forcing the much longer 100-second physical solve to hit its deadline.
        result = run_scene_mesh(definition, {"mesh_substeps": 32, "mesh_iterations": 24}, time.monotonic()+1.0)
        self.assertFalse(result["diagnostics"]["completed"])
        self.assertEqual(result["diagnostics"]["native_status"], "timed_out")
        self.assertLess(result["diagnostics"]["simulated_time_s"], 100)


if __name__ == "__main__":
    unittest.main()
