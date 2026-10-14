"""The QQ adapter keeps generated geometry in the task and completes a real prepare."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT/'toolboxes/physics'
sys.path.insert(0, str(PACKAGE))
spec = importlib.util.spec_from_file_location('qq_modeling_handoff', PACKAGE/'modeling.py')
modeling = importlib.util.module_from_spec(spec)
spec.loader.exec_module(modeling)


class MeshHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.job = Path(self.temporary.name)
        (self.job/'work').mkdir()
        self.task = {'limits':{'wall_time_seconds':None}}

    def tearDown(self):
        self.temporary.cleanup()

    def call(self, operation, arguments):
        (self.job/'work/toolbox-call.json').write_text(json.dumps({
            'operation':operation, 'arguments':arguments}))
        modeling.api_call(PACKAGE, self.job, self.task)
        return json.loads((self.job/'work/toolbox-response.json').read_text())['result']

    def test_large_mesh_can_be_inserted_and_prepared_without_vertex_copy(self):
        example = self.call('physics_example', {'name':'mesh_soft_drop'})
        self.assertTrue(example['ok'])
        scene = json.loads((self.job/'work/draft-scene.json').read_text())
        entity = scene['entities'][0]['id']
        mesh = self.call('physics_mesh', {'spec_json':json.dumps({
            'type':'ellipsoid', 'segments':32, 'rings':16,
            'provenance':{'source':'image', 'notes':'Depth 0.2 m is assumed.'}})})
        self.assertTrue(mesh['ok'], mesh)
        self.assertNotIn('mesh_json', mesh)
        patch = self.call('physics_patch', {'operations':[
            {'op':'replace', 'path':f'/entities/@{entity}/mesh', 'mesh_ref':mesh['mesh_ref']},
            {'op':'replace', 'path':'/world/duration', 'value_json':'0.25'}]})
        self.assertTrue(patch['ok'], patch)
        prepared = self.call('physics_prepare', {})
        self.assertTrue(prepared['ok'], prepared)
        self.assertTrue(prepared['ready_to_simulate'])
        actual = json.loads((self.job/'work/prepared-scene.json').read_text())['scene']
        self.assertEqual(actual['world']['duration'], 0.25)
        self.assertEqual(actual['entities'][0]['mesh']['metadata']['provenance']['notes'], 'Depth 0.2 m is assumed.')
        self.assertEqual(actual['entities'][0]['mesh']['metadata']['vertex_count'], mesh['audit']['vertex_count'])
        self.assertTrue(self.call('physics_simulate', {})['ready_to_run'])

    def test_references_are_confined_to_the_task_and_check_geometry_integrity(self):
        self.call('physics_example', {'name':'mesh_soft_drop'})
        mesh = self.call('physics_mesh', {'spec_json':json.dumps({'type':'box','subdivisions':1})})
        scene = json.loads((self.job/'work/draft-scene.json').read_text())
        path = '/entities/@'+scene['entities'][0]['id']+'/mesh'
        for reference in ('../outside', 'f'*64):
            with self.subTest(reference=reference), self.assertRaises((ValueError, FileNotFoundError)):
                self.call('physics_patch', {'operations':[{'op':'replace','path':path,'mesh_ref':reference}]})
        asset = self.job/'work/mesh-assets'/f"{mesh['mesh_ref']}.json"
        value = json.loads(asset.read_text()); value['vertices'][0][0] += 0.1
        asset.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError, 'integrity'):
            self.call('physics_patch', {'operations':[{'op':'replace','path':path,'mesh_ref':mesh['mesh_ref']}]})

    def test_missing_draft_does_not_fall_back_to_previous_experiment(self):
        (self.job/'work/previous-context.json').write_text(json.dumps({'model':{'scene':{}}}))
        with self.assertRaises(FileNotFoundError):
            self.call('physics_prepare', {})


if __name__ == '__main__':
    unittest.main()
