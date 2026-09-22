"""Analytic oracles for the bounded horizontal-rail slider model."""
from __future__ import annotations

import random
import time
import unittest

from physics_demo.sliders import SliderResolutionError, run_scene


def _slider(identifier: str, x: float, vx: float, **kwargs) -> dict:
    return {"type": "slider", "id": identifier, "position": [x, 0.5, 0],
            "velocity": [vx, 0, 0], "size": [1, 1, 1], "mass": 1.0,
            "acceleration": 0.0, "friction": 0.0, "restitution": 0.0, **kwargs}


def _scene(entities: list[dict], duration=2.0, dt=0.3, fps=3) -> dict:
    queries = [{"id": "gap", "type": "series", "metric": {"type": "surface_gap", "entities": [entities[0]["id"], entities[-1]["id"]]}}]
    for entity in entities:
        queries.extend([
            {"id": entity["id"] + "-x", "type": "series", "metric": {"type": "centroid", "entity": entity["id"], "axis": "x"}},
            {"id": entity["id"] + "-speed", "type": "series", "metric": {"type": "speed", "entity": entity["id"]}},
        ])
    return {"world": {"gravity": [0, -10, 0], "duration": duration, "dt": dt, "output_fps": fps},
            "entities": entities, "interactions": {"mutual_gravity": False, "gravity_G": 1, "softening": 0.001},
            "queries": queries, "colliders": [], "force_fields": []}


def _run(scene: dict) -> dict:
    return run_scene(scene, {"effective_spacing": 0.1}, time.monotonic() + 10.0)


class SliderTests(unittest.TestCase):
    def test_separation_exact_float64_measurement_and_crossing(self):
        trajectory = _run(_scene([_slider("left", -0.5, -0.2), _slider("right", 0.5, 0.3)]))
        observations = trajectory["observations"]
        for time_value, gap in zip(observations["times"], observations["columns"]["gap"]):
            self.assertAlmostEqual(gap, 0.5 * time_value, places=13)
        self.assertEqual(len(trajectory["frames"]), 7)
        self.assertAlmostEqual(observations["columns"]["gap"][-1], 1.0, places=13)
        self.assertTrue(trajectory["diagnostics"]["completed"])
        self.assertEqual(trajectory["diagnostics"]["steps"], 7)

    def test_measurements_independent_of_render_fps(self):
        scene = _scene([_slider("a", -0.5, -0.17), _slider("b", 0.5, 0.34)], dt=0.07)
        low = _run(scene)
        scene["world"]["output_fps"] = 60
        high = _run(scene)
        self.assertEqual(low["observations"], high["observations"])
        self.assertNotEqual(len(low["frames"]), len(high["frames"]))

    def test_unequal_mass_elastic_collision_conserves_momentum_energy(self):
        trajectory = _run(_scene([_slider("a", -1, 2, mass=2, restitution=1),
                                 _slider("b", 1, -1, mass=1, restitution=1)], duration=1))
        values = trajectory["observations"]["columns"]
        # Impact t=1/3.  Analytic velocities after impact: 0, 3 m/s.
        self.assertAlmostEqual(values["a-speed"][-1], 0.0, places=12)
        self.assertAlmostEqual(values["b-speed"][-1], 3.0, places=12)
        self.assertAlmostEqual(values["a-x"][-1], -1.0 / 3.0, places=12)
        self.assertAlmostEqual(values["b-x"][-1], 8.0 / 3.0, places=12)
        initial_momentum = 2.0 * 2.0 - 1.0
        final_momentum = 2.0 * values["a-speed"][-1] + values["b-speed"][-1]
        initial_energy = 0.5 * 2.0 * 2.0 ** 2 + 0.5 * 1.0
        final_energy = 0.5 * 2.0 * values["a-speed"][-1] ** 2 + 0.5 * values["b-speed"][-1] ** 2
        self.assertAlmostEqual(final_momentum, initial_momentum, places=12)
        self.assertAlmostEqual(final_energy, initial_energy, places=12)
        self.assertEqual(trajectory["diagnostics"]["slider_impact_count"], 1)

    def test_friction_stops_without_reversing(self):
        trajectory = _run(_scene([_slider("a", -5, 2, friction=0.2), _slider("b", 5, 0)], duration=2, dt=0.7))
        values = trajectory["observations"]["columns"]
        self.assertAlmostEqual(values["a-x"][-1], -4.0, places=12)
        self.assertEqual(values["a-speed"][-1], 0.0)
        self.assertEqual(values["a-speed"][-2], 0.0)
        self.assertGreater(trajectory["diagnostics"]["substeps"], trajectory["diagnostics"]["steps"])

    def test_static_friction_and_drive_restart_are_exact(self):
        trajectory = _run(_scene([_slider("a", -5, 0, friction=0.2, acceleration=1),
                                 _slider("b", 5, 0, friction=0.2, acceleration=3)], duration=1))
        values = trajectory["observations"]["columns"]
        self.assertEqual(values["a-x"][-1], -5.0)
        self.assertAlmostEqual(values["b-x"][-1], 5.5, places=12)
        self.assertAlmostEqual(values["b-speed"][-1], 1.0, places=12)

    def test_persistent_contact_mass_weighted_acceleration(self):
        trajectory = _run(_scene([_slider("a", -0.5, 0, mass=2, acceleration=3),
                                 _slider("b", 0.5, 0, mass=1)], duration=1))
        values = trajectory["observations"]["columns"]
        self.assertAlmostEqual(values["a-x"][-1], 0.5, places=12)
        self.assertAlmostEqual(values["b-x"][-1], 1.5, places=12)
        self.assertAlmostEqual(values["a-speed"][-1], 2.0, places=12)
        self.assertAlmostEqual(values["gap"][-1], 0.0, places=12)

    def test_contact_static_friction_can_hold_driven_slider(self):
        trajectory = _run(_scene([_slider("a", -0.5, 0, friction=0.1, acceleration=2),
                                 _slider("b", 0.5, 0, friction=1)], duration=1))
        values = trajectory["observations"]["columns"]
        self.assertEqual(values["a-x"][-1], -0.5)
        self.assertEqual(values["b-x"][-1], 0.5)

    def test_accelerating_pair_collision_does_not_tunnel(self):
        trajectory = _run(_scene([_slider("a", -1, 0, acceleration=8),
                                 _slider("b", 1, 0)], duration=1, dt=1))
        values = trajectory["observations"]["columns"]
        self.assertAlmostEqual(values["gap"][-1], 0.0, places=11)
        self.assertEqual(trajectory["diagnostics"]["slider_impact_count"], 1)
        self.assertAlmostEqual(values["a-x"][-1], 1.5, places=11)
        self.assertAlmostEqual(values["b-x"][-1], 2.5, places=11)

    def test_video_samples_exact_parabola_even_coarse_macro_steps(self):
        trajectory = _run(_scene([_slider("a", -5, 0, acceleration=2),
                                 _slider("b", 5, 0)], duration=1, dt=1, fps=10))
        for frame in trajectory["frames"]:
            self.assertAlmostEqual(frame["r"][0][0], -5 + frame["t"] ** 2, places=7)

    def test_deadline_never_claims_completed(self):
        trajectory = run_scene(_scene([_slider("a", -1, 0), _slider("b", 1, 0)]), {}, time.monotonic() - 1)
        self.assertFalse(trajectory["diagnostics"]["completed"])
        self.assertEqual(trajectory["diagnostics"]["simulated_time_s"], 0)
        self.assertEqual(len(trajectory["observations"]["times"]), 1)

    def test_initial_overlap_is_explicit_failure(self):
        with self.assertRaisesRegex(SliderResolutionError, "initial_overlap"):
            _run(_scene([_slider("a", 0, 0), _slider("b", 0.5, 0)]))

    def test_scene_without_queries_omits_observations(self):
        scene = _scene([_slider("a", -1, 0), _slider("b", 1, 0)])
        scene.pop("queries")
        self.assertNotIn("observations", _run(scene))

    def test_seeded_five_slider_contacts_match_finer_timestep(self):
        # Exercises repeated impacts into resting frictional contact chains.
        # A regression previously let velocity residuals split a contact chain.
        random_source = random.Random(962)
        for trial in range(50):
            entities = [_slider(
                str(index), -5 + 1.4 * index, random_source.uniform(-2, 2),
                mass=random_source.uniform(0.5, 2), acceleration=random_source.uniform(-2, 2),
                friction=random_source.uniform(0, 0.4), restitution=random_source.choice([0, 1]),
            ) for index in range(5)]
            scene = _scene(entities, duration=2, dt=0.17, fps=10)
            scene["world"]["gravity"] = [0, -9.81, 0]
            coarse = _run(scene)
            scene["world"]["dt"] = 0.007
            fine = _run(scene)
            with self.subTest(trial=trial):
                for index in range(5):
                    column = str(index) + "-x"
                    self.assertAlmostEqual(coarse["observations"]["columns"][column][-1],
                                           fine["observations"]["columns"][column][-1], places=9)
                self.assertGreaterEqual(coarse["diagnostics"]["slider_min_surface_gap_m"], -1e-9)


if __name__ == "__main__":
    unittest.main()
