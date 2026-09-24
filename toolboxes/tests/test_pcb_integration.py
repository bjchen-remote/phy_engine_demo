"""Release-contract regression for the isolated PCB thermal domain."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from registry import read_json, write_json
from smoke import execute, validate_result


class PcbToolboxIntegrationTests(unittest.TestCase):
    def test_prepared_pcb_runs_in_sandbox_and_preserves_mechanical_domain(self):
        with tempfile.TemporaryDirectory(prefix='pcb-integration-') as temporary:
            job = Path(temporary).resolve()
            (job/'work/tmp').mkdir(parents=True)
            (job/'artifacts').mkdir()
            write_json(job/'task.json', {'schema_version':1, 'task_id':'pcb-regression',
                'request':{'text':'simulate PCB temperature', 'plan_required':True,
                           'trust':'untrusted_external_input'},
                'limits':{'wall_time_seconds':120, 'max_output_bytes':16*1024*1024,
                          'network':False}, 'output_dir':'artifacts', 'work_dir':'work'})

            def api(operation, arguments):
                write_json(job/'work/toolbox-call.json', {'schema_version':1,
                    'operation':operation, 'arguments':arguments})
                execute(ROOT/'physics', job, 'api', 8)
                return read_json(job/'work/toolbox-response.json')['result']

            example = api('pcb_example', {})
            self.assertTrue(example['ok'])
            spec = json.loads(example['spec_json'])
            spec['grid'] = {'nx':24, 'ny':20}
            spec['transient'] = {'duration_s':20, 'time_step_s':2,
                                 'initial_c':25, 'snapshot_count':8}
            prepared = api('pcb_prepare', {'spec_json':json.dumps(spec)})
            self.assertTrue(prepared['ok'] and prepared['ready_to_simulate'], prepared)
            self.assertEqual(read_json(job/'work/prepared-scene.json')['domain'],
                             'pcb_thermal')
            self.assertTrue(api('physics_simulate',
                                {'spec_json':prepared['spec_json']})['ready_to_run'])
            execute(ROOT/'physics', job, 'probe', 5)
            self.assertTrue(read_json(job/'capability.json')['supported'])
            execute(ROOT/'physics', job, 'run', 120)
            self.assertEqual(read_json(job/'artifacts/result-manifest.json')['status'],
                             'succeeded', read_json(job/'artifacts/result-manifest.json'))
            video, manifest = validate_result(job, 16*1024*1024)
            self.assertGreater(video.stat().st_size, 1000)
            self.assertEqual(manifest['verification']['domain'], 'pcb_thermal')
            self.assertIn('video_decode', manifest['verification']['checks'])
            inspected = api('pcb_inspect', {})
            self.assertTrue(inspected['ok'])
            self.assertAlmostEqual(inspected['report']['total_power_w'], 3.3)
            sample = api('pcb_query', {'x_m':0.03, 'y_m':0.03})
            self.assertGreater(sample['sample']['temperature_c'], 25)

            # A later mechanical preparation clears the tagged PCB model.
            api('physics_example', {'name':'three_body'})
            self.assertFalse((job/'work/prepared-scene.json').exists())

    def test_invalid_pcb_model_never_leaves_stale_preparation(self):
        with tempfile.TemporaryDirectory(prefix='pcb-invalid-') as temporary:
            job = Path(temporary).resolve()
            (job/'work/tmp').mkdir(parents=True)
            (job/'artifacts').mkdir()
            write_json(job/'task.json', {'schema_version':1,
                'request':{'text':'PCB', 'plan_required':True},
                'limits':{'wall_time_seconds':120, 'max_output_bytes':16*1024*1024}})
            write_json(job/'work/prepared-scene.json',
                       {'schema_version':1, 'domain':'pcb_thermal', 'scene':{}})
            write_json(job/'work/toolbox-call.json', {'schema_version':1,
                'operation':'pcb_prepare', 'arguments':{'spec_json':'{}'}})
            execute(ROOT/'physics', job, 'api', 8)
            response = read_json(job/'work/toolbox-response.json')['result']
            self.assertFalse(response['ok'])
            self.assertFalse((job/'work/prepared-scene.json').exists())


if __name__ == '__main__':
    unittest.main()
