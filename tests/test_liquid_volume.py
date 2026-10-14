"""Mesh-volume conversion must preserve source identity across prepare/run/inspect."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from physics_demo.core.engine import _make_state
from physics_demo.core.meshes import build_mesh
from physics_demo.core.native_backend import NativeBackendUnavailable
from physics_demo.core.volume_sampling import sample_mesh_volume
from physics_demo.io.planning import make_plan
from physics_demo.runner import prepare, simulate, inspect, validate


def scene(*, spacing=.02, size=(.12, .12, .12), mode='strict'):
    return {
        'version': 1, 'name': 'qualified-water-volume',
        'world': {'gravity': [0, -9.81, 0], 'duration': .02, 'dt': .001,
                  'output_fps': 10, 'bounds': {'min': [-1, -1, -1], 'max': [1, 2, 1]}},
        'budget': {'quality': 'preview', 'wall_time_s': 30, 'backend': 'auto', 'validation': mode},
        'entities': [{'id': 'water', 'type': 'fluid', 'preset': 'water', 'spacing': spacing,
                      'velocity': [0, 0, 0],
                      'shape': {'type': 'mesh_volume', 'mesh': build_mesh({'type': 'box', 'size': list(size)}),
                                'center': [0, .5, 0], 'units': 'm', 'conversion': 'instant',
                                'conservation': 'volume', 'reference_density_kg_m3': 1000}}],
        'colliders': [],
    }


class LiquidVolumeTests(unittest.TestCase):
    def test_plan_and_state_reuse_exact_deterministic_particles(self):
        prepared = prepare(scene())
        self.assertTrue(prepared['ok'], prepared)
        normalized, plan = prepared['scene'], prepared['plan']
        self.assertEqual(plan, make_plan(normalized))
        self.assertEqual(plan['backend'], 'native')
        self.assertEqual(plan['planned_particles'], 216)
        samples = sample_mesh_volume(normalized['entities'][0]['shape'], plan['effective_spacing'])
        particles = _make_state(normalized, plan['effective_spacing'])[0]
        self.assertEqual([p.pos for p in particles], samples['points'])
        self.assertEqual(len(particles), plan['planned_particles'])
        receipt = plan['liquid_volume_receipts']['water']
        self.assertEqual(receipt, samples['receipt'])
        self.assertAlmostEqual(receipt['represented_mass_kg'], 1.728)
        self.assertLessEqual(receipt['relative_volume_error'], .05)
        self.assertTrue(all(p.group == 'water' for p in particles))

    def test_cap_coarsening_resamples_original_geometry(self):
        source = scene(size=(.4, .4, .4), spacing=.01)
        original = copy.deepcopy(source['entities'][0]['shape']['mesh']['vertices'])
        prepared = prepare(source)
        self.assertTrue(prepared['ok'], prepared)
        plan, normalized = prepared['plan'], prepared['scene']
        self.assertGreater(plan['effective_spacing'], .01)
        self.assertLessEqual(plan['planned_particles'], 2000)
        self.assertEqual(normalized['entities'][0]['shape']['mesh']['vertices'], original)
        self.assertEqual(len(_make_state(normalized, plan['effective_spacing'])[0]), plan['planned_particles'])
        self.assertLessEqual(plan['liquid_volume_receipts']['water']['relative_volume_error'], .05)

    def test_thin_source_rejects_cap_without_truncation_even_visual(self):
        result = prepare(scene(size=(.5, .5, .0001), spacing=.0001, mode='visual'))
        self.assertFalse(result['ok'], result)
        self.assertFalse(result['ready_to_simulate'])
        self.assertEqual(result['errors'][0]['code'], 'mesh_volume_sampling_budget')
        self.assertFalse(result['plan']['liquid_volume_fits_limits'])
        self.assertEqual(result['plan']['planned_particles'], 0)

    def test_prepare_resamples_missing_appendage_and_binds_coverage_to_state(self):
        source = scene(spacing=.01, mode='visual')
        source['budget']['quality'] = 'high'
        contour = [[-.1, -.1], [.1, -.1], [.1, -.003], [.2, -.003],
                   [.2, .003], [.1, .003], [.1, .1], [-.1, .1]]
        source['entities'][0]['shape']['mesh'] = build_mesh(
            {'type': 'extrusion', 'contour': contour, 'depth': .1})
        prepared = prepare(source)
        self.assertTrue(prepared['ok'], prepared)
        plan = prepared['plan']
        self.assertGreater(plan['effective_spacing'], .01)
        sampled = sample_mesh_volume(prepared['scene']['entities'][0]['shape'], plan['effective_spacing'])
        self.assertTrue(any(point[0] > .1 for point in sampled['points']))
        self.assertEqual(sampled['receipt'], plan['liquid_volume_receipts']['water'])
        self.assertEqual(sampled['receipt']['source_surface_coverage']['status'], 'passed')
        particles = _make_state(prepared['scene'], plan['effective_spacing'])[0]
        self.assertEqual([p.pos for p in particles], sampled['points'])
        self.assertEqual(len(particles), plan['planned_particles'])

    def test_coupled_budget_and_native_initial_particle_contract(self):
        capped = scene(size=(.4, .4, .4), spacing=.01)
        capped['budget']['quality'] = 'high'
        capped['coupling'] = {}
        planned = prepare(capped)
        self.assertTrue(planned['ok'], planned)
        self.assertEqual(planned['plan']['backend'], 'coupled')
        self.assertLessEqual(planned['plan']['planned_particles'], 4096)
        source = scene()
        source['coupling'] = {}
        with tempfile.TemporaryDirectory(prefix='liquid-coupled-', dir='/tmp') as folder:
            result = simulate(source, folder, 30, make_video=False)
            self.assertTrue(result['ok'], result)
            checked = inspect(folder)
            self.assertTrue(checked['ok'], checked)
            self.assertTrue(checked['quality_gate']['numerical_checks']['liquid_volume_initial_state_matches_source'])

    def test_coupled_rotated_rigid_clearance_rejects_near_and_accepts_far(self):
        for shape, orientation, center in (
            ({'type': 'sphere', 'radius': .1}, [1, 0, 0, 0], [0, .5, 0]),
            ({'type': 'box', 'size': [.4, .01, .01]}, [math.sqrt(.5), 0, math.sqrt(.5), 0], [0, .5, .15]),
            ({'type': 'cylinder', 'radius': .01, 'height': .4}, [math.sqrt(.5), 0, 0, math.sqrt(.5)], [.15, .5, 0]),
        ):
            with self.subTest(shape=shape):
                source = scene(mode='visual')
                source['entities'].append({'id': 'obstacle', 'type': 'rigid_body', 'shape': shape,
                                           'position': center, 'orientation': orientation, 'mass': 1,
                                           'fixed': True, 'velocity': [0, 0, 0]})
                rejected = prepare(source)
                self.assertFalse(rejected['ok'], rejected)
                self.assertEqual(rejected['errors'][0]['code'], 'mesh_volume_initial_contact')
                self.assertIn('conservative initial AABB', rejected['errors'][0]['message'])
                source['entities'][-1]['position'] = [.65, .5, .65]
                accepted = prepare(source)
                self.assertTrue(accepted['ok'], accepted)
                self.assertTrue(any('not exact triangle CCD' in a for a in accepted['assumptions']))

    def test_coupled_mesh_enclosing_source_rejects_and_far_mesh_prepares(self):
        source = scene(mode='visual')
        source['coupling'] = {}
        source['entities'].append({'id': 'shell', 'type': 'mesh', 'motion': 'static',
                                   'mesh': build_mesh({'type': 'box', 'size': [.3]*3}),
                                   'position': [0, .5, 0], 'mass': 1, 'thickness': .01,
                                   'velocity': [0, 0, 0]})
        rejected = prepare(source)
        self.assertFalse(rejected['ok'], rejected)
        self.assertEqual(rejected['errors'][0]['code'], 'mesh_volume_initial_contact')
        source['entities'][-1]['position'][0] = .65
        self.assertTrue(prepare(source)['ok'])

    def test_coupled_point_spheres_and_solid_links_have_initial_clearance(self):
        source = scene(mode='visual')
        source['coupling'] = {}
        source['entities'].append({'id': 'point', 'type': 'point_mass', 'position': [0, .5, 0],
                                   'velocity': [0, 0, 0], 'mass': 1, 'collision_radius': .02})
        rejected = prepare(source)
        self.assertEqual(rejected['errors'][0]['code'], 'mesh_volume_initial_contact')
        source['entities'][-1]['position'][0] = .65
        self.assertTrue(prepare(source)['ok'])
        source = scene(mode='visual')
        source['coupling'] = {}
        source['entities'] += [{'id': name, 'type': 'point_mass', 'position': [x, .5, 0],
                                'velocity': [0, 0, 0], 'mass': 1} for name, x in (('a', -.3), ('b', .3))]
        source['interactions'] = {'mutual_gravity': False}
        source['connections'] = [{'id': 'bar', 'type': 'rod', 'entities': ['a', 'b'],
                                  'rest_length': .6, 'solid': {'radius': .01}}]
        rejected = prepare(source)
        self.assertFalse(rejected['ok'], rejected)
        self.assertEqual(rejected['errors'][0]['code'], 'mesh_volume_initial_contact')
        self.assertIn('solid connection', rejected['errors'][0]['message'])
        for entity in source['entities'][1:]:
            entity['position'][1] = 1.2
        self.assertTrue(prepare(source)['ok'])

    def test_source_declarations_are_explicit_and_fixed(self):
        mutations = [('units', None), ('center', None), ('conversion', None),
                     ('conservation', None), ('reference_density_kg_m3', None),
                     ('units', 'model_unit'), ('conversion', 'melt'),
                     ('conservation', 'mass'), ('reference_density_kg_m3', 11340)]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                value_scene = scene()
                shape = value_scene['entities'][0]['shape']
                if value is None:
                    shape.pop(key)
                else:
                    shape[key] = value
                result = prepare(value_scene)
                self.assertFalse(result['ok'], result)
                self.assertFalse(result['ready_to_simulate'])

    def test_open_and_fake_qualified_metadata_are_rejected(self):
        source = scene()
        source['entities'][0]['shape']['mesh']['triangles'].pop()
        source['entities'][0]['shape']['mesh']['metadata'].update(closed=True, connected=True)
        result = prepare(source)
        self.assertFalse(result['ok'], result)
        self.assertIn('mesh_volume_geometry', {e['code'] for e in result['errors']})

    def test_nonwater_python_and_conflicting_gravity_density_are_rejected(self):
        for option in ('preset', 'backend', 'gravity'):
            with self.subTest(option=option):
                source = scene()
                if option == 'preset':
                    source['entities'][0]['preset'] = 'molten_lead'
                elif option == 'backend':
                    source['budget']['backend'] = 'python'
                else:
                    source['interactions'] = {'mutual_gravity': True, 'particle_gravity_density': 2000}
                self.assertFalse(prepare(source)['ok'])

    def test_source_asset_binding_requires_pair_of_hex_ids(self):
        source = scene()
        shape = source['entities'][0]['shape']
        shape['source_ref'] = 'a' * 64
        self.assertFalse(validate(source)['ok'])
        shape['receipt_sha256'] = 'b' * 64
        self.assertTrue(prepare(source)['ok'])
        shape['source_ref'] = '/tmp/anything'
        self.assertFalse(validate(source)['ok'])

    def test_actual_sample_bounds_and_cross_source_overlap_are_hard(self):
        source = scene()
        source['world']['bounds']['max'][1] = .55
        self.assertEqual(prepare(source)['errors'][0]['code'], 'initial_state_outside_bounds')
        source = scene()
        source['entities'].append(copy.deepcopy(source['entities'][0]))
        source['entities'][1]['id'] = 'overlapping'
        rejected = prepare(source)
        self.assertFalse(rejected['ok'])
        self.assertIn('overlap', rejected['errors'][0]['message'])

    def test_conversion_rejects_initial_collider_projection(self):
        source = scene(mode='visual')
        source['colliders'] = [{'id': 'inside', 'type': 'box', 'center': [0, .5, 0], 'size': [.02]*3}]
        prepared = prepare(source)
        self.assertFalse(prepared['ok'], prepared)
        self.assertEqual(prepared['errors'][0]['code'], 'mesh_volume_initial_contact')

    def test_auto_native_failure_never_runs_python(self):
        from physics_demo.core.backend import run_scene
        prepared = prepare(scene())
        with patch('physics_demo.core.backend.run_scene_native', side_effect=NativeBackendUnavailable('missing native')), patch('physics_demo.core.backend.run_scene_python') as python:
            with self.assertRaises(NativeBackendUnavailable):
                run_scene(prepared['scene'], prepared['plan'], time.monotonic()+5)
        python.assert_not_called()

    def test_short_native_scene_and_full_group_observations(self):
        source = scene()
        source['queries'] = [{'id': 'height', 'type': 'series', 'metric': {'type': 'centroid', 'entity': 'water', 'axis': 'y'}}]
        with tempfile.TemporaryDirectory(prefix='liquid-volume-', dir='/tmp') as folder:
            result = simulate(source, folder, 30, make_video=False)
            self.assertTrue(result['ok'], result)
            checked = inspect(folder)
            self.assertTrue(checked['ok'], checked)
            checks = checked['quality_gate']['numerical_checks']
            self.assertTrue(checks['liquid_volume_conversion_integrity'])
            self.assertTrue(checks['liquid_volume_inventory_matches_solver'])
            self.assertTrue(checks['liquid_volume_initial_state_matches_source'])
            self.assertEqual(result['diagnostics']['particle_count'], 216)
            self.assertTrue(result['diagnostics']['finite'])
            self.assertAlmostEqual(result['simulated_time_s'], .02)
            self.assertTrue(checked['quality_gate']['numerical_passed'])

    def test_visual_inspection_rejects_tampered_inventory_and_initial_particles(self):
        with tempfile.TemporaryDirectory(prefix='liquid-volume-', dir='/tmp') as folder:
            self.assertTrue(simulate(scene(mode='visual'), folder, 30, make_video=False)['ok'])
            path = Path(folder) / 'result.json'
            original = json.loads(path.read_text())
            for kind, gate in (('inventory', 'liquid_volume_inventory_matches_solver'),
                               ('initial', 'liquid_volume_initial_state_matches_source')):
                changed = copy.deepcopy(original)
                if kind == 'inventory':
                    changed['trajectory']['diagnostics']['represented_water_volume_m3'] *= 1.1
                else:
                    changed['trajectory']['frames'][0]['p'][0][0] += .01
                path.write_text(json.dumps(changed))
                checked = inspect(folder)
                self.assertFalse(checked['ok'], checked)
                self.assertIn(gate, checked['quality_gate']['failed_checks'])
                self.assertNotIn(gate, checked['quality_gate']['precision_warnings'])

    def test_inspection_rejects_tampered_source_sampling_receipt(self):
        with tempfile.TemporaryDirectory(prefix='liquid-volume-', dir='/tmp') as folder:
            self.assertTrue(simulate(scene(), folder, 30, make_video=False)['ok'])
            path = Path(folder) / 'result.json'
            saved = json.loads(path.read_text())
            saved['plan']['liquid_volume_receipts']['water']['particle_sha256'] = 'f' * 64
            path.write_text(json.dumps(saved))
            checked = inspect(folder)
            self.assertFalse(checked['ok'], checked)
            self.assertIn('plan', str(checked.get('errors')))

    def test_legacy_scene_does_not_gain_conversion_fields_or_claims(self):
        source = scene()
        source['entities'][0]['shape'] = {'type': 'box', 'center': [0, .5, 0], 'size': [.12]*3}
        prepared = prepare(source)
        self.assertTrue(prepared['ok'])
        self.assertNotIn('liquid_volume_receipts', prepared['plan'])
        self.assertNotIn('liquid_volume_fits_limits', prepared['plan'])
        self.assertFalse(any('Mesh-volume conversion' in a for a in prepared['assumptions']))


if __name__ == '__main__':
    unittest.main()
