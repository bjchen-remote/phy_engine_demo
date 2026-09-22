"""Video acceptance must not silently become numerical certification."""
import json
from pathlib import Path
import tempfile
import unittest

from physics_demo.runner import inspect, load_scene, query, simulate, validate

ROOT = Path(__file__).resolve().parents[1]


class VisualAcceptanceTests(unittest.TestCase):
    def scene(self, mode):
        scene = load_scene(ROOT / 'examples/three_body.json')
        scene['world']['duration'] = .04
        if mode is not None:
            scene['budget']['validation'] = mode
        return scene

    def test_precision_warning_delivers_visual_but_strict_and_legacy_fail(self):
        for mode in ('visual', 'strict', None):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                self.assertTrue(simulate(self.scene(mode), folder, 30, make_video=False)['ok'])
                path = Path(folder)/'result.json'
                result = json.loads(path.read_text())
                result['trajectory']['diagnostics'].update(
                    nbody_relative_energy_drift=.03, nbody_invariants_conserved=False)
                path.write_text(json.dumps(result))
                checked = inspect(path)
                self.assertEqual(checked['ok'], mode == 'visual')
                self.assertFalse(checked['quality_gate']['numerical_passed'])
                self.assertEqual(bool(checked['quality_gate']['precision_warnings']), mode == 'visual')
                # A visual request still cannot pass incomplete time or false conservation.
                result['trajectory']['diagnostics']['completed'] = False
                path.write_text(json.dumps(result))
                self.assertFalse(inspect(path)['ok'])

    def test_visual_query_cannot_return_uncertified_numbers_or_series(self):
        with tempfile.TemporaryDirectory() as folder:
            scene = self.scene('visual')
            scene['queries'] = [{'id':'x', 'type':'series',
                'metric':{'type':'centroid', 'entity':scene['entities'][0]['id'], 'axis':'x'}}]
            self.assertTrue(simulate(scene, folder, 30, make_video=False)['ok'])
            path = Path(folder)/'result.json'
            result = json.loads(path.read_text())
            result['trajectory']['diagnostics'].update(
                nbody_relative_energy_drift=.03, nbody_invariants_conserved=False)
            path.write_text(json.dumps(result))
            checked = inspect(path)
            self.assertTrue(checked['ok'])
            self.assertFalse(checked['measurements']['usable'])
            for key in (None,'x'):
                answer = query(path,key)
                self.assertFalse(answer['ok'])
                self.assertEqual(answer['errors'][0]['code'],'numerical_precision_unverified')
                self.assertNotIn('samples',answer)
                self.assertNotIn('answers',answer)

    def test_visual_still_rejects_missing_video(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertTrue(simulate(self.scene('visual'), folder, 30, make_video=True)['ok'])
            path = Path(folder)/'result.json'
            (Path(folder)/'simulation.mp4').unlink()
            checked = inspect(path)
            self.assertFalse(checked['ok'])
            self.assertIn('verified_video_artifact',checked['quality_gate']['failed_checks'])

    def test_invalid_modes_are_rejected(self):
        for mode in ('off',True,[],{},0):
            self.assertFalse(validate(self.scene(mode))['ok'])

    def test_native_close_encounter_conserves_energy_at_coarse_output_clock(self):
        scene = self.scene('strict')
        scene['entities'] = scene['entities'][:2]
        for index,body in enumerate(scene['entities']):
            body.update(mass=1,position=[-.5+index,0,0],velocity=[0,0,0])
        scene['world'].update(duration=2,dt=.01,output_fps=30)
        scene['interactions'].update(gravity_G=1,softening=.001)
        scene['budget']['backend']='native'
        with tempfile.TemporaryDirectory() as folder:
            result=simulate(scene,folder,30,make_video=False)
            self.assertTrue(result['ok'],result.get('errors'))
            self.assertLess(abs(result['diagnostics']['nbody_relative_energy_drift']),.002)
            self.assertAlmostEqual(result['simulated_time_s'],2)
            self.assertEqual(result['diagnostics']['steps'],200)
