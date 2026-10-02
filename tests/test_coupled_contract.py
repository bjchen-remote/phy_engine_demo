"""Public mixed scene, observer and saved-result integrity regressions."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from physics_demo.api import call_tool
from physics_demo.runner import inspect, prepare, query, simulate
from physics_demo.schema import normalize_and_validate
from physics_demo.planning import make_plan
from physics_demo.coupled import result_checks

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ('gyroscope_precession','torque_free_tumble','spring_rigid_pendulum',
            'water_spring_reaction','solid_spring_water','ghost_spring_water',
            'water_soft_sheet','water_sheet_spring_rigid')

def example(name='torque_free_tumble'):
    return json.loads((ROOT/'examples'/(name+'.json')).read_text())

class CoupledContractTests(unittest.TestCase):
    def test_public_capabilities_distinguish_legacy_rigid_from_rotating_rigid_body(self):
        engines = call_tool('physics_capabilities', {})['capabilities']['engines']
        self.assertEqual(engines['rigid']['entity_type'], 'rigid')
        self.assertEqual(engines['rigid_body']['entity_type'], 'rigid_body')
        self.assertEqual(engines['rigid_body']['route'], 'coupled')
        self.assertEqual(engines['rigid_body']['limits'], engines['coupled']['limits'])
        self.assertEqual(prepare(example())['plan']['backend'], engines['rigid_body']['route'])

    def test_examples_prepare_with_one_shared_clock(self):
        for name in EXAMPLES:
            with self.subTest(name=name):
                s = example(name)
                r = prepare(s)
                self.assertTrue(r['ready_to_simulate'], r)
                self.assertEqual(r['plan']['backend'], 'coupled')
                self.assertTrue(r['plan']['coupling_fits_limits'])

    def test_rotation_observations_survive_saved_query_and_inspect(self):
        s = example(); s['world']['duration'] = .05
        s['queries'] += [{'id':k,'type':'series','metric':{'type':k,'entity':'body'}} for k in ('angular_speed','axis_tilt')]
        with tempfile.TemporaryDirectory() as directory:
            r = simulate(s,directory,make_video=False)
            self.assertTrue(r['ok'],r)
            self.assertTrue(inspect(directory)['ok'])
            answers = json.loads((Path(directory)/'measurements.json').read_text())['answers']
            units = {a['id']:a['unit'] for a in answers}
            self.assertEqual(units['Lx'],'kg*m^2/s')
            self.assertEqual(units['rotation-energy'],'J')
            self.assertEqual(units['angular_speed'],'rad/s')
            self.assertEqual(units['axis_tilt'],'rad')
            self.assertTrue(query(directory)['ok'])

    def test_corrupt_mixed_frames_never_recover_success(self):
        s = example('spring_rigid_pendulum');s['world']['duration']=.05
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(simulate(s,directory,make_video=False)['ok'])
            path=Path(directory)/'result.json';original=json.loads(path.read_text())
            mutations=[lambda t:t['frames'][1]['q'][0].__setitem__(0,2),
                       lambda t:t['frames'][0]['r'][0].__setitem__(0,999),
                       lambda t:t['frames'][1]['g'][0].__setitem__(1,999),
                       lambda t:t.__setitem__('rigid_ids',['wrong']),
                       lambda t:t['point_effective_masses'].__setitem__('anchor',8),
                       lambda t:t['diagnostics'].__setitem__('contact_impulse_norm',-1)]
            for mutate in mutations:
                altered=copy.deepcopy(original);mutate(altered['trajectory']);path.write_text(json.dumps(altered))
                self.assertFalse(inspect(directory)['ok'])
                self.assertFalse(query(directory)['ok'])

    def test_rotation_metric_target_and_axis_are_checked(self):
        s=example('spring_rigid_pendulum')
        for metric in ({'type':'angular_speed','entity':'anchor'},
                       {'type':'angular_momentum','entity':'body','axis':'length'}):
            s['queries']=[{'id':'bad','type':'series','metric':metric}]
            self.assertFalse(normalize_and_validate(s)['valid'])

    def test_unsupported_mass_geometry_or_endpoint_is_explicit(self):
        for change in (
            lambda s:s['connections'][0].update(solid={'radius':.1,'mass':1}),
            lambda s:s['connections'][0].update(endpoints=[{'entity':'body','local_point':[0,0,0]},{'entity':'body','local_point':[0,1,0]}]),
            lambda s:s['entities'][0].update(mass=1e-320),
            lambda s:s['world'].update(duration=1e-5),
            lambda s:s['entities'][1].update(orientation=[0,0,0,0]),
            lambda s:s['entities'][1].update(pivot={'point':[0,0,0],'local_point':[1,1,0]}),
            lambda s:s['budget'].update(backend='python'),
            lambda s:s['interactions'].update(mutual_gravity=True),
        ):
            s=example('spring_rigid_pendulum');change(s)
            r=prepare(s)
            self.assertFalse(r.get('ready_to_simulate',False),r)
            self.assertTrue(r['errors'])

    def test_stiff_springs_are_refused_without_altering_material(self):
        s=example('spring_rigid_pendulum');s['connections'][0]['stiffness']=1e7
        original=copy.deepcopy(s)
        r=prepare(s)
        self.assertFalse(r['ready_to_simulate'])
        self.assertIn('coupling_budget_exceeded',{e['code'] for e in r['errors']})
        self.assertEqual(s,original)

    def test_id_addressed_patch_then_prepare_rotation(self):
        r=call_tool('physics_patch',{'scene_json':json.dumps(example()),'operations':[
            {'op':'replace','path':'/entities/@body/angular_velocity','value_json':'[0.1,6,0.2]'}]})
        self.assertTrue(r['ok'],r)
        self.assertTrue(call_tool('physics_prepare',{'scene_json':r['scene_json']})['ready_to_simulate'])

    def test_id_patch_can_replace_or_remove_whole_query_atomically(self):
        s=example('water_spring_reaction');original=copy.deepcopy(s)
        threshold={'id':'ball-crossing','type':'threshold','metric':{'type':'centroid','entity':'bob','axis':'x'},'operator':'gte','value':.4}
        r=call_tool('physics_patch',{'scene_json':json.dumps(s),'operations':[
            {'op':'replace','path':'/queries/@ball-x','value_json':json.dumps(threshold)},
            {'op':'remove','path':'/queries/@force','value_json':'null'}]})
        self.assertTrue(r['ok'],r)
        self.assertEqual([q['id'] for q in r['scene']['queries']],['ball-crossing','water-x'])
        failed=call_tool('physics_patch',{'scene_json':json.dumps(s),'operations':[
            {'op':'replace','path':'/entities/@bob/mass','value_json':'10'},
            {'op':'remove','path':'/queries/@missing','value_json':'null'}]})
        self.assertFalse(failed['ok'])
        self.assertEqual(s,original)
