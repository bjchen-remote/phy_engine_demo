"""Independent angle-ODE checks of the common Cartesian SHAKE/RATTLE solver.

The reference exists only in tests. For absolute angles measured from downward
Y, let d=q1-q2, A=(m1+m2)l1², B=m2*l1*l2, C=m2*l2². The Lagrangian is
  T = (A*w1²+C*w2²)/2 + B*cos(d)*w1*w2,
  V = -(m1+m2)*g*l1*cos(q1) - m2*g*l2*cos(q2).
Euler-Lagrange gives M*q''=f with M=[[A,B*cos(d)],[B*cos(d),C]] and
  f1 = -B*sin(d)*w2² - (m1+m2)*g*l1*sin(q1),
  f2 =  B*sin(d)*w1² - m2*g*l2*sin(q2).
The matrix determinant is positive for positive lengths and masses. This uses
neither Cartesian constraint projection nor any production force/energy code.
"""
from __future__ import annotations

import math
import time
import unittest

from physics_demo.analysis.pendulum import pendulum_report
from physics_demo.core.connections_solver import run_scene_connections
from physics_demo.io.planning import make_plan
from physics_demo.io.records import RecordedRun
from physics_demo.io.schema import normalize_and_validate
from physics_demo.systems import build_system


DEFAULT = {"lengths": [1., 1.], "masses": [1., 1.],
           "angles": [2., 2.4], "angular_velocities": [0., 0.], "gravity": 9.81}
ASYMMETRIC = {"lengths": [1.2, .7], "masses": [1.3, .8],
              "angles": [1.2, -.5], "angular_velocities": [.2, -.15], "gravity": 9.81}


def _derivative(y, spec):
    q1, q2, w1, w2 = y
    l1, l2 = spec["lengths"]
    m1, m2 = spec["masses"]
    g = spec["gravity"]
    a, b, c = (m1 + m2)*l1*l1, m2*l1*l2, m2*l2*l2
    sine, cosine = math.sin(q1-q2), math.cos(q1-q2)
    cross = b*cosine
    f1 = -b*sine*w2*w2 - (m1+m2)*g*l1*math.sin(q1)
    f2 = b*sine*w1*w1 - m2*g*l2*math.sin(q2)
    determinant = a*c-cross*cross
    return (w1, w2, (c*f1-cross*f2)/determinant,
            (a*f2-cross*f1)/determinant)


def _energy(y, spec):
    q1, q2, w1, w2 = y
    l1, l2 = spec["lengths"]
    m1, m2 = spec["masses"]
    g = spec["gravity"]
    return (.5*(m1+m2)*l1*l1*w1*w1 + .5*m2*l2*l2*w2*w2
            + m2*l1*l2*math.cos(q1-q2)*w1*w2
            - (m1+m2)*g*l1*math.cos(q1) - m2*g*l2*math.cos(q2))


def _positions(y, spec):
    l1, l2 = spec["lengths"]
    first = (l1*math.sin(y[0]), -l1*math.cos(y[0]), 0.)
    return (first, (first[0]+l2*math.sin(y[1]), first[1]-l2*math.cos(y[1]), 0.))


def _oracle(spec, h, *, duration=2., sample_dt=.008):
    y = tuple(spec["angles"] + spec["angular_velocities"])
    positions, energies = [_positions(y, spec)], [_energy(y, spec)]
    stride, count = round(sample_dt/h), round(duration/sample_dt)
    assert abs(stride*h-sample_dt) < 1e-14
    for _ in range(count):
        for _ in range(stride):
            k1 = _derivative(y, spec)
            k2 = _derivative(tuple(a+h*b/2 for a, b in zip(y, k1)), spec)
            k3 = _derivative(tuple(a+h*b/2 for a, b in zip(y, k2)), spec)
            k4 = _derivative(tuple(a+h*b for a, b in zip(y, k3)), spec)
            y = tuple(a+h*(b+2*c+2*d+e)/6 for a, b, c, d, e in zip(y, k1, k2, k3, k4))
        positions.append(_positions(y, spec))
        energies.append(_energy(y, spec))
    return positions, energies


def _run(spec, *, duration, dt, kind="double_pendulum"):
    raw = build_system({**spec, "type": kind, "duration": duration,
                        "dt": dt, "output_fps": 1})
    validated = normalize_and_validate(raw)
    assert validated["valid"], validated["errors"]
    scene = validated["scene"]
    plan = make_plan(scene, include_video=False)
    assert plan["connection_fits_limits"]
    trajectory = run_scene_connections(scene, plan, time.monotonic()+20)
    assert trajectory["diagnostics"]["completed"] and trajectory["diagnostics"]["finite"]
    return RecordedRun(scene, trajectory["observations"], {}), plan


def _position_error(run, reference, sample_dt=.008):
    dt = run.scene["world"]["dt"]
    stride = round(sample_dt/dt)
    positions = [list(zip(*(run.metric(f"bob{i}", "centroid", axis=a) for a in "xyz")))
                 for i in (1, 2)]
    assert len(run.times[::stride]) == len(reference)
    return max(math.dist(points[j*stride], expected[i])
               for j, expected in enumerate(reference) for i, points in enumerate(positions))


class PendulumAccuracyTests(unittest.TestCase):
    def test_independent_equations_have_zero_energy_time_derivative(self):
        # A five-point derivative of independently evaluated T+V checks the
        # Coriolis signs and the absolute-angle convention before using RK4.
        for spec in (DEFAULT, ASYMMETRIC):
            for y in ((.7, -.4, .3, -1.2), (2., 2.4, -2., 1.), (-1., .2, 3., -.8)):
                flow = _derivative(y, spec)
                epsilon = 1e-5
                values = [_energy(tuple(a+factor*epsilon*b for a, b in zip(y, flow)), spec)
                          for factor in (-2, -1, 1, 2)]
                rate = (values[0]-8*values[1]+8*values[2]-values[3])/(12*epsilon)
                self.assertLess(abs(rate), 2e-8, (spec, y, rate))

    def test_two_second_positions_and_second_order_timestep_convergence(self):
        for spec in (DEFAULT, ASYMMETRIC):
            with self.subTest(spec=spec):
                reference, energies = _oracle(spec, .00025)
                refined, _ = _oracle(spec, .000125)
                oracle_difference = max(math.dist(a, b) for row, other in zip(reference, refined)
                                        for a, b in zip(row, other))
                self.assertLess(oracle_difference, 1e-8)
                self.assertLess(max(abs(e-energies[0]) for e in energies), 1e-8)
                errors, settings = [], []
                for dt in (.008, .004, .002):
                    run, plan = _run(spec, duration=2., dt=dt)
                    settings.append((plan["connection_substeps"], plan["connection_iterations"]))
                    errors.append(_position_error(run, refined))
                self.assertEqual(len(set(settings)), 1, "dt refinement must preserve solver settings")
                self.assertLess(errors[0], 5e-4, errors)
                self.assertLess(errors[-1], 5e-5, errors)
                for coarse, fine in zip(errors, errors[1:]):
                    ratio = coarse/fine
                    self.assertGreater(ratio, 2.8, errors)
                    self.assertLess(ratio, 5.2, errors)

    def test_eight_second_default_energy_and_rod_lengths(self):
        run, _ = _run(DEFAULT, duration=8., dt=.002)
        report = pendulum_report(run)
        self.assertEqual(len(run.times), 4001)
        self.assertAlmostEqual(run.times[-1], 8., delta=1e-10)
        # Independently reduce the measured Cartesian data at every step, so
        # the public report cannot satisfy this test by omitting an excursion.
        columns = [(mass, run.metric(f"bob{i}", "speed"),
                    run.metric(f"bob{i}", "centroid", axis="y"))
                   for i, mass in enumerate(DEFAULT["masses"], 1)]
        measured_energy = [sum(mass*(.5*speed[j]**2+DEFAULT["gravity"]*height[j])
                               for mass, speed, height in columns)
                           for j in range(len(run.times))]
        for expected, actual in zip(measured_energy, report["total_energy_J"]):
            self.assertAlmostEqual(expected, actual, delta=1e-12)
        self.assertAlmostEqual(report["peak_energy_drift_J"],
                               max(abs(e-measured_energy[0]) for e in measured_energy), delta=1e-12)
        # The energy scale is the maximum magnitude of gravitational potential,
        # so this criterion remains meaningful near zero initial total energy.
        self.assertLess(report["peak_energy_drift_fraction_of_scale"], 1e-5)
        self.assertLess(report["maximum_rod_length_error_m"], 2e-8)
        self.assertAlmostEqual(report["total_energy_J"][0],
                               _energy(tuple(DEFAULT["angles"]+DEFAULT["angular_velocities"]), DEFAULT),
                               places=12)

    def test_small_angle_single_pendulum_period(self):
        length, gravity = 1.3, 9.81
        spec = {"lengths": [length], "masses": [2.7], "angles": [.01],
                "angular_velocities": [0.], "gravity": gravity}
        run, _ = _run(spec, duration=8., dt=.004, kind="pendulum")
        x = run.metric("bob1", "centroid", axis="x")
        descending = []
        for a, b, t0, t1 in zip(x, x[1:], run.times, run.times[1:]):
            if a > 0 >= b:
                descending.append(t0+(t1-t0)*a/(a-b))
        self.assertGreaterEqual(len(descending), 3)
        expected = 2*math.pi*math.sqrt(length/gravity)
        for a, b in zip(descending, descending[1:]):
            # At .01 rad the physical finite-amplitude correction is 6.25e-6;
            # the remaining allowance covers integration and crossing sampling.
            self.assertLess(abs((b-a)/expected-1), 2e-5)


if __name__ == "__main__":
    unittest.main()
