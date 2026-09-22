"""Flight, volume and input contracts for finite liquid launch factories."""
import copy
import json
import math
import time
import unittest

from physics_demo.api import call_tool
from physics_demo.runner import prepare
from physics_demo.systems import build_system
from physics_demo.core.backend import run_scene


class BallisticBurstTests(unittest.TestCase):
    def test_volume_apex_and_range_have_physical_meaning(self):
        spec={'type':'ballistic_burst','volume':.05,'apex_height':.8,'spread_radius':1.1}
        original=copy.deepcopy(spec);s=build_system(spec)
        volume=sum(4*math.pi*e['shape']['radius']**3/3 for e in s['entities'])
        self.assertAlmostEqual(volume,.05)
        self.assertEqual(spec,original)
        self.assertEqual(s['force_fields'],[])
        for i,e in enumerate(s['entities']):
            vx,vy,vz=e['velocity'];g=-s['world']['gravity'][1]
            self.assertAlmostEqual(vy*vy/(2*g),.8*(1 if i%2==0 else .75))
            flight=(vy+math.sqrt(vy*vy+2*g*e['shape']['center'][1]))/g
            self.assertAlmostEqual(math.hypot(vx,vz)*flight,1.1)

    def test_full_free_flight_envelope_clears_walls(self):
        s=build_system({'type':'ballistic_burst'})
        for e in s['entities']:
            p=e['shape']['center'];v=e['velocity'];r=e['shape']['radius']
            for step in range(101):
                t=s['world']['duration']*step/100
                point=[p[a]+v[a]*t+.5*s['world']['gravity'][a]*t*t for a in range(3)]
                if point[1]<r:continue  # The explicit ground collider ends free flight.
                for a in range(3):
                    self.assertGreater(point[a]-r,s['world']['bounds']['min'][a])
                    self.assertLess(point[a]+r,s['world']['bounds']['max'][a])
        self.assertTrue(prepare(s)['ready_to_simulate'])

    def test_free_liquid_centroid_follows_a_parabola(self):
        s=build_system({'type':'ballistic_burst','parcel_count':1,'source_radius':0,
                       'duration':.2,'dt':.001,'spacing':.04,'spread_radius':.3})
        p=prepare(s);self.assertTrue(p['ready_to_simulate'],p)
        result=run_scene(p['scene'],p['plan'],time.monotonic()+10)
        initial=s['entities'][0];frames=result['frames']
        for f in frames:
            centre=[sum(pos[a] for pos in f['p'])/len(f['p']) for a in range(3)]
            t=f['t']
            for a in range(3):
                expected=initial['shape']['center'][a]+initial['velocity'][a]*t+.5*s['world']['gravity'][a]*t*t
                self.assertAlmostEqual(centre[a],expected,delta=.015)

    def test_invalid_and_unresolved_inputs_fail_without_silent_repair(self):
        for fields in ({'volume':0},{'source_radius':0},{'parcel_count':25},
                       {'parcel_count':2.5},{'spacing':.5},{'source_height':.001},
                       {'preset':'invented'},{'validation':'fake'},{'continuous':True}):
            with self.assertRaises(ValueError):build_system({'type':'ballistic_burst',**fields})
        for field in ('volume','apex_height','gravity','duration','parcel_count'):
            for value in (True,[],None,'run()',float('nan'),float('inf'),10**500):
                with self.assertRaises(ValueError):build_system({'type':'ballistic_burst',field:value})

    def test_api_exposes_lava_and_launch_scope(self):
        result=call_tool('physics_system',{'spec_json':json.dumps({'type':'ballistic_burst','preset':'lava'})})
        self.assertTrue(result['ok'],result)
        self.assertIn('Finite',result['model_scope'])
        self.assertEqual(result['next_action']['tool'],'physics_prepare')
        self.assertTrue(call_tool('physics_liquid',{'preset':'lava','entity_id':'parcel-1'})['ok'])
        self.assertTrue(prepare(result['scene'])['ready_to_simulate'])
