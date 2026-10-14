"""Candidate selection integration with real engine geometry and analytic meshes."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'toolboxes')]
from modeling_flow import runner
from modeling_flow.contracts import FlowError, digest, load_request
from modeling_flow.meshes import audit_mesh, orient_outward, scale_mesh
from modeling_flow.candidate_quality import evaluate_candidate


def cube(subdivisions=0):
    vertices = [[0,0,0],[1,0,0],[1,1,0],[0,1,0],
                [0,0,1],[1,0,1],[1,1,1],[0,1,1]]
    faces = [[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],
             [1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]]
    for _ in range(subdivisions):
        midpoints, next_faces = {}, []
        def midpoint(a, b):
            key = tuple(sorted((a, b)))
            if key not in midpoints:
                midpoints[key] = len(vertices)
                vertices.append([(vertices[a][k] + vertices[b][k])/2 for k in range(3)])
            return midpoints[key]
        for a,b,c in faces:
            ab,bc,ca = midpoint(a,b),midpoint(b,c),midpoint(c,a)
            next_faces.extend([[a,ab,ca],[ab,b,bc],[ca,bc,c],[ab,bc,ca]])
        faces = next_faces
    return scale_mesh({'vertices':vertices,'faces':faces},1.)[0]


class CandidateSelectionTests(unittest.TestCase):
    def setUp(self):
        self.source = cube(3)
        self.request = {'allow_decimation':True,'physical_extent_m':1.,'scale_axis':'max',
                        'simulation_purpose':'surface_xpbd'}

    def force_reduction(self, mesh, simulation=False):
        report = audit_mesh(mesh,simulation=simulation)
        if mesh is self.source:
            report['simulation_budget_passed'] = False
        return report

    def folded_candidate(self):
        candidate = copy.deepcopy(self.source)
        index = candidate['vertices'].index([0.,0.,.5])
        candidate['vertices'][index][2] = -.5001
        return candidate

    def test_engine_self_intersection_rejection_continues_to_next_candidate(self):
        before = copy.deepcopy(self.source)
        folded = self.folded_candidate()
        # The old modeling-only gate accepts this closed, oriented triangle mesh.
        self.assertTrue(audit_mesh(folded,simulation=True)['simulation_eligible'])
        reducer = Mock(side_effect=[folded,copy.deepcopy(self.source)])
        with patch.object(runner,'audit_mesh',side_effect=self.force_reduction), \
             patch.object(runner,'topology_decimate',reducer), \
             patch.dict(sys.modules,{'pymeshlab':Mock()}):
            mesh,report = runner._decimate(self.source,None,self.request)
        self.assertEqual(report['target_faces'],8188)
        self.assertEqual([a['status'] for a in report['attempts']],['rejected','accepted'])
        self.assertEqual(report['attempts'][0]['quality']['code'],'candidate_self_intersection')
        self.assertTrue(report['selected_quality']['engine_audit']['metadata']['self_intersection_checked'])
        self.assertEqual(self.source,before)
        self.assertEqual(mesh,before)

    def test_mutating_reducer_receives_fresh_source_each_attempt(self):
        before = copy.deepcopy(self.source)
        seen = []
        def reducer(source,target):
            self.assertEqual(source,before)
            seen.append(source)
            if len(seen) == 1:
                source['vertices'][0] = [50.,50.,50.]
                return self.folded_candidate()
            return source
        with patch.object(runner,'audit_mesh',side_effect=self.force_reduction), \
             patch.object(runner,'topology_decimate',side_effect=reducer), \
             patch.dict(sys.modules,{'pymeshlab':Mock()}):
            _,report = runner._decimate(self.source,None,self.request)
        self.assertEqual(len(seen),2)
        self.assertIsNot(seen[0],seen[1])
        self.assertIsNot(seen[0],self.source)
        self.assertEqual(self.source,before)
        self.assertTrue(report['raw_geometry_preserved'])

    def test_inward_cleaned_source_uses_outward_comparison_and_preserves_raw(self):
        # A disconnected generated mesh cannot be globally oriented before
        # cleanup. Substitute only the optional numpy/scipy cleanup seam; the
        # retained source, orientation, quality checks and engine audit are real.
        inward = cube()
        inward['faces'] = [[a,c,b] for a,b,c in inward['faces']]
        fragment = cube()
        fragment['vertices'] = [[v * .0005 for v in point] for point in fragment['vertices']]
        raw = {'vertices':copy.deepcopy(inward['vertices']) + fragment['vertices'],
               'faces':copy.deepcopy(inward['faces']) + [[v + 8 for v in face] for face in fragment['faces']]}
        self.assertFalse(orient_outward(raw)[1])
        self.assertEqual(audit_mesh(raw)['connected_components'],2)
        before = copy.deepcopy(raw)
        with patch.object(runner,'numerical_component_cleanup',
                          return_value=(inward,{'applied':True})) as cleanup, \
             patch.object(runner,'topology_decimate') as reducer:
            selected,report = runner._decimate(raw,None,self.request)
        cleanup.assert_called_once_with(raw)
        reducer.assert_not_called()
        self.assertEqual(raw,before)
        self.assertLess(audit_mesh(inward)['signed_volume'],0)
        self.assertEqual(selected,cube())
        self.assertTrue(report['comparison_source_winding_reversed'])
        self.assertEqual(report['raw_geometry_sha256'],digest(before))
        self.assertEqual(report['prepared_source_geometry_sha256'],digest(inward))
        self.assertEqual(report['comparison_source_geometry_sha256'],digest(selected))
        self.assertEqual(report['selected_quality']['source_sha256'],digest(selected))
        self.assertTrue(report['selected_quality']['engine_audit']['metadata']['self_intersection_checked'])

    def test_inward_reduction_source_still_requires_real_candidate_engine_gate(self):
        self.source['faces'] = [[a,c,b] for a,b,c in self.source['faces']]
        before = copy.deepcopy(self.source)
        folded = self.folded_candidate()
        reducer = Mock(side_effect=[folded,copy.deepcopy(self.source)])
        with patch.object(runner,'audit_mesh',side_effect=self.force_reduction), \
             patch.object(runner,'topology_decimate',reducer), \
             patch.dict(sys.modules,{'pymeshlab':Mock()}):
            selected,report = runner._decimate(self.source,None,self.request)
        self.assertEqual(self.source,before)
        self.assertTrue(report['comparison_source_winding_reversed'])
        self.assertEqual([a['status'] for a in report['attempts']],['rejected','accepted'])
        self.assertEqual(report['attempts'][0]['quality']['code'],'candidate_self_intersection')
        self.assertGreater(audit_mesh(selected)['signed_volume'],0)
        self.assertTrue(report['selected_quality']['engine_audit']['metadata']['self_intersection_checked'])

    def test_under_capacity_budget_failure_also_attempts_smaller_candidate(self):
        from physics_demo.core import meshes as engine
        actual = runner.evaluate_candidate
        calls = []
        def evaluate(*args,**kwargs):
            calls.append(None)
            if len(calls) == 1:
                with patch.object(engine,'MAX_PAIR_CHECKS',0),patch.object(engine,'_AUDIT_CACHE',{}):
                    return actual(*args,**kwargs)
            return actual(*args,**kwargs)
        with patch.object(runner,'evaluate_candidate',side_effect=evaluate), \
             patch.object(runner,'topology_decimate',return_value=cube()) as reducer, \
             patch.dict(sys.modules,{'pymeshlab':Mock()}):
            _,report = runner._decimate(self.source,None,self.request)
        self.assertEqual(report['attempts'][0]['status'],'budget_exhausted')
        self.assertEqual(report['attempts'][1]['status'],'accepted')
        self.assertLess(reducer.call_args.args[1],len(self.source['faces']))

    def test_invalid_under_capacity_mesh_cannot_bypass_engine_gate(self):
        folded, _ = scale_mesh(self.folded_candidate(),self.request['physical_extent_m'])
        with self.assertRaises(FlowError) as rejected:
            runner._decimate(folded,None,self.request)
        self.assertFalse(rejected.exception.details['attempts'][0]['quality']['accepted'])
        self.assertEqual(rejected.exception.details['attempts'][0]['quality']['code'],
                         'candidate_self_intersection')

    def test_every_engine_budget_failure_is_retained_and_never_accepted(self):
        from physics_demo.core import meshes as engine
        with patch.object(runner,'audit_mesh',side_effect=self.force_reduction), \
             patch.object(runner,'topology_decimate',return_value=copy.deepcopy(self.source)), \
             patch.dict(sys.modules,{'pymeshlab':Mock()}), \
             patch.object(engine,'MAX_PAIR_CHECKS',0),patch.object(engine,'_AUDIT_CACHE',{}):
            with self.assertRaises(FlowError) as rejected:
                runner._decimate(self.source,None,self.request)
        report = rejected.exception.details
        self.assertEqual(report['status'],'budget_exhausted')
        self.assertFalse(report['applied'])
        self.assertTrue(report['reduction_attempted'])
        self.assertEqual(len(report['attempts']),3)
        for attempt in report['attempts']:
            self.assertEqual(attempt['status'],'budget_exhausted')
            self.assertEqual(attempt['code'],'engine_audit_budget_exhausted')
            self.assertFalse(attempt['quality']['accepted'])

    def test_standalone_child_bootstraps_only_the_host_pinned_engine(self):
        script = ('import json,sys; from modeling_flow.candidate_quality import evaluate_candidate; '
                  'm=json.load(sys.stdin); print(json.dumps(evaluate_candidate(m,m)))')
        env = dict(os.environ)
        env.update(PYTHONPATH=str(ROOT/'toolboxes'),PYTHONDONTWRITEBYTECODE='1',
                   PHYSICS_PIPELINE_PHYSICS_ROOT=str(ROOT/'toolboxes/physics'))
        with tempfile.TemporaryDirectory(prefix='candidate-child-') as directory:
            completed = subprocess.run([sys.executable,'-c',script],cwd=directory,env=env,
                input=json.dumps(cube()),text=True,capture_output=True,check=True)
        report = json.loads(completed.stdout)
        self.assertTrue(report['accepted'],report)
        self.assertEqual(report['engine_audit']['status'],'passed')
        self.assertEqual(len(report['engine_audit']['module_sha256']),64)

    def test_missing_pinned_engine_is_explicitly_unsupported(self):
        with patch.dict(os.environ,{'PHYSICS_PIPELINE_PHYSICS_ROOT':'/nonexistent/candidate-engine'}):
            report = evaluate_candidate(cube(),cube())
        self.assertEqual(report['status'],'unsupported')
        self.assertEqual(report['code'],'engine_auditor_unavailable')
        self.assertFalse(report['accepted'])

    def test_purpose_contract_is_physical_only_and_bounded(self):
        with tempfile.TemporaryDirectory(prefix='candidate-contract-') as directory:
            image = Path(directory)/'fixture.png'
            image.write_bytes(b'fixture')
            request = {'schema_version':'modeling-flow-request/1','image_path':str(image),
                       'physical_extent_m':1.,'material':{'name':'assumed','density_kg_m3':1000}}
            self.assertEqual(load_request(request)['simulation_purpose'],'surface_xpbd')
            self.assertEqual(load_request({**request,'simulation_purpose':'liquid_volume'})['simulation_purpose'],
                             'liquid_volume')
            for purpose in ('cloth','tet_volume',True,None):
                with self.subTest(purpose=purpose),self.assertRaises(FlowError):
                    load_request({**request,'simulation_purpose':purpose})
            with self.assertRaises(FlowError):
                load_request({'schema_version':'modeling-flow-preview-request/1','image_path':str(image),
                              'simulation_purpose':'liquid_volume'})

    def flow_fixture(self):
        # Reuse the explicit offline adapter fixture; no installed weights or
        # neural libraries participate. JSON/OBJ/quality admission are real.
        from test_modeling_flow import ModelingFlowTests
        fixture = ModelingFlowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def test_liquid_purpose_is_geometry_only_in_result_mesh_and_receipt(self):
        fixture = self.flow_fixture()
        request = {**fixture.request,'simulation_purpose':'liquid_volume'}
        with fixture.fake_runtime():
            result = runner.generate(fixture.config,request,fixture.root/'liquid-domain')
            runner._model_mesh.assert_called_once()
        self.assertTrue(result['ok'],result)
        self.assertFalse(result['ready_to_simulate'])
        self.assertFalse(result['ready_for_scene'])
        self.assertTrue(result['ready_for_volume_sampling'])
        self.assertEqual(result['simulation_purpose'],'liquid_volume')
        receipt = json.loads(Path(result['receipt_path']).read_text())
        mesh = json.loads(Path(result['simulation_mesh_json_path']).read_text())
        self.assertEqual(receipt['simulation_purpose'],'liquid_volume')
        self.assertEqual(receipt['liquid_particleization'],'not_performed')
        self.assertFalse(receipt['ready_to_simulate'])
        self.assertEqual(mesh['simulation_purpose'],'liquid_volume')
        self.assertTrue(mesh['candidate_quality']['accepted'])
        self.assertNotIn('particles',mesh)

    def test_missing_engine_preserves_display_and_preview_skips_geometry_gate(self):
        fixture = self.flow_fixture()
        missing = {'PHYSICS_PIPELINE_PHYSICS_ROOT':'/nonexistent/candidate-engine'}
        with fixture.fake_runtime(),patch.dict(os.environ,missing):
            result = runner.generate(fixture.config,fixture.request,fixture.root/'rejected')
        self.assertFalse(result['ok'])
        self.assertFalse(result['ready_to_simulate'])
        self.assertTrue(Path(result['display_mesh_path']).is_file())
        self.assertFalse((fixture.root/'rejected/simulation_mesh.json').exists())
        receipt = json.loads(Path(result['receipt_path']).read_text())
        self.assertEqual(receipt['decimation']['attempts'][0]['status'],'unsupported')
        request = {'schema_version':'modeling-flow-preview-request/1',
                   'image_path':str(fixture.image),'display_cleanup':'none'}
        with fixture.fake_runtime(),patch.dict(os.environ,missing), \
             patch.object(runner,'evaluate_candidate',side_effect=AssertionError('preview called engine')):
            preview = runner.generate(fixture.config,request,fixture.root/'preview')
        self.assertTrue(preview['ok'],preview)
        self.assertFalse(preview['ready_to_simulate'])
        self.assertTrue(preview['ready_to_preview'])
        preview_receipt = json.loads(Path(preview['receipt_path']).read_text())
        self.assertTrue(preview_receipt['display_only'])
        self.assertEqual(preview_receipt['purpose'],'model_preview')
        for key in ('simulation_purpose','ready_for_scene','ready_for_volume_sampling','shape_reuse'):
            self.assertNotIn(key,preview_receipt)
            self.assertNotIn(key,preview)


if __name__ == '__main__':
    unittest.main()
