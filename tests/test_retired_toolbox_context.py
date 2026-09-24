"""A previous QQ experiment must not resurrect an obsolete sphere-drop scene."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / 'toolboxes/physics/toolbox_adapter.py'


class RetiredContextTests(unittest.TestCase):
    def test_old_sphere_scenes_cannot_be_prepared_or_loaded_from_context(self):
        for old_name, replacement in (
            ('droplet-on-sphere', 'droplet_sphere'),
            ('high-detail-droplet-on-sphere', 'high_detail_droplet_sphere'),
        ):
            with self.subTest(old_name=old_name), tempfile.TemporaryDirectory() as folder:
                job = Path(folder)
                (job/'work').mkdir()
                (job/'task.json').write_text(json.dumps({
                    'schema_version': 1,
                    'request': {'text': '把上一版水滴场景改好', 'plan_required': True},
                    'limits': {'wall_time_seconds': 180},
                }))
                old_scene = {'name': old_name}

                def call(operation, arguments):
                    (job/'work/toolbox-call.json').write_text(json.dumps({
                        'schema_version': 1, 'operation': operation,
                        'arguments': arguments,
                    }))
                    subprocess.run([
                        sys.executable, str(ADAPTER), '--phase', 'api',
                        '--task', str(job/'task.json'),
                    ], check=True, capture_output=True, timeout=8)
                    return json.loads((job/'work/toolbox-response.json').read_text())['result']

                (job/'work/previous-context.json').write_text(json.dumps({
                    'schema_version': 1,
                    'previous_request': 'private prior request',
                    'model': {'scene': old_scene},
                }))
                result = call('context', {})
                self.assertFalse(result['ok'])
                self.assertEqual(result['code'], 'retired_scene_reference')
                self.assertEqual(result['replacement_example'], replacement)
                self.assertEqual(result['next_action']['tool'], 'physics_example')
                self.assertNotIn('scene_json', result)
                self.assertNotIn('previous_request', result)

                (job/'work/prepared-scene.json').write_text('{"stale":true}')
                result = call('physics_prepare', {'scene_json': json.dumps(old_scene)})
                self.assertFalse(result['ok'])
                self.assertEqual(result['replacement_example'], replacement)
                self.assertFalse((job/'work/prepared-scene.json').exists())


if __name__ == '__main__':
    unittest.main()
