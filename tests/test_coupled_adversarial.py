"""Public coupled input boundaries and field-window planning regression tests."""
from __future__ import annotations

import copy
import json
import time
import unittest

from physics_demo.api import call_tool
from physics_demo.coupled_solver import run_scene_coupled
from physics_demo.schema import normalize_and_validate


def scene():
    return {
        "version": 1, "name": "coupled-adversarial",
        "world": {"gravity": [0, 0, 0], "duration": .01, "dt": .001,
                  "output_fps": 10, "bounds": {"min": [-2, -2, -2], "max": [2, 2, 2]}},
        "budget": {"wall_time_s": 60, "quality": "preview", "backend": "native"},
        "entities": [
            {"id": "body", "type": "rigid_body", "shape": {"type": "sphere", "radius": .1},
             "mass": 1, "position": [0, 0, 0], "velocity": [0, 0, 0],
             "angular_velocity": [0, 0, 0], "orientation": [1, 0, 0, 0]},
            {"id": "point", "type": "point_mass", "mass": 1,
             "position": [1, 0, 0], "velocity": [0, 0, 0]}],
        "colliders": [], "interactions": {"mutual_gravity": False},
        "coupling": {"substeps": 4, "iterations": 3, "friction": .2},
        "connections": [{"id": "link", "type": "spring",
            "endpoints": [{"entity": "body", "local_point": [0, 0, 0]}, {"entity": "point"}],
            "rest_length": 1, "stiffness": 16, "damping": .1,
            "solid": {"radius": .01, "mass": 0}}],
    }


def prepare(definition):
    return call_tool("physics_prepare", {"scene_json": json.dumps(definition), "budget_seconds": 60})


def pulse(substeps):
    definition = scene()
    definition["world"].update(duration=.002, dt=.002)
    definition["entities"] = [definition["entities"][1]]
    definition["entities"][0]["position"] = [0, 0, 0]
    definition["connections"] = []
    definition["coupling"].update(substeps=substeps, iterations=1)
    definition["force_fields"] = [{"id": "pulse", "type": "uniform", "targets": ["point"],
        "acceleration": [1, 0, 0], "start_time": .000123, "end_time": .001234}]
    definition["queries"] = [{"id": "speed", "type": "series", "metric": {"type": "speed", "entity": "point"}}]
    return definition


class CoupledAdversarialTests(unittest.TestCase):
    def test_new_fields_reject_wrong_json_types_without_tracebacks(self):
        cases = [
            (("coupling",), None), (("coupling",), []),
            (("coupling", "substeps"), True), (("coupling", "substeps"), 1.5),
            (("coupling", "substeps"), 65), (("coupling", "iterations"), 17),
            (("coupling", "friction"), "0.2"),
            (("entities", 0, "shape", "type"), []),
            (("entities", 0, "shape", "radius"), False),
            (("entities", 0, "shape", "radius"), 1e308),
            (("entities", 0, "mass"), 10**400),
            (("entities", 0, "orientation"), []),
            (("entities", 0, "orientation"), [True, 0, 0, 0]),
            (("entities", 0, "orientation"), [0, 0, 0, 0]),
            (("entities", 0, "orientation"), [1e308, 0, 0, 0]),
            (("entities", 0, "angular_velocity"), "spin fast"),
            (("entities", 0, "angular_velocity"), [1e308, 0, 0]),
            (("entities", 0, "fixed"), []),
            (("entities", 0, "pivot"), {"point": [0, 0, 0], "local_point": None}),
            (("entities", 1, "collision_radius"), False),
            (("connections",), {}), (("connections", 0), None),
            (("connections", 0, "id"), []), (("connections", 0, "type"), {}),
            (("connections", 0, "endpoints"), "body,point"),
            (("connections", 0, "endpoints", 0, "entity"), []),
            (("connections", 0, "endpoints", 0, "local_point"), None),
            (("connections", 0, "rest_length"), True),
            (("connections", 0, "stiffness"), {}),
            (("connections", 0, "damping"), []),
            (("connections", 0, "solid"), False),
            (("connections", 0, "solid", "radius"), None),
            (("connections", 0, "solid", "mass"), "heavy"),
        ]
        for path, value in cases:
            with self.subTest(path=path, value=str(value)[:50]):
                definition = scene()
                target = definition
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                original = copy.deepcopy(definition)
                normalized = normalize_and_validate(definition)
                self.assertFalse(normalized["valid"])
                self.assertTrue(normalized["errors"])
                response = prepare(definition)
                self.assertFalse(response["ok"])
                self.assertFalse(response.get("ready_to_simulate", False))
                self.assertTrue(response["errors"])
                json.dumps(response, allow_nan=False)
                self.assertEqual(definition, original)

    def test_tiny_point_mass_is_rejected_before_frequency_overflow(self):
        definition = scene()
        definition["entities"][1]["mass"] = 1e-320
        normalized = normalize_and_validate(definition)
        self.assertFalse(normalized["valid"])
        response = prepare(definition)
        self.assertFalse(response["ok"])
        self.assertIn("coupling_point_mass", {e["code"] for e in response["errors"]})
        self.assertNotIn("infinity", json.dumps(response))

    def test_force_window_boundaries_reserve_substeps_before_native_execution(self):
        response = prepare(pulse(64))
        self.assertFalse(response["ok"])
        self.assertFalse(response["ready_to_simulate"])
        self.assertIn("coupling_budget_exceeded", {e["code"] for e in response["errors"]})

    def test_initial_contact_cfl_is_planned_and_tiny_radii_are_rejected(self):
        definition = pulse(4)
        definition.pop("force_fields")
        definition["world"].update(duration=.05, dt=.05)
        definition["entities"][0].update(collision_radius=.1, velocity=[500, 0, 0])
        response = prepare(definition)
        self.assertFalse(response["ready_to_simulate"])
        self.assertIn("coupling_budget_exceeded", {e["code"] for e in response["errors"]})
        for radius in (1e-320, 1e-6):
            with self.subTest(radius=radius):
                definition["entities"][0]["collision_radius"] = radius
                response = prepare(definition)
                self.assertFalse(response["ok"])
                self.assertIn("point_collision_radius", {e["code"] for e in response["errors"]})

    def test_mesh_thickness_constrains_shared_contact_cfl_before_simulation(self):
        definition = scene()
        definition["world"].update(gravity=[0, -9.81, 0], duration=.01, dt=.001)
        definition["entities"] = [
            {"id": "drop", "type": "fluid", "shape": {"type": "sphere", "center": [0, .015, 0], "radius": .003},
             "spacing": .0004, "velocity": [0, -1, 0],
             "properties": {"viscosity": .03, "surface_tension": .5}},
            {"id": "ground", "type": "mesh", "motion": "static", "thickness": 1e-5,
             "mesh": {"vertices": [[-.03, 0, -.03], [.03, 0, -.03],
                                   [.03, 0, .03], [-.03, 0, .03]],
                      "triangles": [[0, 1, 2], [0, 2, 3]]}},
        ]
        definition["connections"] = []
        too_thin = prepare(definition)
        self.assertFalse(too_thin["ready_to_simulate"])
        self.assertIn("coupling_budget_exceeded", {e["code"] for e in too_thin["errors"]})
        self.assertGreater(too_thin["plan"]["coupling_initial_cfl_substeps"], 64)

        definition["entities"][1]["thickness"] = .0004
        resolved = prepare(definition)
        self.assertTrue(resolved["ready_to_simulate"], resolved)
        self.assertLessEqual(resolved["plan"]["coupling_initial_cfl_substeps"], 64)

    def test_mesh_pin_attachment_uses_zero_velocity(self):
        definition = scene()
        definition["world"].update(duration=.001, dt=.001)
        definition["entities"][0] = {
            "id": "cloth", "type": "mesh", "position": [0, 0, 0], "velocity": [1, 0, 0],
            "pinned_vertices": [0],
            "mesh": {"vertices": [[0, 0, 0], [0, .1, 0], [0, 0, .1]], "triangles": [[0, 1, 2]]}}
        definition["connections"] = [{"id": "rod", "type": "rod", "rest_length": 1,
            "endpoints": [{"entity": "cloth", "vertex": 0}, {"entity": "point"}]}]
        response = prepare(definition)
        self.assertTrue(response["ready_to_simulate"], response.get("errors"))
        trajectory = run_scene_coupled(response["scene"], response["plan"], time.monotonic()+20)
        self.assertTrue(trajectory["diagnostics"]["completed"])
        self.assertEqual(trajectory["frames"][-1]["m"][0], [0, 0, 0])
        self.assertEqual(trajectory["frames"][-1]["g"][0], [1, 0, 0])

    def test_within_budget_pulse_integrates_only_actual_overlap(self):
        response = prepare(pulse(4))
        self.assertTrue(response["ok"], response)
        self.assertTrue(response["ready_to_simulate"])
        trajectory = run_scene_coupled(response["scene"], response["plan"], time.monotonic()+20)
        self.assertTrue(trajectory["diagnostics"]["completed"])
        self.assertTrue(trajectory["diagnostics"]["finite"])
        interval = .001234 - .000123
        self.assertAlmostEqual(trajectory["observations"]["columns"]["speed"][-1], interval, places=13)
        self.assertAlmostEqual(trajectory["frames"][-1]["g"][0][0],
                               .5*interval*interval + interval*(.002-.001234), places=13)


if __name__ == "__main__":
    unittest.main()
