"""Synthetic imported mesh -> prepared liquid -> native data -> MP4/ZIP.

The test fixture stages the new engine source without modifying a release copy.
The supervisor's builder owns feature/version publication; the fixture installs
the same explicit feature and helper while exercising the real v1 adapter.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

TOOLBOXES = Path(__file__).resolve().parents[1]
ROOT = TOOLBOXES.parent
sys.path.insert(0, str(TOOLBOXES))
from build_pipeline import build
from pipeline.bundle import digest_tree, sha256, verify_bundle
from pipeline import bundle


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')


def readonly_profile(package, job, interpreter):
    """Public-test sandbox: pinned reads with all filesystem writes denied."""
    def literal(path):
        return str(path.resolve()).replace('\\', '\\\\').replace('"', '\\"')
    readable = [Path('/System'), Path('/usr'), Path('/private/etc'), Path('/dev'),
        Path('/Library/Developer/CommandLineTools'), Path('/opt/homebrew'),
        Path(sys.prefix), Path(sys.base_prefix), interpreter.parent, package, job]
    reads = ' '.join(f'(subpath "{literal(path)}")' for path in readable)
    roots = ' '.join(f'(literal "{path}")' for path in ('/', '/Library', '/Library/Developer', '/private', '/private/var'))
    return ' '.join(['(version 1)', '(deny default)', '(allow process*)',
        '(allow signal (target self))', '(allow sysctl-read)', '(allow mach-lookup)',
        '(allow ipc-posix*)', '(allow file-read-metadata (subpath "/"))',
        f'(allow file-read* {roots} {reads})', '(deny file-write*)', '(deny network*)'])


def imported_ref(task_id, asset_id, source_model_ref, producer, members):
    return hashlib.sha256(json.dumps({'task_id': task_id, 'asset_id': asset_id,
        'source_model_ref': source_model_ref, 'producer': producer, 'members': members},
        sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class ImageWorkerEnginePinTests(unittest.TestCase):
    def test_worker_engine_setting_overrides_inherited_checkout(self):
        spec = importlib.util.spec_from_file_location('_liquid_image_engine_pin', TOOLBOXES/'pipeline/image_modeling.py')
        image = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'bundle': bundle}):
            spec.loader.exec_module(image)
        with tempfile.TemporaryDirectory(prefix='liquid-worker-pin-') as temporary:
            directory = Path(temporary).resolve()
            root, job = directory/'package', directory/'job'
            (root/'modules/modeling').mkdir(parents=True)
            (root/'modules/modeling/flow_runner.py').write_text('# synthetic; never executed\n')
            (job/'inputs').mkdir(parents=True)
            pixels = job/'inputs/image.png'
            pixels.write_bytes(b'synthetic')
            config = job/'work/modeling-runtime/config.json'
            save(config, {'schema_version': 'modeling-flow-config/1'})
            task = {'request': {'input_images': [{'id': 'image', 'path': 'inputs/image.png', 'sha256': sha256(pixels)}]},
                    'modeling_runtime': {'config_path': 'work/modeling-runtime/config.json',
                        'config_sha256': sha256(config), 'python_executable': '/not-executed'},
                    'limits': {'wall_time_seconds': 30}}
            lock = {'engine': {'path': 'engine'}, 'modules': {'modeling': {'path': 'modules/modeling'}}}
            arguments = {'image_id': 'image', 'physical_extent_m': .2,
                         'material': {'name': 'water assumption', 'density_kg_m3': 1000}}
            atomic = lambda path, value: save(path, value)
            object_hash = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
            with patch.dict(os.environ, {'PHYSICS_PIPELINE_PHYSICS_ROOT': '/foreign/checkout'}), \
                    patch.object(image.subprocess, 'run', side_effect=RuntimeError('stop before inference')) as execute:
                with self.assertRaisesRegex(RuntimeError, 'stop before inference'):
                    image.generate_image_model(root, job, task, lock, arguments, atomic, object_hash)
            self.assertEqual(execute.call_args.kwargs['env']['PHYSICS_PIPELINE_PHYSICS_ROOT'], str(root/'engine'))


@unittest.skipUnless(sys.platform == 'darwin', 'native video workflow targets macOS')
class LiquidPipelineAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix='liquid-pipeline-')
        cls.directory = Path(cls.temporary.name).resolve()
        engine = cls.directory / 'engine-source'
        shutil.copytree(TOOLBOXES / 'physics', engine,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
        shutil.rmtree(engine / 'physics_release/physics_demo')
        shutil.copytree(ROOT / 'physics_demo', engine / 'physics_release/physics_demo',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.DS_Store'))
        cls.package = cls.directory / 'package'
        cls.lock = build(engine, cls.package)
        shutil.copyfile(TOOLBOXES / 'pipeline/liquefy.py', cls.package / 'liquefy.py')
        module = cls.package / cls.lock['modules']['modeling']['path']
        info = json.loads((module / 'module.json').read_text())
        info['liquefy'] = 'mesh-volume/1'
        save(module / 'module.json', info)
        cls.lock['modules']['modeling']['liquefy'] = 'mesh-volume/1'
        cls.lock['modules']['modeling']['digest'] = digest_tree(module)
        save(cls.package / 'bundle-lock.json', cls.lock)
        assert verify_bundle(cls.package) == cls.lock
        cls.package_digest = digest_tree(cls.package)
        cls.environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1',
                           'TMPDIR': str(cls.directory), 'PYTHONPATH': ''}

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.job = self.directory / self._testMethodName
        (self.job / 'work').mkdir(parents=True)
        (self.job / 'artifacts').mkdir()
        self.task = {'schema_version': 1, 'task_id': '液化-' + self._testMethodName,
            'output_dir': 'artifacts', 'work_dir': 'work',
            'request': {'text': '让已有模型瞬时变成水样液体', 'plan_required': True},
            'limits': {'wall_time_seconds': 60, 'network': False, 'max_output_bytes': 16*1024*1024},
            'delivery': {'data_attachments': True, 'max_data_bytes': 16*1024*1024}}
        save(self.job / 'task.json', self.task)
        vertices = [[0,0,0], [1,0,0], [1,1,0], [0,1,0],
                    [0,0,1], [1,0,1], [1,1,1], [0,1,1]]
        faces = [[0,2,1], [0,3,2], [4,5,6], [4,6,7],
                 [0,1,5], [0,5,4], [3,7,6], [3,6,2],
                 [0,4,7], [0,7,3], [1,2,6], [1,6,5]]
        incoming = self.job / 'work/imported-models/.staging'
        save(incoming / 'raw.json', {'vertices': vertices, 'faces': faces, 'units': 'model_unit'})
        (incoming / 'license.txt').write_text('Synthetic geometry fixture; CC0.\n')
        members = [{'path': 'raw.json', 'role': 'raw_mesh', 'sha256': sha256(incoming/'raw.json'),
                    'size_bytes': (incoming/'raw.json').stat().st_size},
                   {'path': 'license.txt', 'role': 'license', 'sha256': sha256(incoming/'license.txt'),
                    'size_bytes': (incoming/'license.txt').stat().st_size}]
        asset_id = 'a'*64
        producer = {'id': 'physics-modeling', 'version': 'prior-synthetic', 'digest': 'c'*64}
        self.reference = imported_ref(self.task['task_id'], asset_id, 'b'*64, producer, members)
        self.imported = incoming.with_name(self.reference)
        incoming.rename(self.imported)
        save(self.imported / 'manifest.json', {'schema_version': 'model-asset-import/1',
            'task_id': self.task['task_id'], 'imported_asset_ref': self.reference,
            'asset_id': asset_id, 'source_model_ref': 'b'*64,
            'producer': producer,
            'members': members})
        self.arguments = {'imported_asset_ref': self.reference, 'physical_extent_m': .12,
                          'entity_id': 'object', 'position': [0,.5,0], 'velocity': [0,0,0],
                          'spacing_m': .02, 'conversion': 'instant', 'conservation': 'volume',
                          'reference_density_kg_m3': 1000, 'preset': 'water'}

    def call(self, operation, arguments):
        save(self.job/'work/toolbox-call.json', {'schema_version': 1,
            'operation': operation, 'arguments': arguments})
        process = subprocess.run([sys.executable, str(self.package/'adapter.py'), '--phase', 'api',
            '--task', str(self.job/'task.json')], env=self.environment, capture_output=True, text=True, timeout=70)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads((self.job/'work/toolbox-response.json').read_text())['result']

    def prepared_liquid(self):
        # An actual source solid is replaced wholesale; no overlapping mesh is
        # kept as a collider and no unknown field is left on the fluid entity.
        save(self.job/'work/draft-scene.json', {'version': 1, 'name': 'synthetic mesh liquid',
            'world': {'gravity': [0,0,0], 'duration': .02, 'dt': .001, 'output_fps': 10,
                      'bounds': {'min': [-1,-1,-1], 'max': [1,2,1]}},
            'budget': {'quality': 'preview', 'backend': 'native', 'validation': 'strict', 'wall_time_s': 60},
            'entities': [{'id': 'object', 'type': 'mesh', 'motion': 'static', 'mesh': {}}]})
        source_hash = sha256(self.imported/'raw.json')
        converted = self.call('modeling_liquefy', self.arguments)
        self.assertTrue(converted.get('ok'), converted)
        self.assertFalse(converted['ready_to_simulate'])
        self.assertEqual(sha256(self.imported/'raw.json'), source_hash)
        self.assertEqual(converted['next_action']['arguments']['operations'][0]['op'], 'replace')
        inserted = self.call('physics_patch', converted['next_action']['arguments'])
        self.assertTrue(inserted.get('ok'), inserted)
        scene = json.loads((self.job/'work/draft-scene.json').read_text())
        self.assertEqual(len(scene['entities']), 1)
        self.assertEqual(scene['entities'][0]['type'], 'fluid')
        self.assertEqual(scene['entities'][0]['shape']['type'], 'mesh_volume')
        prepared = self.call('physics_prepare', {})
        self.assertTrue(prepared.get('ready_to_simulate'), prepared)
        return converted

    def verify_portable_projection(self, result_path, result_hash):
        """Reconstruct the complete ZIP from original inputs in the pinned runtime."""
        # An isolated interpreter prevents other tests' source-checkout module
        # caches from replacing the actual pinned engine/rendering verifier.
        script = r'''
import json, pathlib, sys
root, job = map(pathlib.Path, sys.argv[1:3])
package_hash, result_hash, model_hash = sys.argv[3:]
sys.path.insert(0, str(root))
from bundle import digest_tree, inside, read_json, sha256, verify_bundle
assert digest_tree(root) == package_hash
lock = verify_bundle(root)
assert lock['modules']['rendering']['portable_data'] == 'pipeline-data/2'
engine = inside(root, lock['engine']['path'])
rendering = inside(root, lock['modules']['rendering']['path'])
sys.path[:0] = [str(rendering), str(engine/'physics_release'), str(engine)]
prepared = read_json(inside(job, 'work/prepared-scene.json'), 4_000_000)
task = read_json(inside(job, 'task.json'))
source_result = inside(job, 'work/physics/artifacts/result.json')
source_model = inside(job, 'work/physics/artifacts/model.json')
for candidate in (root/'.portable-write-must-fail', job/'work/.portable-write-must-fail', source_result):
    try:
        with candidate.open('ab') as stream:
            stream.write(b'forbidden verifier write')
    except PermissionError:
        pass
    else:
        raise AssertionError('read-only verifier sandbox allowed a filesystem write')
assert sha256(source_result) == result_hash and sha256(source_model) == model_hash
assert read_json(source_result, 64*1024*1024)['scene'] == prepared['scene']
assert read_json(source_model, 64*1024*1024)['model'] == prepared['scene']
import image_modeling, liquefy
inventory = image_modeling.used_modeling_assets(job, prepared['scene'], root, readonly=True)
inventory += liquefy.used_liquefy_assets(job, prepared['scene'], root, task, lock, readonly=True)
assert inventory and any(item['name'].endswith('/source-mesh.json') for item in inventory)
from pipeline_presentation.portable_result import verify_portable_data
proof = verify_portable_data(source_result, source_model, inside(job, 'artifacts/data.zip'),
    domain=prepared.get('domain', 'physics'), max_bytes=task['delivery']['max_data_bytes'],
    modeling_assets=inventory)
proof['inventory_paths'] = sorted(item['name'] for item in inventory)
print(json.dumps(proof, sort_keys=True, separators=(',', ':'), allow_nan=False))
'''
        before = {path.relative_to(self.job).as_posix(): sha256(path)
                  for path in self.job.rglob('*') if path.is_file()}
        model_hash = sha256(result_path.with_name('model.json'))
        interpreter = Path(sys.executable).resolve()
        profile = readonly_profile(self.package, self.job, interpreter)
        process = subprocess.run(['/usr/bin/sandbox-exec', '-p', profile,
            str(interpreter), '-I', '-B', '-c', script, str(self.package), str(self.job),
            self.package_digest, result_hash, model_hash], cwd=self.package,
            env=self.environment, capture_output=True, text=True, timeout=60)
        self.assertEqual(process.returncode, 0, process.stderr or process.stdout)
        proof = json.loads(process.stdout)
        self.assertIs(proof['verified'], True)
        self.assertEqual(proof['schema'], 'pipeline-data/2')
        self.assertEqual(proof['source_result_sha256'], result_hash)
        self.assertEqual(proof['source_model_sha256'], model_hash)
        self.assertEqual(proof['sha256'], sha256(self.job/'artifacts/data.zip'))
        self.assertEqual(proof['bytes'], (self.job/'artifacts/data.zip').stat().st_size)
        with zipfile.ZipFile(self.job/'artifacts/data.zip') as archive:
            self.assertEqual(proof['projected_result_sha256'], hashlib.sha256(archive.read('result.json')).hexdigest())
            self.assertEqual(set(proof['inventory_paths']),
                             {name for name in archive.namelist() if name.startswith('modeling/')})
        self.assertEqual(before, {path.relative_to(self.job).as_posix(): sha256(path)
                                  for path in self.job.rglob('*') if path.is_file()})
        self.assertEqual(digest_tree(self.package), self.package_digest)
        return proof

    def test_import_native_solver_video_data_and_solver_free_rerender(self):
        capabilities = self.call('pipeline_capabilities', {})
        self.assertTrue(capabilities['model_liquefaction']['available'])
        self.assertFalse(capabilities['model_liquefaction']['neural_inference_required'])
        self.prepared_liquid()
        runnable = self.call('physics_simulate', {})
        self.assertTrue(runnable.get('ready_to_run'), runnable)
        process = subprocess.run([sys.executable, str(self.package/'adapter.py'), '--phase', 'run',
            '--task', str(self.job/'task.json')], env=self.environment, capture_output=True, text=True, timeout=75)
        self.assertEqual(process.returncode, 0, process.stderr)
        final = json.loads((self.job/'artifacts/result-manifest.json').read_text())
        self.assertEqual(final['status'], 'succeeded', final)
        self.assertTrue(final['verification']['numerical_passed'], final)
        result_path = self.job/'work/physics/artifacts/result.json'
        result_hash = sha256(result_path)
        result = json.loads(result_path.read_text())
        self.assertEqual(result['trajectory']['diagnostics']['particle_count'], result['plan']['planned_particles'])
        self.assertIn('native-c11', result['trajectory']['diagnostics']['backend'])
        render = json.loads((self.job/'artifacts/render-manifest.json').read_text())
        self.assertTrue(render['media']['all_frames_decoded'])
        self.assertGreater(render['media']['frame_count'], 1)
        with zipfile.ZipFile(self.job/'artifacts/data.zip') as archive:
            self.assertIsNone(archive.testzip())
            exported = json.loads(archive.read('result.json'))
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['schema'], 'pipeline-data/2')
            self.assertEqual(manifest['source_result_sha256'], result_hash)
            self.assertEqual(exported['scene'], result['scene'])
            self.assertEqual(exported['trajectory']['frames'], result['trajectory']['frames'])
            names = archive.namelist()
            self.assertTrue(any(name.startswith('modeling/liquefy/') for name in names), names)
            self.assertTrue(any(name.endswith('/source-mesh.json') and name.startswith('modeling/') for name in names), names)
            self.assertTrue(any(name.endswith('.txt') and name.startswith('modeling/') for name in names), names)
        source_verified = self.verify_portable_projection(result_path, result_hash)
        ready = self.call('pipeline_render_prepare', {'rendering_quality': 'preview'})
        self.assertTrue(ready.get('ready_to_simulate'), ready)
        self.assertFalse(ready['solver_rerun'])
        process = subprocess.run([sys.executable, str(self.package/'adapter.py'), '--phase', 'run',
            '--task', str(self.job/'task.json')], env=self.environment, capture_output=True, text=True, timeout=75)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(sha256(result_path), result_hash)
        state = json.loads((self.job/'work/pipeline/state.json').read_text())
        self.assertFalse(state['solver_rerun'])
        rerender_verified = self.verify_portable_projection(result_path, result_hash)
        self.assertEqual(rerender_verified['sha256'], source_verified['sha256'])
        evidence = os.environ.get('PHY_LIQUID_ACCEPTANCE_OUT')
        if evidence:
            destination = Path(evidence)
            destination.mkdir(parents=True, exist_ok=True)
            for name in ('simulation.mp4', 'data.zip', 'result-manifest.json', 'render-manifest.json'):
                shutil.copyfile(self.job/'artifacts'/name, destination/name)
            shutil.copyfile(result_path, destination/'result.json')
            shutil.copyfile(self.job/'work/prepared-scene.json', destination/'prepared-scene.json')
            save(destination/'acceptance.json', {
                'source': 'synthetic imported cube; no image inference or QQ transport',
                'task_id': self.task['task_id'], 'particle_count': result['plan']['planned_particles'],
                'volume_receipts': result['plan']['liquid_volume_receipts'],
                'quality_gate': final['verification'], 'initial_render_media': render['media'],
                'rerender_media': json.loads((destination/'render-manifest.json').read_text())['media'],
                'solver_result_sha256_before_rerender': result_hash,
                'solver_result_sha256_after_rerender': sha256(result_path),
                'solver_rerun': state['solver_rerun'],
                'video_sha256': sha256(destination/'simulation.mp4'),
                'data_sha256': sha256(destination/'data.zip')})

    def test_changed_import_is_rejected_without_inference_or_source_overwrite(self):
        converted = self.call('modeling_liquefy', self.arguments)
        self.assertTrue(converted.get('ok'), converted)
        raw = self.imported/'raw.json'
        raw.write_text(raw.read_text()+' ')
        failed = self.call('modeling_liquefy', self.arguments)
        self.assertFalse(failed['ok'])
        self.assertTrue(any(word in failed.get('message', '').lower() for word in ('hash', 'changed')), failed)

    def test_missing_extent_and_nonwater_are_explicit_failures(self):
        arguments = dict(self.arguments)
        arguments.pop('physical_extent_m')
        self.assertFalse(self.call('modeling_liquefy', arguments)['ok'])
        self.assertFalse(self.call('modeling_liquefy', {**self.arguments, 'reference_density_kg_m3': 800})['ok'])
        self.assertFalse(self.call('modeling_liquefy', {**self.arguments, 'conversion': 'melting'})['ok'])

    def test_existing_entity_unmentioned_pose_and_velocity_are_preserved(self):
        save(self.job/'work/draft-scene.json', {'entities': [{'id': 'object', 'type': 'mesh',
            'position': [.1, .7, .2], 'velocity': [.01, -.02, .03]}]})
        arguments = {key: value for key, value in self.arguments.items() if key not in ('position', 'velocity')}
        converted = self.call('modeling_liquefy', arguments)
        self.assertTrue(converted.get('ok'), converted)
        entity = json.loads((self.job/converted['entity_path']).read_text())
        self.assertEqual(entity['shape']['center'], [.1, .7, .2])
        self.assertEqual(entity['velocity'], [.01, -.02, .03])


if __name__ == '__main__':
    unittest.main()
