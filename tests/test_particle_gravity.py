"""Independent force oracle and public self-gravity integration contract."""
import copy
import ctypes as C
import json
import math
from pathlib import Path
import random
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from physics_demo.core.backend import run_scene
from physics_demo.core.native_backend import NativeBackendUnavailable
from physics_demo.runner import prepare, validate


def scene():
    return {
        "version": 1, "name": "two-gravitating-volumes",
        "world": {"duration": .1, "dt": .002, "output_fps": 20,
                  "gravity": [0, 0, 0], "bounds": {"min": [-3]*3, "max": [3]*3}},
        "budget": {"quality": "preview", "backend": "native", "validation": "visual"},
        "entities": [{"id": name, "type": "granular", "spacing": .1,
                      "shape": {"type": "box", "center": [x, 0, 0], "size": [.05]*3},
                      "properties": {"friction": 0, "cohesion": 0}}
                     for name, x in [("left", -1), ("right", 1)]],
        "interactions": {"mutual_gravity": True, "gravity_G": 1,
                         "softening": .05, "particle_gravity_density": 1000},
    }


class ParticleGravityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parents[1]
        wrapper = Path(cls.temp.name)/"force.c"
        wrapper.write_text('''#include "particle_gravity.h"
int force(int n, const double *x, const double *y, const double *z,
          double theta, double eps, double gm, double *out) {
    PGTree t = {0};
    if (theta > 0) {
        if (!pg_init(&t,n)) { pg_destroy(&t); return -1; }
        pg_build(&t,x,y,z);
    } else pg_bind_positions(&t,n,x,y,z);
    for(int i=0;i<n;++i) pg_acceleration(&t,i,theta,eps,gm,out+3*i);
    int nodes=t.used; pg_destroy(&t); return nodes;
}
''')
        library = Path(cls.temp.name)/"forces.so"
        subprocess.run(["cc", "-O3", "-std=c11", "-shared", "-fPIC", str(wrapper),
                        "-I", str(root/"physics_demo/core/native"), "-o", str(library)],
                       check=True, capture_output=True)
        cls.library = C.CDLL(str(library))
        cls.force = cls.library.force
        cls.force.argtypes = [C.c_int, *([C.POINTER(C.c_double)]*3),
                              C.c_double, C.c_double, C.c_double, C.POINTER(C.c_double)]
        cls.force.restype = C.c_int

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def forces(self, positions, theta=.5, eps=.03, gm=.7):
        n = len(positions)
        arrays = [(C.c_double*n)(*(p[a] for p in positions)) for a in range(3)]
        out = (C.c_double*(3*n))()
        nodes = self.force(n, *arrays, theta, eps, gm, out)
        if theta == 0:
            self.assertEqual(nodes, 0)
        else:
            self.assertGreater(nodes, 0)
        self.assertLessEqual(nodes, 2*n-1)
        return [list(out[3*i:3*i+3]) for i in range(n)]

    def test_direct_matches_independent_python_oracle(self):
        randomizer = random.Random(193)
        points = [[randomizer.uniform(-1, 1) for _ in range(3)] for _ in range(41)]
        actual = self.forces(points, 0)
        for i, point in enumerate(points):
            expected = [0., 0., 0.]
            for j, other in enumerate(points):
                if i == j: continue
                delta = [other[a]-point[a] for a in range(3)]
                scale = .7/(sum(v*v for v in delta)+.03**2)**1.5
                expected = [expected[a]+scale*delta[a] for a in range(3)]
            for a in range(3): self.assertAlmostEqual(actual[i][a], expected[a], places=10)

    def test_tree_error_and_tighter_opening_angle(self):
        rng = random.Random(719)
        points = [[rng.gauss(0, .15)+(-2 if i < 320 else 2),
                   rng.gauss(0, .25), rng.gauss(0, .25)] for i in range(640)]
        direct = self.forces(points, 0)
        def error(theta):
            tree = self.forces(points, theta)
            return math.sqrt(sum((a-b)**2 for av,bv in zip(tree,direct) for a,b in zip(av,bv)) /
                             sum(a*a for av in direct for a in av))
        coarse, fine = error(.5), error(.25)
        self.assertLess(coarse, .02)
        self.assertLess(fine, coarse)

    def test_coincident_and_line_positions_are_bounded(self):
        self.assertEqual(self.forces([[1., 2., 3.]]*64), [[0., 0., 0.]]*64)
        self.assertEqual(self.forces([[0., 0., 0.]]), [[0., 0., 0.]])
        force = self.forces([[i*1e-7, 0., 0.] for i in range(60)])
        self.assertTrue(all(math.isfinite(v) for row in force for v in row))

    def test_far_leaf_never_includes_self_force(self):
        points = [[0., 0., 0.], [10., 0., 0.]]
        direct = self.forces(points, 0)
        self.assertEqual(direct, self.forces(points, .7))
        self.assertAlmostEqual(direct[0][0], -direct[1][0])

    def test_enabled_gravity_moves_two_volumes_and_preserves_centroid(self):
        s=scene(); p=prepare(s); self.assertTrue(p['ready_to_simulate'],p)
        result=run_scene(p['scene'],p['plan'],time.monotonic()+10)
        end=result['frames'][-1]['p']
        self.assertLess(end[1][0]-end[0][0], 1.999)
        self.assertAlmostEqual(end[0][0]+end[1][0],0,places=7)
        self.assertEqual(result['diagnostics']['particle_gravity']['algorithm'],'Barnes-Hut')
        s['interactions']['mutual_gravity']=False
        p=prepare(s); off=run_scene(p['scene'],p['plan'],time.monotonic()+10)
        self.assertEqual(off['frames'][-1]['p'],[[-1.,0.,0.],[1.,0.,0.]])

    def test_strict_defaults_to_direct_and_reports_mass(self):
        s=scene();s['budget']['validation']='strict';p=prepare(s)
        self.assertTrue(p['ready_to_simulate'],p)
        g=p['agent_report']['particle_gravity']
        self.assertEqual(g['algorithm'],'direct')
        self.assertAlmostEqual(g['represented_mass_kg'],2.)
        self.assertIn('particle_gravity_p50_s',p['plan']['timing_estimate'])

    def test_direct_solver_matches_exact_two_particle_tree(self):
        frames = []
        for theta in (0, .5):
            s = scene()
            s['interactions']['gravity_theta'] = theta
            p = prepare(s)
            self.assertTrue(p['ready_to_simulate'], p)
            result = run_scene(p['scene'], p['plan'], time.monotonic() + 10)
            self.assertEqual(result['diagnostics']['particle_gravity']['algorithm'],
                             'direct' if theta == 0 else 'Barnes-Hut')
            frames.append(result['frames'])
        self.assertEqual(frames[0], frames[1])

    def test_invalid_controls_and_unsupported_routes_are_explicit(self):
        for field,value in [('gravity_theta',.71),('gravity_theta',-1),
                            ('gravity_theta',float('nan')),('gravity_theta',True),
                            ('particle_gravity_density',0),('particle_gravity_density',30001),
                            ('softening',0)]:
            s=scene();s['interactions'][field]=value
            self.assertFalse(validate(s)['ok'],(field,value))
        s=scene();s['budget']['backend']='python'
        self.assertFalse(prepare(s)['ok'])
        s=scene();s['entities'].append({'id':'point','type':'point_mass','mass':1,'position':[0,2,0]})
        self.assertIn('particle_gravity_route',{e['code'] for e in prepare(s)['errors']})

    def test_missing_native_never_drops_particle_gravity(self):
        s=scene();s['budget']['backend']='auto';p=prepare(s)
        with patch('physics_demo.core.backend.run_scene_native',side_effect=NativeBackendUnavailable('test')):
            with self.assertRaises(NativeBackendUnavailable):
                run_scene(p['scene'],p['plan'],time.monotonic()+10)


if __name__ == '__main__': unittest.main()
