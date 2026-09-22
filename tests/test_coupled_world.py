"""Real coupled C solves against impulse, momentum and rigid-body oracles."""
from __future__ import annotations

import ctypes as C
import math
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from physics_demo import coupled_solver as bridge
from physics_demo.native_backend import CDiagnostics, _compiler, _load_library as particle_library


def point(ident, p, mass=1., v=(0., 0., 0.), radius=0., fixed=False):
    return {"id": ident, "type": "point_mass", "position": list(p), "velocity": list(v),
            "mass": mass, "fixed": fixed, "collision_radius": radius}


def fluid(p, v=(0., 0., 0.)):
    return {"id": "water", "type": "fluid", "shape": {"type": "sphere", "center": list(p), "radius": .02},
            "velocity": list(v), "properties": {"viscosity": 0., "surface_tension": 0., "friction": 0., "cohesion": 0.}}


def rigid(ident="body", p=(0., 0., 0.), v=(0., 0., 0.), mass=3., shape=None, omega=(0., 0., 0.)):
    return {"id": ident, "type": "rigid_body", "shape": shape or {"type": "sphere", "radius": .08},
            "position": list(p), "velocity": list(v), "mass": mass, "fixed": False,
            "orientation": [1., 0., 0., 0.], "angular_velocity": list(omega), "friction": 0., "restitution": 0.}


def definition(entities, *, duration=.02, dt=.001, substeps=2, gravity=(0., 0., 0.), links=None, queries=None, colliders=None):
    scene = {"entities": entities, "connections": links or [], "queries": queries or [], "colliders": colliders or [],
        "force_fields": [], "coupling": {"substeps": substeps, "iterations": 3, "friction": 0.},
        "world": {"duration": duration, "dt": dt, "output_fps": 50, "gravity": list(gravity),
                  "bounds": {"min": [-10., -10., -10.], "max": [10., 10., 10.]}},
        "interactions": {"mutual_gravity": False, "gravity_G": 1., "softening": .01,
                         "water_sand_drag": 0., "wetting_rate": 0.}}
    plan = {"effective_spacing": .1, "render_particle_limit": 4096, "threads": 1,
        "density_iterations": 5, "divergence_iterations": 2, "max_substeps": 32,
        "coupling_substeps": substeps, "coupling_iterations": 3, "mesh_substeps": 2, "mesh_iterations": 8}
    return scene, plan


def series(ident, kind, **values):
    return {"id": ident, "type": "series", "metric": {"type": kind, **values}}


def vector(value):
    return [value.x, value.y, value.z]


class CoupledWorldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library, _, _ = bridge._load_library(time.monotonic()+30)

    def solve(self, value):
        scene, plan = value
        s, metadata = bridge._pack(scene, plan, time.monotonic()+20)
        d, pd, md = bridge.Diagnostics(), CDiagnostics(), bridge.mesh.Diagnostics()
        status = self.library.coupled_simulate(C.byref(s), C.byref(d), C.byref(pd), C.byref(md))
        self.assertEqual(status, 0)
        self.assertTrue(d.completed)
        self.assertTrue(d.finite)
        self.assertAlmostEqual(d.simulated_time_s, scene["world"]["duration"], places=12)
        return s, d, pd, md, metadata

    def assert_momentum(self, d, expected, tolerance=2e-10):
        for initial, final, reference in zip(vector(d.initial_momentum), vector(d.final_momentum), expected):
            self.assertAlmostEqual(initial, reference, delta=tolerance)
            self.assertAlmostEqual(final, reference, delta=tolerance)

    def test_particle_node_collision_matches_unequal_mass_inelastic_oracle(self):
        s, d, _, _, _ = self.solve(definition([fluid([-.12, 0, 0], [2, 0, 0]), point("node", [0, 0, 0], 3, radius=.08)]))
        # spacing .1 means one water particle weighs exactly 1 kg; the
        # 1 kg + 3 kg perfectly inelastic pair must finish at 0.5 m/s.
        self.assertEqual(s.particles.contents.particle_count, 1)
        self.assertAlmostEqual(s.particles.contents.vx[0], .5, places=11)
        self.assertAlmostEqual(s.point[0].velocity.x, .5, places=11)
        self.assert_momentum(d, [2, 0, 0])
        self.assertGreater(d.contact_count, 0)
        self.assertGreater(d.max_penetration_m, 0)
        self.assertAlmostEqual(d.contact_impulse_norm, 1.5, places=10)

    def test_particle_rigid_collision_has_equal_opposite_linear_response(self):
        s, d, _, _, _ = self.solve(definition([fluid([-.12, 0, 0], [2, 0, 0]), rigid()]))
        self.assertAlmostEqual(s.rigid[0].body.velocity.x, .5, places=11)
        self.assertAlmostEqual(s.particles.contents.vx[0], .5, places=11)
        self.assertEqual(vector(s.rigid[0].body.angular_momentum), [0, 0, 0])
        self.assert_momentum(d, [2, 0, 0])

    def test_off_center_particle_hit_transfers_rigid_torque(self):
        s, d, _, _, _ = self.solve(definition([fluid([-.146, .15, 0], [2, 0, 0]),
            rigid(shape={"type": "box", "size": [.2, .5, .4]})], duration=.003))
        self.assertLess(s.rigid[0].body.angular_momentum.z, -.01)
        self.assertGreater(s.rigid[0].body.velocity.x, .1)
        self.assertLess(s.particles.contents.vx[0], 1.9)
        self.assert_momentum(d, [2, 0, 0])

    def test_mesh_receives_particle_impulse_and_total_momentum_is_preserved(self):
        soft = {"id": "soft", "type": "mesh", "position": [0, 0, 0], "velocity": [0, 0, 0],
            "motion": "soft", "mass": 3., "pinned_vertices": [], "damping": 0., "friction": 0., "thickness": .01,
            "mesh": {"vertices": [[-1, -1, 0], [1, -1, 0], [0, 1, 0]], "triangles": [[0, 1, 2]]}}
        s, d, _, md, _ = self.solve(definition([fluid([0, -.3333333333333, -.06], [0, 0, 2]), soft]))
        self.assert_momentum(d, [0, 0, 2], tolerance=2e-8)
        self.assertGreater(d.contact_count, 0)
        self.assertGreater(md.contact_count, 0)
        self.assertGreater(sum(s.mesh.contents.velocities[3*i+2] for i in range(3)), .1)
        self.assertLess(s.particles.contents.vz[0], 1.9)

    def test_mesh_spring_attachment_reaction_is_not_one_way(self):
        soft = {"id": "soft", "type": "mesh", "position": [0, 0, 0], "velocity": [0, 0, 0],
            "motion": "soft", "mass": 3., "pinned_vertices": [], "damping": 0., "friction": 0., "thickness": .01,
            "mesh": {"vertices": [[0, 0, 0], [0, 1, 0], [0, 0, 1]], "triangles": [[0, 1, 2]]}}
        link = {"id": "spring", "type": "spring", "endpoints": [{"entity": "node"}, {"entity": "soft", "vertex": 0}],
                "rest_length": 1., "stiffness": 10., "damping": 0.}
        s, d, _, _, _ = self.solve(definition([point("node", [1.2, 0, 0], 2.), soft], links=[link]))
        self.assertLess(s.point[0].velocity.x, 0)
        self.assertGreater(sum(s.mesh.contents.velocities[3*i] for i in range(3)), 0)
        self.assert_momentum(d, [0, 0, 0], tolerance=2e-8)

    def test_solid_spring_collides_and_massless_line_does_not(self):
        def run(solid):
            link = {"id": "spring", "type": "spring", "entities": ["left", "right"],
                    "rest_length": 1., "stiffness": 5., "damping": 0.}
            if solid:
                link["solid"] = {"radius": .03, "mass": 2.}
            return self.solve(definition([fluid([0, -.07, 0], [0, 2, 0]),
                point("left", [-.5, 0, 0]), point("right", [.5, 0, 0])], links=[link]))
        ideal, ideal_d, _, _, _ = run(False)
        solid, solid_d, _, _, metadata = run(True)
        self.assertEqual(ideal_d.contact_count, 0)
        self.assertAlmostEqual(ideal.particles.contents.vy[0], 2, places=11)
        self.assertEqual(ideal.point[0].velocity.y, 0)
        self.assertGreater(solid_d.contact_count, 0)
        self.assertGreater(solid.point[0].velocity.y, .1)
        self.assertLess(solid.particles.contents.vy[0], 1)
        self.assertEqual(metadata["point_effective_masses"], {"left": 2., "right": 2.})
        self.assert_momentum(solid_d, [0, 2, 0])
        self.assertAlmostEqual(solid.point[0].velocity.y, .4, places=10)
        self.assertAlmostEqual(solid.particles.contents.vy[0], .4, places=10)

    def test_off_center_spring_generates_rigid_torque_and_support_reaction(self):
        link = {"id": "spring", "type": "spring", "endpoints": [{"entity": "anchor"},
            {"entity": "body", "local_point": [.1, 0, 0]}], "rest_length": .7, "stiffness": 10., "damping": 0.}
        s, d, _, _, _ = self.solve(definition([point("anchor", [0, 1, 0], fixed=True),
            rigid(shape={"type": "box", "size": [.2, .2, .2]})], links=[link]))
        self.assertGreater(s.rigid[0].body.angular_momentum.z, 1e-3)
        self.assertGreater(s.rigid[0].body.velocity.y, 0)
        for p, support in zip(vector(d.final_momentum), vector(d.support_impulse)):
            self.assertAlmostEqual(p, support, places=11)

    def test_free_gyro_world_momentum_energy_and_quaternion_are_conserved(self):
        queries = [series("energy", "rotational_energy", entity="body")]
        queries += [series(axis, "angular_momentum", entity="body", axis=axis) for axis in "xyz"]
        s, d, _, _, _ = self.solve(definition([rigid(mass=6, shape={"type": "box", "size": [1, 2, 3]},
            omega=[1, 2, 3])], duration=1., dt=.01, queries=queries))
        initial = [24.5, 6.5, 10., 7.5]
        for i in range(d.observations_written):
            for j, exact in enumerate(initial):
                self.assertAlmostEqual(s.observation_values[4*i+j], exact, delta=2e-10)
        self.assertLess(d.max_quaternion_error, 1e-14)
        self.assertLess(abs(sum(x*x for x in [s.rigid[0].body.orientation.w, s.rigid[0].body.orientation.x,
            s.rigid[0].body.orientation.y, s.rigid[0].body.orientation.z])-1), 1e-14)
        self.assert_momentum(d, [0, 0, 0])

    def test_sphere_floor_friction_uses_surface_lever_arm(self):
        s, d, _, _, _ = self.solve(definition([rigid(p=[0, .1005, 0], v=[1, -1, 0], mass=2,
            shape={"type": "sphere", "radius": .1})], duration=.001, dt=.001, substeps=1,
            colliders=[{"type": "plane", "normal": [0, 1, 0], "offset": 0, "friction": 1.}]))
        r = s.rigid[0].body
        self.assertAlmostEqual(r.velocity.x, 5/7, places=10)
        self.assertAlmostEqual(r.velocity.y, 0, places=10)
        self.assertAlmostEqual(r.angular_momentum.z, -2/35, places=10)
        self.assertGreater(d.max_penetration_m, 0)
        self.assertAlmostEqual(r.velocity.x + .1*r.angular_momentum.z/.008, 0, places=10)

    def test_solid_capsule_lands_on_static_floor(self):
        link = {"id": "spring", "type": "spring", "entities": ["left", "right"],
                "rest_length": 1., "stiffness": 5., "damping": 0., "solid": {"radius": .03, "mass": 0.}}
        s, d, _, _, _ = self.solve(definition([point("left", [-.5, .035, 0], v=[0, -1, 0]),
            point("right", [.5, .035, 0], v=[0, -1, 0])], links=[link],
            colliders=[{"type": "plane", "normal": [0, 1, 0], "offset": 0, "friction": 0.}]))
        self.assertGreater(d.contact_count, 0)
        for i in range(2):
            self.assertGreaterEqual(s.point[i].position.y, .03-1e-7)
            self.assertGreater(s.point[i].velocity.y, -1e-4)
        for initial, final, support in zip(vector(d.initial_momentum), vector(d.final_momentum), vector(d.support_impulse)):
            self.assertAlmostEqual(final-initial, support, places=10)

    def test_solid_capsule_transfers_impulse_to_unattached_rigid(self):
        link = {"id": "spring", "type": "spring", "entities": ["left", "right"],
                "rest_length": 1., "stiffness": 5., "damping": 0., "solid": {"radius": .03, "mass": 0.}}
        s, d, _, _, _ = self.solve(definition([point("left", [-.5, .11, 0], v=[0, -1, 0]),
            point("right", [.5, .11, 0], v=[0, -1, 0]), rigid()], links=[link]))
        self.assertGreater(d.contact_count, 0)
        self.assertLess(s.rigid[0].body.velocity.y, -.05)
        self.assertGreater(s.point[0].velocity.y, -.95)
        self.assert_momentum(d, [0, -2, 0])

    def test_solid_capsule_does_not_collide_with_its_attached_rigid(self):
        link = {"id": "spring", "type": "spring", "endpoints": [{"entity": "anchor"}, {"entity": "body"}],
                "rest_length": 1., "stiffness": 1., "damping": 0., "solid": {"radius": .1, "mass": 0.}}
        s, d, _, _, _ = self.solve(definition([point("anchor", [1, 0, 0], fixed=True),
            rigid(shape={"type": "sphere", "radius": .2})], links=[link]))
        self.assertEqual(d.contact_count, 0)
        self.assertEqual(vector(s.rigid[0].body.position), [0, 0, 0])

    @staticmethod
    def triangle(motion="static", friction=0.):
        return {"id": "surface", "type": "mesh", "position": [0, 0, 0], "velocity": [0, 0, 0],
            "motion": motion, "mass": 3., "pinned_vertices": [], "damping": 0., "friction": friction, "thickness": .01,
            "mesh": {"vertices": [[-2, 0, -2], [2, 0, -2], [0, 0, 2]], "triangles": [[0, 2, 1]]}}

    def test_rigid_sphere_contacts_large_triangle_interior_with_friction_torque(self):
        body = rigid(p=[0, .1105, 0], v=[1, -1, 0], mass=2., shape={"type": "sphere", "radius": .1})
        body["friction"] = 1.
        s, d, _, _, _ = self.solve(definition([body, self.triangle(friction=1.)], duration=.001, dt=.001, substeps=1))
        r = s.rigid[0].body
        self.assertGreater(d.contact_count, 0)
        self.assertAlmostEqual(r.velocity.x, 5/7, places=10)
        self.assertAlmostEqual(r.velocity.y, 0, places=10)
        self.assertAlmostEqual(r.angular_momentum.z, -2/35, places=10)
        self.assertGreaterEqual(r.position.y, .11-1e-10)

    def test_rigid_box_and_cylinder_samples_land_on_large_triangle(self):
        for shape in ({"type": "box", "size": [.2, .2, .2]}, {"type": "cylinder", "radius": .1, "height": .2}):
            with self.subTest(shape=shape["type"]):
                s, d, _, _, _ = self.solve(definition([rigid(p=[0, .115, 0], v=[0, -1, 0], shape=shape), self.triangle()]))
                self.assertGreater(d.contact_count, 0)
                self.assertGreater(s.rigid[0].body.position.y, .105)
                self.assertGreater(s.rigid[0].body.velocity.y, -.2)

    def test_rigid_sphere_dynamic_triangle_reaction_preserves_momentum(self):
        s, d, _, _, _ = self.solve(definition([rigid(p=[0, .06, -2/3], v=[0, -2, 0], mass=1.,
            shape={"type": "sphere", "radius": .045}), self.triangle(motion="soft")]))
        self.assertGreater(d.contact_count, 0)
        self.assert_momentum(d, [0, -2, 0], tolerance=2e-8)
        self.assertAlmostEqual(s.rigid[0].body.velocity.y, -.5, places=8)
        for i in range(3):
            self.assertAlmostEqual(s.mesh.contents.velocities[3*i+1], -.5, places=8)

    def test_native_stiffness_resolution_and_same_rigid_links_are_rejected(self):
        scene, plan = definition([point("anchor", [1, 0, 0], fixed=True), rigid()], links=[
            {"id": "spring", "type": "spring", "endpoints": [{"entity": "anchor"}, {"entity": "body"}],
             "rest_length": 1., "stiffness": 1e7, "damping": 0.}])
        s, _ = bridge._pack(scene, plan, time.monotonic()+20)
        d, pd, md = bridge.Diagnostics(), CDiagnostics(), bridge.mesh.Diagnostics()
        self.assertEqual(self.library.coupled_simulate(C.byref(s), C.byref(d), C.byref(pd), C.byref(md)), 1)
        s.link[0].stiffness = 1
        s.link[0].a = bridge.Endpoint(2, 0, bridge.Vec3(-.1, 0, 0))
        s.link[0].b = bridge.Endpoint(2, 0, bridge.Vec3(.1, 0, 0))
        self.assertEqual(self.library.coupled_simulate(C.byref(s), C.byref(d), C.byref(pd), C.byref(md)), 1)

    def test_point_uniform_radial_vortex_fields_match_existing_native_verlet(self):
        from tests.test_particle_step import fixture as particle_fixture
        native, _, _ = particle_library(20)
        for field in ({"type": "uniform", "acceleration": [.4, .1, -.3]},
                      {"type": "radial", "center": [0, 0, 0], "strength": -2., "radius": 2.},
                      {"type": "vortex", "center": [0, 0, 0], "axis": [0, 1, 0],
                       "strength": 3., "inward_strength": .2, "radius": 2.}):
            with self.subTest(field=field["type"]):
                scene, plan = definition([point("node", [.2, .3, .4], v=[.1, -.1, .05])],
                                         duration=.01, dt=.001, substeps=1)
                scene["force_fields"] = [{"id": "field", "targets": ["node"], "start_time": .002,
                                           "end_time": .008, **field}]
                s, _, _, _, _ = self.solve((scene, plan))
                original = particle_fixture(count=0, duration=.01, dt=.001)
                for axis, name in enumerate("xyz"):
                    getattr(original, "body_"+name)[0] = scene["entities"][0]["position"][axis]
                    getattr(original, "body_v"+name)[0] = scene["entities"][0]["velocity"][axis]
                original.field_count = s.fields
                original.fields = s.field
                original.body_field_mask = (C.c_uint32 * 1)(1)
                diagnostic = CDiagnostics()
                self.assertEqual(native.phy_simulate(C.byref(original), C.byref(diagnostic)), 0)
                for axis in "xyz":
                    self.assertAlmostEqual(getattr(s.point[0].position, axis), getattr(original, "body_"+axis)[0], places=13)
                    self.assertAlmostEqual(getattr(s.point[0].velocity, axis), getattr(original, "body_v"+axis)[0], places=13)
    def test_raw_native_argument_rejections_under_sanitizers(self):
        directory = Path(bridge.__file__).with_name("native")
        source = Path(__file__).with_name("coupled_native_safety.c")
        with tempfile.TemporaryDirectory(prefix="coupled-native-safety-") as work:
            executable = Path(work)/"safety"
            subprocess.run([_compiler(), "-std=c11", "-Wall", "-Wextra", "-Werror", "-O1", "-g",
                "-fsanitize=address,undefined,float-cast-overflow", "-fno-omit-frame-pointer", "-pthread",
                str(source), *(str(directory/name) for name in ("coupled_native.c", "physics_native.c", "mesh_native.c")),
                "-lm", "-o", str(executable)], check=True, capture_output=True, timeout=30)
            result = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
