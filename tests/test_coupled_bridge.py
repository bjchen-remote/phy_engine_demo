"""Independent C-layout and mixed-world packing checks, without solving."""
from __future__ import annotations

import copy
import ctypes as C
import gc
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from physics_demo import coupled_solver as bridge
from physics_demo import native_runtime
from physics_demo.native_backend import CDiagnostics, _compiler


def fixture():
    point = lambda ident, position, mass: {"id": ident, "type": "point_mass", "position": position,
        "velocity": [.1, 0, 0], "mass": mass, "fixed": False, "collision_radius": .1}
    scene = {"world": {"duration": .02, "dt": .01, "output_fps": 10, "gravity": [0, -9.81, 0],
                        "bounds": {"min": [-10, -10, -10], "max": [10, 10, 10]}},
        "coupling": {"substeps": 4, "iterations": 3, "friction": .2},
        "interactions": {"mutual_gravity": False, "gravity_G": 1., "softening": .01,
                         "water_sand_drag": .2, "wetting_rate": .1},
        "entities": [
            {"id": "water", "type": "fluid", "shape": {"type": "sphere", "center": [0, 2, 0], "radius": .2},
             "velocity": [0, 0, 0], "properties": {"viscosity": .01, "surface_tension": .02}},
            point("a", [2, 1, 0], 2),
            {"id": "soft", "type": "mesh", "position": [0, 1, 0], "velocity": [0, 0, 0],
             "mesh": {"vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0]], "triangles": [[0, 1, 2]]},
             "motion": "soft", "mass": 3, "pinned_vertices": [0], "damping": 0},
            {"id": "spin", "type": "rigid_body", "shape": {"type": "box", "size": [1, 2, 3]},
             "position": [3, 2, 0], "velocity": [0, 0, 0], "orientation": [1, 0, 0, 0],
             "angular_velocity": [1, 2, 3], "mass": 6, "fixed": False, "friction": .2, "restitution": 0},
            point("b", [3.2, 1, 0], 4)],
        "connections": [
            {"id": "massive", "type": "spring", "entities": ["a", "b"], "rest_length": 1,
             "stiffness": 10, "damping": .2, "solid": {"radius": .03, "mass": 2}},
            {"id": "attached", "type": "spring", "endpoints": [{"entity": "soft", "vertex": 1},
             {"entity": "spin", "local_point": [.5, 0, 0]}], "rest_length": 2, "stiffness": 5, "damping": 0}],
        "force_fields": [{"id": "push", "type": "uniform", "acceleration": [1, 0, 0],
                          "targets": ["water", "b"], "start_time": 0, "end_time": .02}],
        "colliders": [{"type": "plane", "normal": [0, 1, 0], "offset": 0, "friction": .2}],
        "queries": [
            {"id": "force", "type": "series", "metric": {"type": "spring_force", "connection": "massive"}},
            {"id": "angular", "type": "series", "metric": {"type": "angular_momentum", "entity": "spin", "axis": "z"}},
            {"id": "distance", "type": "series", "metric": {"type": "center_distance", "entities": ["soft", "b"]}}]}
    plan = {"effective_spacing": .1, "render_particle_limit": 4, "threads": 1,
            "density_iterations": 5, "divergence_iterations": 2, "max_substeps": 64,
            "coupling_substeps": 4, "coupling_iterations": 3, "mesh_substeps": 4, "mesh_iterations": 6}
    return scene, plan


class CoupledBridgeTests(unittest.TestCase):
    def test_ctypes_layout_matches_c_header_for_every_field(self):
        declarations = [(bridge.Vec3, "rm_vec3"), (bridge.Quaternion, "rm_quat"), (bridge.Body, "rm_body"),
            (bridge.Point, "CoupledPoint"), (bridge.Rigid, "CoupledRigid"), (bridge.Endpoint, "CoupledEndpoint"),
            (bridge.Link, "CoupledLink"), (bridge.Entity, "CoupledEntity"), (bridge.Metric, "CoupledMetric"),
            (bridge.Simulation, "CoupledSimulation"), (bridge.Diagnostics, "CoupledDiagnostics")]
        header = Path(bridge.__file__).with_name("native") / "coupled_native.h"
        lines = ['#include <stdio.h>', '#include <stddef.h>', f'#include "{header}"', 'int main(void) {']
        expected = []
        for kind, cname in declarations:
            lines.append(f'printf("%zu\\n", sizeof({cname}));')
            expected.append(C.sizeof(kind))
            for field, _ in kind._fields_:
                lines.append(f'printf("%zu\\n", offsetof({cname}, {field}));')
                expected.append(getattr(kind, field).offset)
        lines.append('return 0; }')
        with tempfile.TemporaryDirectory(prefix="coupled-layout-") as directory:
            source, executable = Path(directory)/"layout.c", Path(directory)/"layout"
            source.write_text("\n".join(lines))
            subprocess.run([_compiler(), "-std=c11", str(source), "-o", str(executable)],
                           check=True, capture_output=True, timeout=20)
            actual = subprocess.check_output([str(executable)], text=True, timeout=5)
        self.assertEqual([int(value) for value in actual.splitlines()], expected)

    def test_mixed_pack_maps_mass_endpoints_queries_fields_and_ownership(self):
        scene, plan = fixture()
        original = copy.deepcopy(scene)
        simulation, metadata = bridge._pack(scene, plan, time.monotonic()+20)
        gc.collect()
        self.assertEqual(scene, original)
        self.assertEqual((simulation.points, simulation.rigids, simulation.links, simulation.entities), (2, 1, 2, 5))
        self.assertEqual(metadata["point_effective_masses"], {"a": 3., "b": 5.})
        self.assertAlmostEqual(simulation.point[0].inverse_mass, 1/3)
        self.assertAlmostEqual(simulation.point[1].inverse_mass, 1/5)
        self.assertEqual((simulation.point[0].field_mask, simulation.point[1].field_mask), (0, 1))
        self.assertEqual((simulation.link[1].a.kind, simulation.link[1].a.index), (1, 1))
        self.assertEqual((simulation.link[1].b.kind, simulation.link[1].b.index), (2, 0))
        self.assertEqual(simulation.link[1].b.local_point.x, .5)
        self.assertEqual([simulation.entity[i].kind for i in range(5)], [0, 1, 2, 3, 1])
        self.assertEqual((simulation.metric[0].type, simulation.metric[0].a), (9, 0))
        self.assertEqual((simulation.metric[1].type, simulation.metric[1].a, simulation.metric[1].axis0), (12, 3, 2))
        self.assertEqual((simulation.metric[2].a, simulation.metric[2].b), (2, 4))
        p, m = simulation.particles.contents, simulation.mesh.contents
        self.assertGreater(p.particle_count, 4)
        self.assertEqual((p.render_count, p.body_count, p.observation_metric_count, p.max_substeps), (4, 0, 0, 32))
        self.assertEqual(p.particle_field_mask[0], 1)
        self.assertEqual((m.vertices, m.metrics, m.inverse_mass[0]), (3, 0, 0))
        self.assertAlmostEqual(m.inverse_mass[1], 1.)
        self.assertEqual((simulation.rigid[0].body.angular_momentum.x,
                          simulation.rigid[0].body.angular_momentum.y,
                          simulation.rigid[0].body.angular_momentum.z), (6.5, 10., 7.5))
        self.assertEqual(metadata["rigid_ids"], ["spin"])
        self.assertEqual(metadata["gravity_body_ids"], ["a", "b"])
        self.assertTrue(all(bool(getattr(simulation, name)) for name in
                            ("point", "rigid", "link", "entity", "metric", "field", "collider",
                             "point_frames", "rigid_frames", "frame_times", "observation_times", "observation_values")))

    def test_absent_domains_use_null_contexts_but_nonnull_dummy_buffers(self):
        scene, plan = fixture()
        scene.update(entities=[scene["entities"][3]], connections=[], queries=[], force_fields=[], colliders=[])
        simulation, metadata = bridge._pack(scene, plan, time.monotonic()+20)
        self.assertFalse(simulation.particles)
        self.assertFalse(simulation.mesh)
        self.assertEqual(simulation.points, 0)
        self.assertEqual(metadata["particle_radius"], 0)
        for name in ("point", "link", "metric", "field", "collider", "point_frames", "observation_times", "observation_values"):
            self.assertTrue(getattr(simulation, name), name)

    def test_rotated_rigid_and_pivot_inertia_are_world_momentum(self):
        scene, _ = fixture()
        body = scene["entities"][3]
        body["orientation"] = [math.sqrt(.5), 0, 0, math.sqrt(.5)]
        body["angular_velocity"] = [1, 0, 0]
        packed = bridge._pack_rigid(body)
        self.assertAlmostEqual(packed.body.angular_momentum.x, 5, places=12)
        self.assertAlmostEqual(packed.body.angular_momentum.y, 0, places=12)
        body["pivot"] = {"point": [0, 0, 0], "local_point": [0, -1, 0]}
        packed = bridge._pack_rigid(body)
        self.assertEqual(packed.body.inv_mass, 0)
        self.assertEqual(packed.body.inertia_body_diag.x, 12.5)
        self.assertEqual(packed.body.inertia_body_diag.z, 8.5)

    def test_unpacked_frame_channels_and_nested_diagnostics_follow_header(self):
        scene, plan = fixture()
        s, metadata = bridge._pack(scene, plan, time.monotonic()+20)
        s.frame_times[0] = 0
        for i, value in enumerate([1, 2, 3, 1, 0, 0, 0]):
            s.rigid_frames[i] = value
        s.point_frames[0] = 7
        s.particles.contents.frame_particles[0] = 8
        s.mesh.contents.frames[0] = 9
        s.observation_times[0] = 0
        for i in range(3):
            s.observation_values[i] = 10+i
        diag = bridge.Diagnostics(status=0, completed=1, finite=1, frames_written=1, observations_written=1)
        particle = CDiagnostics(finite=1, completed=0, threads_used=1, final_mean_water_density_ratio=.9)
        mesh = bridge.mesh.Diagnostics(finite=1, completed=0, min_volume_ratio=1, max_volume_ratio=1)
        result = bridge._unpack(scene, s, metadata, diag, particle, mesh, 0, .2, "build", time.monotonic())
        self.assertEqual(result["frames"][0]["r"], [[1, 2, 3]])
        self.assertEqual(result["frames"][0]["q"], [[1, 0, 0, 0]])
        self.assertEqual([result["frames"][0][name][0][0] for name in ("g", "p", "m")], [7, 8, 9])
        self.assertEqual(result["observations"]["columns"], {"force": [10], "angular": [11], "distance": [12]})
        self.assertTrue(result["diagnostics"]["completed"])
        self.assertEqual(result["diagnostics"]["particle_diagnostics"]["final_mean_water_density_ratio"], .9)
        self.assertEqual(result["diagnostics"]["mesh_diagnostics"]["min_volume_ratio"], 1)
        json.dumps(result, allow_nan=False)

    def test_default_runtime_cache_identity_is_preserved_and_dependencies_invalidate(self):
        with tempfile.TemporaryDirectory(prefix="coupled-cache-test-") as directory:
            folder = Path(directory)
            source, extra, header = folder/"main.c", folder/"extra.c", folder/"math.h"
            for path, contents in ((source, "main source"), (source.with_suffix(".h"), "main header"),
                                   (extra, "extra source"), (extra.with_suffix(".h"), "extra header"),
                                   (header, "math header")):
                path.write_text(contents)
            calls = []
            def run(args, **kwargs):
                calls.append(args)
                if args[-1] == "--version":
                    return subprocess.CompletedProcess(args, 0, stdout=b"test compiler", stderr=b"")
                Path(args[args.index("-o")+1]).write_bytes(b"fake dylib")
                return subprocess.CompletedProcess(args, 0, stdout=b"", stderr=b"")
            with patch.object(native_runtime, "_compiler", return_value="cc"), \
                 patch.object(native_runtime.subprocess, "run", side_effect=run), \
                 patch.object(native_runtime.C, "CDLL", return_value=object()), \
                 patch.object(native_runtime.tempfile, "gettempdir", return_value=directory):
                arguments = dict(prefix="libtest-", name="test", deadline=time.monotonic()+20,
                                 libraries={}, bind=lambda library: None)
                _, _, legacy = native_runtime.load_library(source, **arguments)
                flags = calls[1][1:calls[1].index(str(source))]
                expected = hashlib.sha256(source.read_bytes()+source.with_suffix(".h").read_bytes()
                                          +b"test compiler"+"\0".join(flags).encode()).hexdigest()[:20]
                self.assertEqual(legacy, expected)
                _, _, combined = native_runtime.load_library(source, **arguments, extra_sources=(extra,),
                                                             dependencies=(header,), pthread=True)
                self.assertIn("-pthread", calls[-1])
                self.assertIn(str(extra), calls[-1])
                header.write_text("changed math header")
                _, _, changed = native_runtime.load_library(source, **arguments, extra_sources=(extra,),
                                                            dependencies=(header,), pthread=True)
                self.assertNotEqual(combined, changed)


if __name__ == "__main__":
    unittest.main()
