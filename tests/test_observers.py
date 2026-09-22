from __future__ import annotations

import copy
import ctypes
import json
import math
from pathlib import Path
import time
import unittest

from physics_demo.engine import RigidBody, run_scene
from physics_demo.native_backend import (
    ABI_VERSION, CDiagnostics, CObservationEntity, CObservationMetric, CSimulation,
    _load_library, run_scene_native,
)
from physics_demo.observers import Observer, unpack_observations
from physics_demo.planning import make_plan
from physics_demo.schema import normalize_and_validate


def _scene(entities: list[dict]) -> dict:
    checked = normalize_and_validate({
        "version": 1, "name": "observer-analytic",
        "world": {"gravity": [0, 0, 0], "duration": 0.2, "dt": 0.05, "output_fps": 5,
                  "bounds": {"min": [-10, -10, -10], "max": [10, 10, 10]}},
        "budget": {"wall_time_s": 30, "quality": "preview"},
        "entities": entities, "colliders": [], "interactions": {"mutual_gravity": False},
    })
    assert checked["valid"], checked
    return checked["scene"]


class ObserverTests(unittest.TestCase):
    def test_native_samples_preserve_precision_and_ignore_unwritten_capacity(self):
        times = (ctypes.c_double * 3)(0, .1, math.nan)
        values = (ctypes.c_double * 6)(1 + 1e-12, -2, 3, -4, math.nan, math.nan)
        result = unpack_observations(["position", "speed"], times, values, 2)
        self.assertEqual(result["times"], [0., .1])
        self.assertEqual(result["columns"], {"position": [1 + 1e-12, 3.], "speed": [-2., -4.]})
        values[0] = 99
        self.assertEqual(result["columns"]["position"][0], 1 + 1e-12)

    def test_native_zero_samples_and_nbody_only_samples_do_not_read_scalar_buffer(self):
        null = ctypes.POINTER(ctypes.c_double)()
        empty = unpack_observations(["position"], null, null, 0)
        self.assertEqual(empty["times"], [])
        self.assertEqual(empty["columns"], {"position": []})
        nbody_only = unpack_observations([], (ctypes.c_double * 2)(0, .1), null, 2)
        self.assertEqual(nbody_only["times"], [0., .1])
        self.assertEqual(nbody_only["columns"], {})

    def test_constant_velocity_native_and_python_observations_ignore_video_fps(self):
        scene = _scene([
            {"id": "a", "type": "point_mass", "position": [0, 0, 0], "velocity": [1, 2, 0], "mass": 2},
            {"id": "b", "type": "point_mass", "position": [3, 0, 0], "velocity": [0, 0, 0], "mass": 1},
        ])
        scene["queries"] = [
            {"id": "x", "type": "series", "metric": {"type": "centroid", "entity": "a", "axis": "x"}},
            {"id": "speed", "type": "series", "metric": {"type": "speed", "entity": "a"}},
            {"id": "distance", "type": "series", "metric": {"type": "center_distance", "entities": ["a", "b"]}},
            {"id": "stable", "type": "nbody_stability", "max_radius": 10, "min_separation": 0.1},
        ]
        plan = make_plan(scene, include_video=False)
        results = [solver(scene, plan, time.monotonic() + 30) for solver in (run_scene, run_scene_native)]
        for result in results:
            observation = result["observations"]
            self.assertEqual(len(result["frames"]), 2)
            self.assertEqual(len(observation["times"]), 5)
            for index, t in enumerate(observation["times"]):
                self.assertAlmostEqual(observation["columns"]["x"][index], t, places=13)
                self.assertAlmostEqual(observation["columns"]["speed"][index], math.sqrt(5), places=13)
                self.assertAlmostEqual(observation["columns"]["distance"][index], math.hypot(3 - t, 2 * t), places=13)
                self.assertAlmostEqual(observation["nbody"]["stable"]["energy"][index], 5, places=13)
                self.assertEqual(observation["nbody"]["stable"]["momentum"][index], [2, 4, 0])
        self.assertEqual(results[0]["observations"], results[1]["observations"])
        scene["world"]["output_fps"] = 60
        plan = make_plan(scene, include_video=False)
        for solver in (run_scene, run_scene_native):
            self.assertEqual(solver(scene, plan, time.monotonic() + 30)["observations"], results[0]["observations"])

    def test_particle_radius_uses_all_particles_and_float64(self):
        scene = _scene([{"id": "cloud", "type": "granular", "spacing": 0.2,
                         "shape": {"type": "box", "center": [0, 1, 0], "size": [0.8, 0.1, 0.1]},
                         "velocity": [0.3, 0, 0], "properties": {"friction": 0, "cohesion": 0}},
                        {"id": "unrelated-pool", "type": "granular", "spacing": 0.2,
                         "shape": {"type": "box", "center": [4, 1, 3], "size": [0.8, 0.1, 0.1]},
                         "velocity": [0, 0, 0], "properties": {"friction": 0, "cohesion": 0}}])
        scene["queries"] = [{"id": "radius", "type": "series", "metric": {
            "type": "spread_radius", "entity": "cloud", "plane": "xz", "origin": [0, 1, 0]}}]
        plan = make_plan(scene, include_video=False)
        plan["render_particle_limit"] = 1
        for solver in (run_scene, run_scene_native):
            result = solver(scene, plan, time.monotonic() + 30)
            self.assertEqual(len(result["frames"][0]["p"]), 1)
            self.assertEqual(result["diagnostics"]["particle_count"], 8)
            observation = result["observations"]
            for t, radius in zip(observation["times"], observation["columns"]["radius"]):
                self.assertAlmostEqual(radius, 0.3 + 0.3 * t, places=12)

    def test_speed_is_centroid_velocity_not_mean_particle_speed(self):
        scene = _scene([{"id": "cloud", "type": "granular", "spacing": 0.2,
                         "shape": {"type": "box", "center": [0, 0, 0], "size": [0.4, 0.1, 0.1]},
                         "velocity": [0, 0, 0], "properties": {"friction": 0, "cohesion": 0}}])
        scene["force_fields"] = [{"id": "outward", "type": "radial", "targets": ["cloud"],
                                  "center": [0, 0, 0], "strength": 1, "radius": 2,
                                  "start_time": 0, "end_time": 0.2}]
        scene["queries"] = [
            {"id": "speed", "type": "series", "metric": {"type": "speed", "entity": "cloud"}},
            {"id": "x", "type": "series", "metric": {"type": "centroid", "entity": "cloud", "axis": "x"}},
        ]
        plan = make_plan(scene, include_video=False)
        for solver in (run_scene, run_scene_native):
            result = solver(scene, plan, time.monotonic() + 30)
            self.assertGreater(abs(result["frames"][-1]["p"][0][0]), abs(result["frames"][0]["p"][0][0]))
            for key in ("speed", "x"):
                for value in result["observations"]["columns"][key]:
                    self.assertAlmostEqual(value, 0, places=13)

    def test_native_and_python_static_surface_gap_definitions(self):
        for shapes, expected in [
            ([{"type": "sphere", "radius": 0.5}, {"type": "sphere", "radius": 1}], 3.5),
            ([{"type": "box", "size": [2, 2, 2]}, {"type": "box", "size": [2, 2, 2]}], 2),
        ]:
            with self.subTest(shapes=shapes):
                scene = _scene([
                    {"id": "a", "type": "rigid", "shape": shapes[0], "position": [0, 0, 0], "mass": 0},
                    {"id": "b", "type": "rigid", "shape": shapes[1], "position": [3, 4, 0], "mass": 0},
                ])
                scene["queries"] = [{"id": "gap", "type": "series", "metric": {
                    "type": "surface_gap", "entities": ["a", "b"]}}]
                plan = make_plan(scene, include_video=False)
                for solver in (run_scene, run_scene_native):
                    result = solver(scene, plan, time.monotonic() + 30)
                    self.assertEqual(result["observations"]["columns"]["gap"], [expected] * 5)

    def test_nbody_full_window_extrema_survive_video_decimation(self):
        raw = json.loads((Path(__file__).parents[1] / "examples" / "three_body.json").read_text())
        scene = normalize_and_validate(raw)["scene"]
        scene["world"].update(duration=2.0, output_fps=1)
        scene["queries"] = [{"id": "stability", "type": "nbody_stability", "max_radius": 2, "min_separation": 0.1}]
        plan = make_plan(scene, include_video=False)
        results = [solver(scene, plan, time.monotonic() + 30) for solver in (run_scene, run_scene_native)]
        for result in results:
            observations = result["observations"]
            self.assertEqual(len(observations["times"]), result["diagnostics"]["steps"] + 1)
            self.assertEqual(len(observations["times"]), 401)
            self.assertEqual(len(result["frames"]), 3)
            data = observations["nbody"]["stability"]
            self.assertGreater(max(data["max_com_radius"]), max(data["max_com_radius"][0], data["max_com_radius"][-1]) + 0.02)
            self.assertLess(min(data["min_pair_distance"]), min(data["min_pair_distance"][0], data["min_pair_distance"][-1]) - 0.1)
        for key in ("max_com_radius", "min_pair_distance", "energy"):
            for reference, native in zip(results[0]["observations"]["nbody"]["stability"][key],
                                         results[1]["observations"]["nbody"]["stability"][key]):
                self.assertAlmostEqual(reference, native, places=11)

    def test_decimal_terminal_tail_preserves_completed_macro_step_count(self):
        scene = _scene([{"id": "a", "type": "point_mass", "position": [0, 0, 0],
                         "velocity": [1, 0, 0], "mass": 1}])
        scene["world"].update(duration=4, dt=0.0083333333, output_fps=1)
        scene["queries"] = [{"id": "x", "type": "series", "metric": {
            "type": "centroid", "entity": "a", "axis": "x"}}]
        plan = make_plan(scene, include_video=False)
        for solver in (run_scene, run_scene_native):
            result = solver(scene, plan, time.monotonic() + 30)
            times = result["observations"]["times"]
            self.assertTrue(result["diagnostics"]["completed"])
            self.assertEqual(len(times), result["diagnostics"]["steps"] + 1)
            self.assertLessEqual(len(times), plan["steps"] + 1)
            self.assertLess(abs(times[-1] - 4), scene["world"]["dt"] * 1e-5)

    def test_observer_tracks_rigid_updates_and_signed_gap(self):
        scene = {"interactions": {"gravity_G": 1, "mutual_gravity": False, "softening": 0.001},
                 "queries": [{"id": "gap", "type": "series", "metric": {
                     "type": "surface_gap", "entities": ["a", "b"]}}]}
        boxes = [RigidBody("a", [0, 0, 0], [0, 0, 0], 1, {"type": "box", "size": [2, 2, 2]}),
                 RigidBody("b", [3, 0, 0], [0, 0, 0], 1, {"type": "box", "size": [2, 2, 2]})]
        observer = Observer(scene, [], [], boxes)
        observer.capture(0, [], [], boxes)
        boxes[1].pos[0] = 2
        observer.capture(1, [], [], boxes)
        boxes[1].pos[0] = 1.5
        observer.capture(2, [], [], boxes)
        self.assertEqual(observer.result["columns"]["gap"], [1, 0, -0.5])

    def test_no_queries_does_not_change_trajectory(self):
        scene = _scene([{"id": "a", "type": "point_mass", "position": [0, 0, 0],
                         "velocity": [1, 0, 0], "mass": 1}])
        plan = make_plan(scene, include_video=False)
        with_queries = copy.deepcopy(scene)
        with_queries["queries"] = [{"id": "x", "type": "series", "metric": {
            "type": "centroid", "entity": "a", "axis": "x"}}]
        for solver in (run_scene, run_scene_native):
            plain = solver(scene, plan, time.monotonic() + 30)
            observed = solver(with_queries, plan, time.monotonic() + 30)
            self.assertNotIn("observations", plain)
            self.assertEqual(plain["frames"], observed["frames"])

    def test_immovable_objects_report_executed_zero_speed(self):
        for entity in [
            {"id": "fixed", "type": "rigid", "shape": {"type": "sphere", "radius": 0.5},
             "mass": 0, "position": [1, 2, 3], "velocity": [4, 5, 6]},
            {"id": "fixed", "type": "point_mass", "mass": 1, "fixed": True,
             "position": [1, 2, 3], "velocity": [4, 5, 6]},
        ]:
            with self.subTest(entity=entity["type"]):
                scene = _scene([entity])
                scene["queries"] = [
                    {"id": "speed", "type": "series", "metric": {"type": "speed", "entity": "fixed"}},
                    {"id": "x", "type": "series", "metric": {"type": "centroid", "entity": "fixed", "axis": "x"}},
                ]
                plan = make_plan(scene, include_video=False)
                for solver in (run_scene, run_scene_native):
                    result = solver(scene, plan, time.monotonic() + 30)
                    self.assertEqual(result["observations"]["columns"]["speed"], [0] * 5)
                    self.assertEqual(result["observations"]["columns"]["x"], [1] * 5)

    def test_native_observation_arguments_reject_bad_ranges_before_writing(self):
        library, _, _ = _load_library(30)

        def simulation():
            entity = CObservationEntity(kind=2, start=0, count=1, shape=1,
                                        size=(ctypes.c_double * 3)(1, 0, 0),
                                        velocity=(ctypes.c_double * 3)(4, 5, 6))
            metrics = (CObservationMetric * 1)(CObservationMetric(type=2, a=entity, b=entity))
            return CSimulation(
                abi_version=ABI_VERSION, frame_capacity=2, density_iterations=1,
                divergence_iterations=1, thread_count=1, max_substeps=8,
                spacing=0.2, dt=0.05, duration=0.2, output_fps=5,
                bounds_min=(ctypes.c_double * 3)(-10, -10, -10),
                bounds_max=(ctypes.c_double * 3)(10, 10, 10), deadline_seconds=1,
                frame_times=(ctypes.c_double * 2)(), observation_metric_count=1,
                observation_capacity=5, observation_metrics=metrics,
                observation_times=(ctypes.c_double * 5)(), observation_values=(ctypes.c_double * 5)(),
            )

        valid = simulation()
        diagnostics = CDiagnostics()
        self.assertEqual(library.phy_simulate(ctypes.byref(valid), ctypes.byref(diagnostics)), 0)
        self.assertEqual(diagnostics.observations_written, 5)
        self.assertEqual(list(valid.observation_values[:5]), [0] * 5)
        mutations = {
            "too_many_metrics": lambda s: setattr(s, "observation_metric_count", 17),
            "negative_metric_count": lambda s: setattr(s, "observation_metric_count", -1),
            "insufficient_capacity": lambda s: setattr(s, "observation_capacity", 1),
            "excessive_capacity": lambda s: setattr(s, "observation_capacity", 250003),
            "missing_times": lambda s: setattr(s, "observation_times", ctypes.POINTER(ctypes.c_double)()),
            "missing_values": lambda s: setattr(s, "observation_values", ctypes.POINTER(ctypes.c_double)()),
            "missing_metrics": lambda s: setattr(s, "observation_metrics", ctypes.POINTER(CObservationMetric)()),
            "invalid_nbody_flag": lambda s: setattr(s, "observation_nbody", 2),
            "nbody_without_bodies": lambda s: setattr(s, "observation_nbody", 1),
            "invalid_metric_kind": lambda s: setattr(s.observation_metrics[0], "type", 5),
            "invalid_axis": lambda s: s.observation_metrics[0].axes.__setitem__(0, 3),
            "nonfinite_origin": lambda s: s.observation_metrics[0].origin.__setitem__(0, math.nan),
            "negative_start": lambda s: setattr(s.observation_metrics[0].a, "start", -1),
            "zero_count": lambda s: setattr(s.observation_metrics[0].a, "count", 0),
            "oversized_static_count": lambda s: setattr(s.observation_metrics[0].a, "count", 2**31 - 1),
            "missing_particle_group": lambda s: setattr(s.observation_metrics[0].a, "kind", 0),
            "missing_body": lambda s: setattr(s.observation_metrics[0].a, "kind", 1),
            "zero_sphere_radius": lambda s: s.observation_metrics[0].a.size.__setitem__(0, 0),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                candidate = simulation()
                mutate(candidate)
                diagnostics = CDiagnostics()
                self.assertEqual(library.phy_simulate(ctypes.byref(candidate), ctypes.byref(diagnostics)), 1)
                self.assertEqual(diagnostics.observations_written, 0)
                self.assertEqual(diagnostics.frames_written, 0)


if __name__ == "__main__":
    unittest.main()
