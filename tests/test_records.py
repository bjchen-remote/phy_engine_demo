"""Public recorded state must be complete, copied and bound to trusted files."""
import json
from pathlib import Path
import tempfile
import unittest

from physics_demo import load_run, run_system
from physics_demo.analysis import compare_pendulums, pendulum_report


class RecordedRunTests(unittest.TestCase):
    def test_state_uses_full_step_observations_and_accessors_return_copies(self):
        with tempfile.TemporaryDirectory() as directory:
            r=run_system({'type':'double_pendulum','duration':.1,'dt':.002,'output_fps':1},directory,make_video=False)
            self.assertTrue(r['ok'],r)
            run=load_run(directory)
            self.assertEqual(len(run.times),51)
            self.assertEqual(len(json.loads((Path(directory)/'result.json').read_text())['trajectory']['frames']),2)
            self.assertEqual(run.state('bob2',1)['time_s'],.002)
            series=run.series('bob2-x');series['values'][0]=999
            scene=run.scene;scene['entities'][1]['mass']=999
            self.assertNotEqual(run.series('bob2-x')['values'][0],999)
            self.assertEqual(run.scene['entities'][1]['mass'],1)
            with self.assertRaises(ValueError):run.state('anchor')
            for index in (True,1.2,52,-52):
                with self.assertRaises(ValueError):run.state('bob2',index)
            report=pendulum_report(run)
            self.assertLess(report['peak_energy_drift_fraction_of_scale'],1e-6)
            self.assertLess(report['maximum_rod_length_error_m'],1e-8)
            self.assertFalse(report['long_term_stability_proven'])

    def test_tampered_observations_and_scene_are_never_loaded(self):
        for name in ('measurements.json','scene.normalized.json'):
            with tempfile.TemporaryDirectory() as directory:
                run_system({'type':'pendulum','duration':.05},directory,make_video=False)
                path=Path(directory)/name
                data=json.loads(path.read_text())
                if name=='measurements.json':data['observations']['columns']['bob1-x'][0]=999
                else:data['entities'][1]['mass']=999
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):load_run(directory)

    def test_comparison_rejects_different_models_and_aligns_refined_grid(self):
        with tempfile.TemporaryDirectory() as parent:
            root=Path(parent)
            configs=[{'type':'double_pendulum','duration':.05},
                     {'type':'double_pendulum','duration':.05,'masses':[1,2]},
                     {'type':'double_pendulum','duration':.05,'dt':.001},
                     {'type':'double_pendulum','duration':.04}]
            runs=[]
            for i,config in enumerate(configs):
                run_system(config,root/str(i),make_video=False);runs.append(load_run(root/str(i)))
            for other in (runs[1],runs[3]):
                with self.assertRaises(ValueError):compare_pendulums(runs[0],other)
            refined=compare_pendulums(runs[0],runs[2])
            self.assertEqual(refined['sample_counts'],{'first':26,'second':51,'common':26})
            self.assertLess(refined['maximum_separation_m'],1e-6)
            same=compare_pendulums(runs[0],runs[0])
            self.assertEqual(same['maximum_separation_m'],0)
            self.assertEqual(same['threshold_status'],'not_observed')
            self.assertFalse(same['chaos_proven'])
