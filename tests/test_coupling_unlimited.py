"""Synthetic planning-only regressions. No original requests, solver, video or network."""
from __future__ import annotations
import copy
import unittest
from unittest.mock import patch

from physics_demo.runner import prepare
from physics_demo.io import mesh_scene

def scene(nodes=20):
    return {'name':'synthetic-coupling-budget-regression',
        'world':{'duration':1.,'dt':0.0001,'output_fps':1,'gravity':[0,0,0],
                 'bounds':{'min':[-4,-4,-4],'max':[4,4,4]}},
        'budget':{'wall_time_s':300,'quality':'preview','backend':'native','validation':'strict'},
        'entities':[{'id':'p'+str(i),'type':'point_mass','mass':1.,
                     'position':[(i%5)*.2,(i//5)*.2,0.],'velocity':[0,0,0],
                     'collision_radius':.01} for i in range(nodes)],
        'interactions':{'mutual_gravity':False},
        'coupling':{'substeps':8,'iterations':16},'connections':[]}

def mesh_case():
    value=scene(1)
    value['world']['duration']=0.0001
    value['entities'].append({'id':'sheet','type':'mesh','motion':'static',
        'position':[0,0,1],'velocity':[0,0,0],
        'mesh':{'vertices':[[0,0,0],[.2,0,0],[0,.2,0]],'triangles':[[0,1,2]]}})
    return value

def codes(result):
    return [error['code'] for error in result.get('errors',[])]

class CouplingUnlimitedRegression(unittest.TestCase):
    def test_real_prepare_work_only_rejection_and_host_unlimited(self):
        value=scene();before=copy.deepcopy(value)
        finite=prepare(value)
        self.assertEqual(codes(finite),['coupling_budget_exceeded'])
        self.assertFalse(finite['ready_to_simulate'])
        self.assertGreater(finite['plan']['coupling_work_units'],2e9)
        self.assertTrue(finite['plan']['timing_estimate']['fits_budget'])
        unlimited=prepare(value,unlimited=True)
        self.assertTrue(unlimited['ready_to_simulate'])
        self.assertEqual(unlimited['plan']['coupling_work_units'],finite['plan']['coupling_work_units'])
        for key in ('world','entities','connections','coupling'):
            self.assertEqual(unlimited['scene'][key],finite['scene'][key])
        self.assertEqual(value,before)

    def test_ordinary_low_work_prepare_unchanged(self):
        value=scene(2);value['world']['dt']=.001
        for unlimited in (False,True):
            with self.subTest(unlimited=unlimited):
                result=prepare(value,unlimited=unlimited)
                self.assertTrue(result['ready_to_simulate'])
                self.assertLess(result['plan']['coupling_work_units'],2e9)
                self.assertEqual(result['scene']['budget']['validation'],'strict')

    def test_scene_cannot_self_authorize_unlimited(self):
        value=scene();value['budget']['unlimited_runtime']=True
        result=prepare(value)
        self.assertFalse(result['ready_to_simulate'])
        self.assertIn('unlimited_runtime_authorization',codes(result))

    def test_spring_resolution_still_rejects_derived_substeps(self):
        value=scene(2);value['world']['dt']=.001
        value['connections']=[{'id':'spring','type':'spring','entities':['p0','p1'],
            'rest_length':.2,'stiffness':1e7,'damping':0.}]
        result=prepare(value,unlimited=True)
        self.assertIn('coupling_budget_exceeded',codes(result))
        self.assertGreater(result['plan']['coupling_substeps'],64)

    def test_force_boundary_headroom_remains_in_substep_guard(self):
        value=scene(1);value['coupling']['substeps']=64
        accepted=prepare(value,unlimited=True)
        self.assertTrue(accepted['ready_to_simulate'])
        value['force_fields']=[{'id':'pulse','type':'uniform','targets':['p0'],
            'acceleration':[0,0,0],'start_time':.00005,'end_time':.00015}]
        result=prepare(value,unlimited=True)
        self.assertEqual(result['plan']['coupling_substeps'],64)
        self.assertEqual(result['plan']['coupling_event_headroom'],1)
        self.assertIn('coupling_budget_exceeded',codes(result))

    def test_step_ceiling_is_independent_of_elapsed_time_budget(self):
        value=scene(1);value['world']['duration']=10.
        accepted=prepare(value,unlimited=True)
        self.assertEqual(accepted['plan']['steps'],100000)
        self.assertTrue(accepted['ready_to_simulate'])
        value['world']['duration']=10.0001
        result=prepare(value,unlimited=True)
        self.assertGreater(result['plan']['steps'],100000)
        self.assertIn('coupling_budget_exceeded',codes(result))

    def test_initial_geometry_must_remain_within_world(self):
        value=mesh_case();value['entities'][-1]['position']=[5.,0.,0.]
        result=prepare(value,unlimited=True)
        self.assertFalse(result['ready_to_simulate'])
        self.assertIn('initial_state_outside_bounds',codes(result))

    def test_invalid_mesh_topology_not_relaxed(self):
        value=mesh_case()
        value['entities'][-1]['mesh']['triangles']=[[0,0,1]]
        result=prepare(value,unlimited=True)
        self.assertFalse(result['ready_to_simulate'])
        self.assertIn('invalid_mesh',codes(result))

    def test_low_level_mesh_size_estimate_still_enforces_coupled_cap(self):
        original=mesh_scene.make_mesh_plan
        for count,allowed in ((2048,True),(2049,False)):
            def estimated(scene,include_video):
                plan=original(scene,include_video)
                plan['mesh_vertices']=count
                return plan
            with self.subTest(vertices=count),patch.object(mesh_scene,'make_mesh_plan',estimated):
                result=prepare(mesh_case(),unlimited=True)
                self.assertEqual(result['ready_to_simulate'],allowed)
                if not allowed:self.assertIn('coupling_budget_exceeded',codes(result))

    def test_mesh_resource_gate_remains_even_with_host_unlimited(self):
        original=mesh_scene.make_mesh_plan
        def blocked(scene,include_video):
            plan=original(scene,include_video)
            plan['mesh_fits_limits']=False
            return plan
        with patch.object(mesh_scene,'make_mesh_plan',blocked):
            result=prepare(mesh_case(),unlimited=True)
        self.assertEqual(result['plan']['mesh_vertices'],3)
        self.assertFalse(result['plan']['mesh_fits_limits'])
        self.assertFalse(result['ready_to_simulate'])
        self.assertIn('coupling_budget_exceeded',codes(result))

if __name__ == "__main__":
    unittest.main()
