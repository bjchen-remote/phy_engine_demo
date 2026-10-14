"""Independent analytic acceptance tests for the isolated connections C ABI."""
from __future__ import annotations

import ctypes as C
import math
import time
import unittest

from physics_demo.connections_solver import Diagnostics, _load_library, _pack, run_scene_connections
from physics_demo.native_backend import NativeBackendUnavailable, NativeResourceLimitError


def node(ident, position, mass=1., velocity=None, fixed=False):
    return {"id": ident, "type": "point_mass", "position": list(position),
            "velocity": list(velocity or [0., 0., 0.]), "mass": mass, "fixed": fixed}


def series(ident, kind, **kwargs):
    return {"id": ident, "type": "series", "metric": {"type": kind, **kwargs}}


def scene(damping=0., dt=.01, duration=2.):
    return {"world": {"dt": dt, "duration": duration, "output_fps": 24., "gravity": [0., -9.81, 0.]},
            "entities": [node("anchor", [0., 0., 0.], fixed=True), node("bob", [1.2, 0., 0.])],
            "connections": [{"id": "link", "type": "spring", "entities": ["anchor", "bob"],
                             "rest_length": 1., "stiffness": 16., "damping": damping}],
            "force_fields": [], "queries": [series("x", "centroid", entity="bob", axis="x"),
                series("length", "connection_length", connection="link"),
                series("extension", "connection_extension", connection="link"),
                series("force", "spring_force", connection="link"),
                series("energy", "spring_energy", connection="link")]}


def plan(value, substeps=1, iterations=24):
    by_id = {e["id"]: e for e in value["entities"]}
    sums = {ident: 0. for ident in by_id}
    dampings = {ident: 0. for ident in by_id}
    for link in value["connections"]:
        if link["type"] == "spring":
            for ident in link["entities"]:
                sums[ident] += link["stiffness"]
                dampings[ident] += link.get("damping", 0.)
    omega = math.sqrt(max((2*sums[i]/e["mass"] for i, e in by_id.items() if not e["fixed"]), default=0))
    constraint_steps = 8 if any(e["type"] != "spring" for e in value["connections"]) else 1
    damping_rate = max((2*dampings[i]/e["mass"] for i, e in by_id.items() if not e["fixed"]), default=0.)
    return {"connection_substeps": max(substeps, constraint_steps, math.ceil(value["world"]["dt"]*omega/.15),
                                       math.ceil(value["world"]["dt"]*damping_rate/.25)),
            "connection_iterations": iterations}


def run(value, **kwargs):
    return run_scene_connections(value, plan(value, **kwargs), time.monotonic()+20)


def uniform(value, acceleration, targets=None, start=0., end=None):
    value["force_fields"] = [{"id": "gravity", "type": "uniform", "acceleration": acceleration,
        "targets": targets or [e["id"] for e in value["entities"] if not e["fixed"]],
        "start_time": start, "end_time": end if end is not None else value["world"]["duration"]}]


def damped_extension(t, mass, stiffness, damping, amplitude):
    gamma = damping/(2*mass)
    discriminant = stiffness/mass-gamma*gamma
    if abs(discriminant) < 1e-12:
        return amplitude*math.exp(-gamma*t)*(1+gamma*t)
    if discriminant > 0:
        frequency = math.sqrt(discriminant)
        return amplitude*math.exp(-gamma*t)*(math.cos(frequency*t)+gamma/frequency*math.sin(frequency*t))
    frequency = math.sqrt(-discriminant)
    r1, r2 = -gamma+frequency, -gamma-frequency
    return amplitude*(r2*math.exp(r1*t)-r1*math.exp(r2*t))/(r2-r1)


class ConnectionSolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library, _, _ = _load_library(time.monotonic()+30)

    def native(self, value, **kwargs):
        simulation = _pack(value, plan(value, **kwargs), 10.)
        diagnostics = Diagnostics()
        status = self.library.connections_simulate(C.byref(simulation), C.byref(diagnostics))
        self.assertEqual(status, 0)
        self.assertTrue(diagnostics.completed)
        return simulation, diagnostics

    def test_optional_tensile_failure_is_irreversible_and_queries_zero_after_break(self):
        value = scene(dt=.01, duration=.2)
        value["entities"][1]["position"] = [1., 0., 0.]
        value["entities"][1]["velocity"] = [1., 0., 0.]
        value["connections"][0]["break_tensile_strain"] = .05
        result = run(value)
        times = result["diagnostics"]["connection_break_times_s"]
        self.assertEqual(result["diagnostics"]["broken_connection_count"], 1)
        self.assertGreater(times[0], .05)
        self.assertLess(times[0], .07)
        self.assertGreater(result["diagnostics"]["connection_break_lengths_m"][0], 1.05)
        self.assertFalse(result["diagnostics"]["connection_energy_conservation_applicable"])
        self.assertEqual(result["frames"][0]["connection_active"], [True])
        self.assertEqual(result["frames"][-1]["connection_active"], [False])
        for t, force, energy in zip(result["observations"]["times"],
                                    result["observations"]["columns"]["force"],
                                    result["observations"]["columns"]["energy"]):
            if t >= times[0]:
                self.assertEqual((force, energy), (0., 0.))
        self.assertEqual(result["diagnostics"]["final_spring_energy"], 0.)
        self.assertEqual(sum(not frame["connection_active"][0] for frame in result["frames"]),
                         sum(frame["t"] + 1e-12 >= times[0] for frame in result["frames"]))

    def test_threshold_above_peak_does_not_break_and_initial_break_never_heals(self):
        value = scene(dt=.01, duration=.2)
        value["entities"][1]["position"] = [1., 0., 0.]
        value["entities"][1]["velocity"] = [1., 0., 0.]
        value["connections"][0]["break_tensile_strain"] = .5
        intact = run(value)
        self.assertEqual(intact["diagnostics"]["connection_break_times_s"], [None])
        self.assertTrue(all(frame["connection_active"] == [True] for frame in intact["frames"]))
        self.assertTrue(intact["diagnostics"]["connection_energy_conservation_applicable"])
        value["entities"][1]["position"] = [1.2, 0., 0.]
        value["entities"][1]["velocity"] = [-2., 0., 0.]
        value["connections"][0]["break_tensile_strain"] = .1
        broken = run(value)
        self.assertEqual(broken["diagnostics"]["connection_break_times_s"], [0.])
        self.assertAlmostEqual(broken["diagnostics"]["connection_break_lengths_m"][0], 1.2)
        self.assertFalse(broken["diagnostics"]["connection_prebreak_energy_conservation_applicable"])
        self.assertTrue(all(frame["connection_active"] == [False] for frame in broken["frames"]))
        self.assertLess(broken["frames"][-1]["g"][1][0], 1.)
        self.assertTrue(all(force == 0. for force in broken["observations"]["columns"]["force"]))
        self.assertTrue(all(energy == 0. for energy in broken["observations"]["columns"]["energy"]))

    def test_only_overstretched_edge_in_small_network_fails(self):
        value = scene(dt=.005, duration=.1)
        value["entities"][1]["position"] = [1., 0., 0.]
        value["entities"][1]["velocity"] = [1., 0., 0.]
        value["entities"].append(node("end", [2., 0., 0.]))
        value["connections"][0]["break_tensile_strain"] = .03
        value["connections"].append({"id": "second", "type": "spring",
                                      "entities": ["bob", "end"], "rest_length": 1.,
                                      "stiffness": 16., "damping": 0.,
                                      "break_tensile_strain": .5})
        result = run(value)
        self.assertEqual(result["diagnostics"]["broken_connection_count"], 1)
        self.assertIsNotNone(result["diagnostics"]["connection_break_times_s"][0])
        self.assertIsNone(result["diagnostics"]["connection_break_times_s"][1])
        self.assertEqual(result["frames"][-1]["connection_active"], [False, True])

    def test_undamped_oscillator_second_order(self):
        errors = []
        for dt in (.02, .01):
            value = scene(dt=dt, duration=4.)
            result = run(value)
            observations = result["observations"]
            errors.append(max(abs(x-1-.2*math.cos(4*t)) for t, x in zip(observations["times"], observations["columns"]["x"])))
            self.assertLess(abs(result["diagnostics"]["final_kinetic_energy"]
                                + result["diagnostics"]["final_spring_energy"]-.32), .001)
            self.assertTrue(result["diagnostics"]["connection_energy_conservation_applicable"])
        self.assertLess(errors[1], .00021)
        self.assertGreater(errors[0]/errors[1], 3.8)

    def test_peak_energy_drift_cannot_be_hidden_by_endpoint_return(self):
        value = scene(dt=.02, duration=math.pi/2)
        value["queries"].append(series("speed", "speed", entity="bob"))
        result = run(value)
        diagnostic = result["diagnostics"]
        initial = diagnostic["initial_kinetic_energy"]+diagnostic["initial_spring_energy"]
        final = diagnostic["final_kinetic_energy"]+diagnostic["final_spring_energy"]
        endpoint_drift = abs(final-initial)/initial
        peak = diagnostic["connection_peak_relative_energy_drift"]
        columns = result["observations"]["columns"]
        observed_peak = max(abs(.5*speed*speed+energy-initial)/initial
                            for speed, energy in zip(columns["speed"], columns["energy"]))
        self.assertLess(endpoint_drift, 1e-5)
        self.assertGreater(peak, .001)
        self.assertGreater(peak, 100*endpoint_drift)
        self.assertAlmostEqual(peak, observed_peak, places=13)

    def test_damped_under_critical_over_and_stiff_damping(self):
        for damping in (2., 8., 12.):
            with self.subTest(damping=damping):
                value = scene(damping=damping, dt=.001, duration=1.)
                result = run(value)
                error = max(abs(x-1-damped_extension(t, 1., 16., damping, .2)) for t, x in zip(
                    result["observations"]["times"], result["observations"]["columns"]["x"]))
                self.assertLess(error, 2e-6)
                self.assertFalse(result["diagnostics"]["connection_energy_conservation_applicable"])

    def test_two_free_masses_analytic_mode_and_momentum(self):
        value = scene(dt=.002, duration=3.)
        value["entities"] = [node("anchor", [0, 0, 0], mass=2, velocity=[.3, 0, 0]),
                             node("bob", [1.2, 0, 0], mass=3, velocity=[.3, 0, 0])]
        value["queries"] = [series("a", "centroid", entity="anchor", axis="x"),
                             series("b", "centroid", entity="bob", axis="x"),
                             series("d", "center_distance", entities=["anchor", "bob"])]
        result = run(value)["observations"]
        omega = math.sqrt(16*(1/2+1/3))
        for t, a, b, distance in zip(result["times"], *(result["columns"][k] for k in ("a", "b", "d"))):
            self.assertAlmostEqual((2*a+3*b)/5, .72+.3*t, places=11)
            self.assertAlmostEqual(distance, 1+.2*math.cos(omega*t), delta=6e-6)
        simulation, _ = self.native(value)
        self.assertAlmostEqual(2*simulation.velocities[0]+3*simulation.velocities[3], 1.5, places=12)

    def test_static_gravity_extension_and_fixed_node(self):
        value = scene(duration=2.)
        value["connections"][0]["stiffness"] = 100.
        value["entities"][0]["position"] = [0., 2., 0.]
        value["entities"][1]["position"] = [0., 2.-1.-9.81/100, 0.]
        uniform(value, [0., -9.81, 0.])
        result = run(value)
        for frame in result["frames"]:
            self.assertEqual(frame["g"][0], [0., 2., 0.])
            self.assertAlmostEqual(frame["g"][1][1], 2.-1.-9.81/100, places=12)

    def test_rod_small_pendulum_period_and_refinement(self):
        errors = []
        for dt in (.02, .01):
            value = scene(dt=dt, duration=10.)
            theta = .05
            value["entities"][1]["position"] = [math.sin(theta), -math.cos(theta), 0.]
            value["connections"][0] = {"id": "link", "type": "rod", "entities": ["anchor", "bob"], "rest_length": 1.}
            value["queries"] = [series("x", "centroid", entity="bob", axis="x"),
                                 series("length", "connection_length", connection="link")]
            uniform(value, [0., -9.81, 0.])
            result = run(value)
            t, x = result["observations"]["times"], result["observations"]["columns"]["x"]
            crossings = [t[i]+(t[i+1]-t[i])*x[i]/(x[i]-x[i+1]) for i in range(len(t)-1) if x[i]>0>=x[i+1]]
            expected = 2*math.pi/math.sqrt(9.81)*(1+theta*theta/16)
            self.assertGreaterEqual(len(crossings), 4)
            self.assertAlmostEqual((crossings[-1]-crossings[0])/(len(crossings)-1), expected, delta=.001)
            self.assertLess(result["diagnostics"]["max_rod_error_m"], 1e-12)
            final = result["frames"][-1]["g"][1]
            mechanical = result["diagnostics"]["final_kinetic_energy"]+9.81*(final[1]+1)
            initial = 9.81*(1-math.cos(theta))
            errors.append(abs(mechanical-initial)/initial)
        self.assertLess(errors[1], 5e-6)
        self.assertLess(errors[1], errors[0]*.35)

    def test_spring_chain_normal_mode(self):
        value = scene(dt=.001, duration=3.)
        n = 4
        value["entities"] = [node(str(i), [i+.02*math.sin(math.pi*i/(n+1)), 0., 0.], fixed=i in (0, n+1))
                             for i in range(n+2)]
        value["connections"] = [{"id": str(i), "type": "spring", "entities": [str(i), str(i+1)],
                                 "rest_length": 1., "stiffness": 25., "damping": 0.} for i in range(n+1)]
        value["queries"] = [series("x", "centroid", entity="2", axis="x")]
        result = run(value)["observations"]
        omega = 10*math.sin(math.pi/(2*(n+1)))
        self.assertLess(max(abs(x-2-.02*math.sin(2*math.pi/(n+1))*math.cos(omega*t))
                            for t, x in zip(result["times"], result["columns"]["x"])), 1e-7)

    def test_double_pendulum_small_mode_and_long_chain_iterations(self):
        value = scene(dt=.002, duration=3.)
        theta = .01
        phi = math.sqrt(2)*theta
        value["entities"] = [node("anchor", [0., 0., 0.], fixed=True),
            node("a", [math.sin(theta), -math.cos(theta), 0.]),
            node("b", [math.sin(theta)+math.sin(phi), -math.cos(theta)-math.cos(phi), 0.])]
        value["connections"] = [{"id": str(i), "type": "rod", "entities": pair, "rest_length": 1.}
                                 for i, pair in enumerate((["anchor", "a"], ["a", "b"]))]
        value["queries"] = [series("x", "centroid", entity="a", axis="x")]
        uniform(value, [0., -9.81, 0.])
        result = run(value)
        omega = math.sqrt(9.81*(2-math.sqrt(2)))
        self.assertLess(max(abs(x-theta*math.cos(omega*t)) for t, x in zip(
            result["observations"]["times"], result["observations"]["columns"]["x"])), 4e-6)
        value["world"].update(dt=.01, duration=1.)
        value["entities"] = [node(str(i), [i*math.sin(.2), -i*math.cos(.2), 0.], fixed=i==0)
                             for i in range(17)]
        value["connections"] = [{"id": str(i), "type": "rod", "entities": [str(i), str(i+1)],
                                 "rest_length": 1.} for i in range(16)]
        value["queries"] = []
        uniform(value, [0., -9.81, 0.])
        coarse = run(value, iterations=8)["diagnostics"]["max_constraint_error_ratio"]
        fine = run(value, iterations=64)["diagnostics"]["max_constraint_error_ratio"]
        self.assertLess(fine, 1.)
        self.assertLess(fine, coarse*.1)

    def test_rope_slack_tautening_and_release(self):
        for speed in (1., -.1):
            value = scene(dt=.01, duration=1.)
            value["entities"][1]["position"] = [.5, 0., 0.]
            value["entities"][1]["velocity"] = [speed, 0., 0.]
            value["connections"][0] = {"id": "link", "type": "rope", "entities": ["anchor", "bob"], "rest_length": 1.}
            value["queries"] = [series("x", "centroid", entity="bob", axis="x")]
            result = run(value)
            for t, x in zip(result["observations"]["times"], result["observations"]["columns"]["x"]):
                self.assertAlmostEqual(x, min(1., .5+speed*t), delta=2e-12)
            self.assertLess(result["diagnostics"]["max_rope_extension_m"], 1e-12)

    def test_metric_units_sign_and_force_velocity(self):
        value = scene(damping=3.)
        value["entities"][1]["velocity"] = [-.5, 0., 0.]
        result = run(value)["observations"]["columns"]
        self.assertAlmostEqual(result["length"][0], 1.2)
        self.assertAlmostEqual(result["extension"][0], .2)
        self.assertAlmostEqual(result["force"][0], 16*.2-3*.5)
        self.assertAlmostEqual(result["energy"][0], .5*16*.2*.2)

    def test_fps_does_not_change_observations_or_fixed_initial_frame(self):
        value = scene(dt=.007, duration=.517)
        first = run(value)
        value["world"]["output_fps"] = 53.
        second = run(value)
        self.assertEqual(first["observations"], second["observations"])
        self.assertEqual(first["frames"][0]["g"], [e["position"] for e in value["entities"]])
        self.assertEqual(second["frames"][-1]["t"], .517)
        self.assertEqual(len(second["frames"]), math.ceil(.517*53)+1)

    def test_terminal_remainder_matches_native_macro_sampling(self):
        for dt, duration, expected in ((.01, .14, 14), (.0083333333, 1., 120)):
            value = scene(dt=dt, duration=duration)
            result = run(value)
            self.assertEqual(result["diagnostics"]["steps"], expected)
            self.assertEqual(len(result["observations"]["times"]), expected+1)
            self.assertEqual(result["frames"][-1]["t"], duration)
            self.assertEqual(result["diagnostics"]["simulated_time_s"], duration)

    def test_fields_event_window_and_radial_vortex(self):
        # A nearly force-free spring makes the field solution independently simple.
        value = scene(dt=.01, duration=.1)
        value["connections"][0]["stiffness"] = 1e-9
        value["entities"][1]["position"] = [1., 0., 0.]
        uniform(value, [2., 0., 0.], start=.0037, end=.0371)
        simulation, _ = self.native(value)
        interval = .0371-.0037
        self.assertAlmostEqual(simulation.velocities[3], 2*interval, delta=1e-10)
        self.assertAlmostEqual(simulation.positions[3], 1+interval*interval+2*interval*(.1-.0371), delta=1e-10)
        for kind in ("radial", "vortex"):
            value = scene(dt=.001, duration=.001)
            value["connections"][0]["stiffness"] = 1e-9
            value["entities"][1]["position"] = [1., 0., 0.]
            field = {"id": "f", "type": kind, "center": [0., 0., 0.], "strength": 2., "radius": 2.,
                     "targets": ["bob"], "start_time": 0., "end_time": .001}
            if kind == "vortex":
                field.update(axis=[0., 0., 1.], inward_strength=.5)
            value["force_fields"] = [field]
            simulation, _ = self.native(value)
            self.assertAlmostEqual(simulation.velocities[3 if kind == "radial" else 4], .001, delta=1e-8)

    def test_native_abi_bounds_pointers_nan_and_deadline(self):
        self.assertEqual(self.library.connections_abi_version(), 3)
        mutations = [("abi", 999), ("nodes", 65), ("connections", 257), ("metrics", 17),
                     ("substeps", 0), ("substeps", 65), ("iterations", 65), ("dt", math.nan),
                     ("frame_capacity", 1), ("positions", C.POINTER(C.c_double)()),
                     ("break_times", C.POINTER(C.c_double)()),
                     ("break_lengths", C.POINTER(C.c_double)())]
        for name, value in mutations:
            with self.subTest(name=name, value=value):
                original = scene()
                simulation = _pack(original, plan(original), 10.)
                setattr(simulation, name, value)
                diag = Diagnostics()
                self.assertEqual(self.library.connections_simulate(C.byref(simulation), C.byref(diag)), 1)
        for mutation in (lambda s: setattr(s.connection[0], "a", 900),
                         lambda s: setattr(s.connection[0], "type", 9),
                         lambda s: setattr(s.connection[0], "stiffness", -1),
                         lambda s: setattr(s.connection[0], "break_tensile_strain", 11),
                         lambda s: setattr(s.metric[0], "a", 999),
                         lambda s: s.mass.__setitem__(1, 0),
                         lambda s: s.velocities.__setitem__(0, 1)):
            original = scene()
            simulation = _pack(original, plan(original), 10.)
            mutation(simulation)
            self.assertEqual(self.library.connections_simulate(C.byref(simulation), C.byref(Diagnostics())), 1)
        original = scene()
        simulation = _pack(original, plan(original), -1.)
        diag = Diagnostics()
        self.assertEqual(self.library.connections_simulate(C.byref(simulation), C.byref(diag)), 3)
        self.assertEqual(diag.frames_written, 1)
        self.assertEqual(diag.steps, 0)
        self.assertFalse(diag.completed)
        with self.assertRaises(NativeBackendUnavailable):
            run_scene_connections(original, plan(original), time.monotonic()-1)
        long_scene = scene(dt=.0001, duration=20.)
        long_scene["queries"] = []
        simulation = _pack(long_scene, plan(long_scene), .0001)
        diag = Diagnostics()
        self.assertEqual(self.library.connections_simulate(C.byref(simulation), C.byref(diag)), 3)
        self.assertFalse(diag.completed)
        self.assertLess(diag.simulated_time_s, 20.)
        self.assertLess(diag.runtime_s, .1)

    def test_native_initial_constraint_and_stiffness_guards(self):
        for kind in ("rod", "rope"):
            value = scene()
            value["queries"] = []
            value["connections"][0] = {"id": "link", "type": kind, "entities": ["anchor", "bob"], "rest_length": 1.}
            simulation = _pack(value, plan(value), 10.)
            self.assertEqual(self.library.connections_simulate(C.byref(simulation), C.byref(Diagnostics())), 1)
        value["connections"][0]["type"] = "rod"
        value["entities"][1]["position"] = [1., 0., 0.]
        value["entities"][1]["velocity"] = [1., 0., 0.]
        simulation = _pack(value, plan(value), 10.)
        self.assertEqual(self.library.connections_simulate(C.byref(simulation), C.byref(Diagnostics())), 1)
        value["entities"][1]["velocity"] = [0., 1., 0.]
        self.native(value)
        value = scene()
        value["connections"][0]["stiffness"] = 1e7
        with self.assertRaises(NativeResourceLimitError):
            run(value)
        value = scene(damping=1e5)
        with self.assertRaises(NativeResourceLimitError):
            run(value)


if __name__ == "__main__":
    unittest.main()
