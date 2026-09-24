"""Persistent DFSPH stepping: old integrator parity and externally changed state."""
from __future__ import annotations

import ctypes as C
import math
from pathlib import Path
import subprocess
import tempfile
import unittest

from physics_demo.native_backend import (
    CSimulation, CDiagnostics, CForceField, _compiler, _load_library,
)


def array(kind, values):
    return (kind * len(values))(*values)


def fixture(*, count=8, sand=False, threads=1, duration=.02, dt=.001):
    """Small native fixture; no scene validator/packer can hide ABI errors."""
    coordinates = [[.12 * (i % 2), .12 * ((i // 2) % 2), .12 * (i // 4)] for i in range(count)]
    materials = [int(sand and i == count - 1) for i in range(count)]
    s = CSimulation(abi_version=5, particle_count=count, body_count=1,
                    render_count=count, frame_capacity=2, density_iterations=5,
                    divergence_iterations=2, thread_count=threads, max_substeps=1,
                    spacing=.12, dt=dt, duration=duration, output_fps=10,
                    gravity=(C.c_double * 3)(0, -1, 0),
                    bounds_min=(C.c_double * 3)(-10, -10, -10),
                    bounds_max=(C.c_double * 3)(10, 10, 10),
                    softening=.01, water_sand_drag=.1, wetting_rate=.25,
                    deadline_seconds=20,
                    material=array(C.c_int32, materials),
                    body_fixed=array(C.c_int32, [0]), body_mass=array(C.c_double, [2]),
                    body_x=array(C.c_double, [2]), body_y=array(C.c_double, [2]),
                    body_z=array(C.c_double, [2]), body_vx=array(C.c_double, [.1]),
                    body_vy=array(C.c_double, [0]), body_vz=array(C.c_double, [0]),
                    render_indices=array(C.c_int32, range(count)),
                    frame_particles=array(C.c_float, [-17] * (2 * count * 3)),
                    frame_bodies=array(C.c_double, [-17] * 6),
                    frame_times=array(C.c_double, [-17, -17]))
    for axis, name in enumerate(("x", "y", "z")):
        values = [row[axis] for row in coordinates]
        setattr(s, name, array(C.c_double, values))
        setattr(s, "anchor_" + name, array(C.c_double, values))
        setattr(s, "v" + name, array(C.c_double, [.1 if axis == 0 else 0] * count))
    for name, value in (("viscosity", .01), ("surface_tension", .01), ("friction", .2),
                        ("cohesion", .05 if sand else 0), ("wetness", .1 if sand else 0)):
        setattr(s, name, array(C.c_double, [value] * count))
    return s


class ParticleStepTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library, _, _ = _load_library(30)
        cls.library.phy_context_create.argtypes = [C.POINTER(CSimulation), C.POINTER(CDiagnostics)]
        cls.library.phy_context_create.restype = C.c_void_p
        cls.library.phy_context_step.argtypes = [C.c_void_p, C.c_double, C.c_double]
        cls.library.phy_context_step.restype = C.c_int
        cls.library.phy_context_destroy.argtypes = [C.c_void_p]
        cls.library.phy_context_destroy.restype = None
        cls.library.phy_context_refresh.argtypes = [C.c_void_p]
        cls.library.phy_context_refresh.restype = C.c_int
        cls.library.phy_context_set_velocity_decay.argtypes = [C.c_void_p, C.c_double]
        cls.library.phy_context_set_velocity_decay.restype = C.c_int

    def context(self, simulation):
        diagnostics = CDiagnostics()
        context = self.library.phy_context_create(C.byref(simulation), C.byref(diagnostics))
        self.assertTrue(context, diagnostics.status)
        self.addCleanup(self.library.phy_context_destroy, context)
        return context, diagnostics

    def test_persistent_steps_match_original_water_sand_integrator(self):
        for threads in (1, 2):
            with self.subTest(threads=threads):
                whole, stepped = fixture(sand=True, threads=threads), fixture(sand=True, threads=threads)
                original = CDiagnostics()
                self.assertEqual(self.library.phy_simulate(C.byref(whole), C.byref(original)), 0)
                context, diagnostic = self.context(stepped)
                for index in range(20):
                    self.assertEqual(self.library.phy_context_step(context, index * .001, .001), 0)
                for name in ("x", "y", "z", "vx", "vy", "vz", "wetness", "anchor_x", "anchor_y", "anchor_z"):
                    for index in range(whole.particle_count):
                        self.assertAlmostEqual(getattr(whole, name)[index], getattr(stepped, name)[index], places=12)
                self.assertEqual(diagnostic.substeps, original.substeps)
                self.assertAlmostEqual(diagnostic.final_mean_water_density_ratio,
                                       original.final_mean_water_density_ratio, places=12)
                self.assertAlmostEqual(diagnostic.mean_sand_displacement_m,
                                       original.mean_sand_displacement_m, places=12)
                self.assertAlmostEqual(diagnostic.mean_sand_wetness, original.mean_sand_wetness, places=12)
                self.assertEqual(stepped.body_x[0], 2)
                self.assertGreater(whole.body_x[0], 2)
                self.assertEqual((diagnostic.frames_written, diagnostic.observations_written, diagnostic.completed),
                                 (0, 0, 0))
                self.assertEqual(list(stepped.frame_times[:2]), [-17, -17])
                self.assertEqual(stepped.frame_particles[0], -17)

    def test_external_impulse_and_translation_are_accepted_next_step(self):
        value = fixture(count=1)
        value.gravity[:] = [0, 0, 0]
        value.surface_tension[0] = value.viscosity[0] = 0
        context, diagnostics = self.context(value)
        self.assertEqual(self.library.phy_context_step(context, 0, .001), 0)
        value.x[0], value.y[0], value.vx[0], value.vy[0] = 3, 2, 2, -1
        self.assertEqual(self.library.phy_context_step(context, .001, .001), 0)
        damping = .9995 ** (.001 * 90)
        self.assertAlmostEqual(value.x[0], 3.002, places=12)
        self.assertAlmostEqual(value.y[0], 1.999, places=12)
        self.assertAlmostEqual(value.vx[0], 2 * damping, places=11)
        self.assertAlmostEqual(value.vy[0], -damping, places=11)
        self.assertAlmostEqual(diagnostics.represented_water_volume_m3, .12 ** 3)

    def test_force_field_uses_absolute_time(self):
        value = fixture(count=1)
        value.material[0] = 1
        value.gravity[:] = [0, 0, 0]
        value.vx[0] = value.friction[0] = value.cohesion[0] = 0
        field = CForceField(type=0, vector=(C.c_double * 3)(2, 0, 0), start_time=.01, end_time=.02)
        value.field_count = 1
        value.fields = (CForceField * 1)(field)
        value.particle_field_mask = array(C.c_uint32, [1])
        value.body_field_mask = array(C.c_uint32, [0])
        context, _ = self.context(value)
        self.assertEqual(self.library.phy_context_step(context, 0, .01), 0)
        self.assertEqual(value.vx[0], 0)
        self.assertEqual(self.library.phy_context_step(context, .01, .01), 0)
        self.assertAlmostEqual(value.vx[0], .02, places=12)
        self.assertAlmostEqual(value.x[0], .0002, places=12)

    def test_external_position_change_rebuilds_neighbors(self):
        value, expected = fixture(count=2), fixture(count=2)
        value.x[1] = 3
        context, diagnostic = self.context(value)
        self.assertEqual(self.library.phy_context_step(context, 0, .001), 0)
        self.assertEqual(diagnostic.max_neighbors, 0)
        # Replace the coupled state's positions and velocities by a fresh close
        # pair. Its next step must equal that pair's fresh-context step exactly.
        for name in ("x", "y", "z", "vx", "vy", "vz"):
            for index in range(2):
                getattr(value, name)[index] = getattr(expected, name)[index]
        fresh, _ = self.context(expected)
        self.assertEqual(self.library.phy_context_step(context, .001, .001), 0)
        self.assertEqual(self.library.phy_context_step(fresh, .001, .001), 0)
        self.assertEqual(diagnostic.max_neighbors, 1)
        for name in ("x", "y", "z", "vx", "vy", "vz"):
            self.assertEqual(list(getattr(value, name)[:2]), list(getattr(expected, name)[:2]))

    def test_explicit_zero_decay_preserves_isolated_particle_momentum(self):
        value = fixture(count=1)
        value.gravity[:] = [0, 0, 0]
        context, _ = self.context(value)
        setter = self.library.phy_context_set_velocity_decay
        self.assertEqual(setter(None, 0), 1)
        self.assertEqual(setter(context, -1), 1)
        self.assertEqual(setter(context, math.nan), 1)
        self.assertEqual(setter(context, 0), 0)
        self.assertEqual(self.library.phy_context_step(context, 0, .01), 0)
        self.assertAlmostEqual(value.vx[0], .1, places=14)
        self.assertEqual(setter(context, 2), 0)
        self.assertEqual(self.library.phy_context_step(context, .01, .01), 0)
        self.assertAlmostEqual(value.vx[0], .1*math.exp(-.02), places=14)

    def test_refresh_recomputes_post_contact_density_without_advancing(self):
        value = fixture(count=2)
        context, diagnostic = self.context(value)
        self.assertEqual(self.library.phy_context_step(context, 0, .001), 0)
        old_density = diagnostic.final_mean_water_density_ratio
        value.x[1] = 3
        state = tuple(tuple(getattr(value, name)[:2]) for name in ("x", "y", "z", "vx", "vy", "vz"))
        self.assertEqual(self.library.phy_context_refresh(context), 0)
        self.assertEqual(state, tuple(tuple(getattr(value, name)[:2]) for name in ("x", "y", "z", "vx", "vy", "vz")))
        self.assertLess(diagnostic.final_mean_water_density_ratio, old_density)
        self.assertEqual(diagnostic.substeps, 1)
        self.assertEqual(diagnostic.simulated_time_s, .001)
        self.assertEqual(self.library.phy_context_refresh(None), 1)

    def test_invalid_step_never_advances_and_valid_retry_works(self):
        value = fixture(count=1)
        context, diagnostic = self.context(value)
        before = (value.x[0], value.vx[0])
        for t, h in ((0, 0), (0, -1), (-1, .001), (math.nan, .001), (0, math.inf),
                     (0, 1e-300), (.019, .002)):
            self.assertEqual(self.library.phy_context_step(context, t, h), 1)
            self.assertEqual((value.x[0], value.vx[0]), before)
        self.assertEqual(diagnostic.substeps, 0)
        self.assertEqual(self.library.phy_context_step(context, 0, .001), 0)
        value.x[0] = math.nan
        self.assertEqual(self.library.phy_context_step(context, .001, .001), 4)
        self.assertFalse(diagnostic.finite)

    def test_descriptor_counts_are_snapshotted_and_state_arrays_are_borrowed(self):
        value = fixture(count=1)
        context, _ = self.context(value)
        value.particle_count = 2 ** 30
        value.vx[0] = 2
        self.assertEqual(self.library.phy_context_step(context, 0, .001), 0)
        self.assertGreater(value.x[0], .001)

    def test_create_validates_before_array_access(self):
        for name, bad in (("particle_count", 24001), ("body_count", 65), ("collider_count", 65),
                          ("field_count", 33), ("frame_capacity", 1), ("max_substeps", 0),
                          ("abi_version", 6), ("duration", math.inf), ("thread_count", 33)):
            with self.subTest(name=name):
                value = fixture(count=1)
                setattr(value, name, bad)
                diagnostic = CDiagnostics()
                self.assertFalse(self.library.phy_context_create(C.byref(value), C.byref(diagnostic)))
                self.assertEqual(diagnostic.status, 1)
        value = fixture(count=1)
        value.deadline_seconds = 0
        diagnostic = CDiagnostics()
        self.assertFalse(self.library.phy_context_create(C.byref(value), C.byref(diagnostic)))
        self.assertEqual(diagnostic.status, 3)
        self.assertEqual(self.library.phy_context_step(None, 0, .001), 1)
        self.library.phy_context_destroy(None)

    def test_native_lifecycle_and_rejections_under_sanitizers(self):
        source = Path(__file__).with_name("particle_step_test.c")
        native = source.parent.parent / "physics_demo" / "core" / "native" / "physics_native.c"
        with tempfile.TemporaryDirectory(prefix="physics-particle-step-") as directory:
            executable = Path(directory) / "particle-step-test"
            subprocess.run([_compiler(), "-std=c11", "-Wall", "-Wextra", "-Werror", "-O1", "-g",
                            "-fsanitize=address,undefined,float-cast-overflow", "-fno-omit-frame-pointer",
                            "-pthread", str(source), str(native), "-lm", "-o", str(executable)],
                           check=True, capture_output=True, timeout=30)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
