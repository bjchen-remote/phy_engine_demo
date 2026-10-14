"""Real, small CPU geometry exports from sealed current-task model fixtures.

No neural model, network service, video renderer or QQ transport is used. The
child process additionally refuses neural imports during the positive tests.
"""
from __future__ import annotations

import base64
import copy
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from build_pipeline import build
from pipeline import bundle
from modeling_flow.meshes import write_obj
from modeling_flow.contracts import digest, load_request


ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False), encoding='utf-8')


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'pipeline' / filename)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {'bundle': bundle}):
        spec.loader.exec_module(module)
    return module


ADAPTER = load('_cleanup_adapter_test', 'adapter.py')
CLEANUP = load('_cleanup_pipeline_test', 'model_cleanup.py')


def region(n=11):
    vertices = [[x / (n - 1), y / (n - 1), .02 * math.sin(x / (n - 1) * math.pi)
                 + .003 * (-1 if (x + y) % 2 else 1)]
                for y in range(n) for x in range(n)]
    faces = []
    for y in range(n - 1):
        for x in range(n - 1):
            a = y * n + x
            faces.extend([[a, a + 1, a + n], [a + 1, a + n + 1, a + n]])
    start = len(vertices)
    vertices += [[1.2, 1.2, .1], [1.2001, 1.2, .1], [1.2, 1.2001, .1]]
    faces.append([start, start + 1, start + 2])
    return {'vertices': vertices, 'faces': faces}


class PipelineModelCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not all(importlib.util.find_spec(name) for name in ('numpy', 'trimesh')):
            raise RuntimeError('Run cleanup export tests with the installed modeling Python (numpy/trimesh required)')
        cls.package_temp = tempfile.TemporaryDirectory(prefix='cleanup-package-fixture-')
        root = Path(cls.package_temp.name).resolve()
        engine = root / 'engine'
        (engine / 'physics_release/physics_demo').mkdir(parents=True)
        (engine / 'physics_release/physics_demo/runner.py').write_text('# inert composition fixture\n')
        (engine / 'toolbox_adapter.py').write_text('# inert composition fixture\n')
        write(engine / 'toolbox.json', {'schema_version': 1, 'id': 'physics', 'version': '1.4.7',
            'entrypoint': 'toolbox_adapter.py', 'capabilities': ['explicit cleanup composition fixture'],
            'agent_api': {'operations': ['physics_prepare', 'physics_simulate']}})
        cls.package = root / 'package'
        cls.lock = build(engine, cls.package)

    @classmethod
    def tearDownClass(cls):
        cls.package_temp.cleanup()

    def setUp(self):
        import trimesh
        self.trimesh = trimesh
        self.temporary = tempfile.TemporaryDirectory(prefix='cleanup-current-task-')
        self.addCleanup(self.temporary.cleanup)
        self.job = Path(self.temporary.name).resolve()
        (self.job / 'work').mkdir(); (self.job / 'inputs').mkdir()
        self.image = self.job / 'inputs/reference.png'
        self.image.write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aE1cAAAAASUVORK5CYII='))
        config_path = self.job / 'work/modeling-runtime/config.json'
        write(config_path, {'schema_version': 'modeling-flow-config/1',
                            'fixture': 'sealed geometry only; weights are not installed'})
        runtime = {'schema_version': 1, 'python_executable': sys.executable,
            'config_path': config_path.relative_to(self.job).as_posix(),
            'config_sha256': bundle.sha256(config_path), 'provider_notice': 'explicit CPU export fixture'}
        write(self.job / 'modeling-runtime-pin.json', runtime)
        self.task = {'schema_version': 1, 'task_id': 'cpu-cleanup-current-job-fixture',
            'modeling_runtime': runtime,
            'request': {'input_images': [{'id': 'input-image-1', 'path': 'inputs/reference.png',
                'sha256': bundle.sha256(self.image)}]},
            'limits': {'wall_time_seconds': 30, 'max_output_bytes': 16 * 1024 * 1024, 'network': False},
            'delivery': {'data_attachments': True, 'max_data_bytes': 16 * 1024 * 1024}}
        self.guard = self.job / 'work/import-guard'; self.guard.mkdir()
        (self.guard / 'sitecustomize.py').write_text('''import sys
class NoNeuralImports:
 def find_spec(self, fullname, path=None, target=None):
  if fullname == 'modeling_flow.runner' or any(fullname == n or fullname.startswith(n + '.') for n in ('torch','mlx','hy3dmlx','hy3dgen')):
   raise AssertionError('neural import forbidden in a cleanup-only CPU test')
sys.meta_path.insert(0, NoNeuralImports())
''')
        self.reference, self.assets = self.seal()

    def seal(self, *, raw_assets=True, extra_request=None):
        mesh = region()
        request = {'schema_version': 'modeling-flow-preview-request/1', 'image_path': str(self.image),
                   'scale_axis': 'max', 'seed': 17, 'num_inference_steps': 30,
                   'octree_resolution': 128, **(extra_request or {})}
        reference = ADAPTER.object_hash({'request': request, 'image_sha256': bundle.sha256(self.image),
            'runtime_sha256': self.task['modeling_runtime']['config_sha256'],
            'module': self.lock['modules']['modeling']})
        directory = self.job / 'work/modeling-images' / reference
        assets = directory / 'assets'; assets.mkdir(parents=True)
        write(directory / 'request.json', request)
        document = {'schema_version': 'modeling-flow-display/1', 'units': 'model_unit', **mesh,
            'scale': {'units': 'model_unit', 'normalized_extent': 1., 'physical_extent_m': None},
            'image_sha256': bundle.sha256(self.image), 'physical_accuracy': 'unverified',
            'assumptions': ['explicit sealed triangle fixture, no inferred anatomy']}
        for prefix in ('display', 'raw') if raw_assets else ('display',):
            write(assets / (prefix + '_mesh.json'), document)
            write_obj(assets / (prefix + '.obj'), mesh, 'model_unit')
            self.trimesh.Trimesh(vertices=mesh['vertices'], faces=mesh['faces'], process=False).export(
                str(assets / (prefix + '.glb')), file_type='glb')
        receipt = {'schema_version': 'modeling-flow-receipt/1', 'purpose': 'model_preview',
            'display_only': True, 'ready_to_simulate': False,
            'configuration_file_sha256': self.task['modeling_runtime']['config_sha256'],
            'simulation_performed': False, 'numerical_usable': False,
            'image': {'sha256': bundle.sha256(self.image)},
            'sampling': {'seed': 17, 'num_inference_steps': 30, 'octree_resolution': 128},
            'artifacts': [{'path': p.name, 'sha256': bundle.sha256(p), 'bytes': p.stat().st_size}
                          for p in assets.iterdir()]}
        try:
            receipt['request_sha256'] = digest(load_request(request))
        except ValueError:
            receipt['request_sha256'] = digest(request)  # Deliberately invalid saved-request fixtures.
        write(assets / 'receipt.json', receipt)
        public = {'ok': True, 'result_kind': 'model-preview', 'model_ref': reference,
                  'ready_to_preview': True, 'ready_to_simulate': False, 'simulation_performed': False}
        write(directory / 'response.json', {'public_result': public, 'artifact_pins': [
            {'path': p.relative_to(self.job).as_posix(), 'sha256': bundle.sha256(p)}
            for p in assets.iterdir()]})
        return reference, assets

    def run_cleanup(self, **arguments):
        args = {'model_ref': self.reference, **arguments}
        with mock.patch.dict(os.environ, {'PYTHONPATH': str(self.guard)}), \
                mock.patch.object(CLEANUP.subprocess, 'run', wraps=subprocess.run) as worker:
            result = CLEANUP.cleanup_model(self.package, self.job, self.task, self.lock,
                                          args, ADAPTER.atomic, ADAPTER.object_hash)
        return result, worker

    def test_composed_package_advertises_current_model_cleanup_without_neural_inference(self):
        self.assertTrue((self.package / 'model_cleanup.py').is_file())
        operations = bundle.read_json(self.package / 'toolbox.json')['agent_api']['operations']
        self.assertIn('modeling_preview_cleanup', operations)
        capabilities = ADAPTER.capabilities(self.lock, self.task['modeling_runtime'])['model_preview']
        self.assertEqual(capabilities['cleanup_operation'], 'modeling_preview_cleanup')
        self.assertFalse(capabilities['cleanup_needs_neural_inference'])

    def test_actual_surface_export_preserves_raw_bytes_and_never_imports_neural_modules(self):
        original = {p.name: bundle.sha256(p) for p in self.assets.iterdir()}
        result, worker = self.run_cleanup()
        self.assertTrue(result['ok']); self.assertFalse(result['neural_inference_performed'])
        self.assertNotEqual(result['model_ref'], self.reference)
        self.assertTrue(result['display_vertices_modified'])
        self.assertEqual(worker.call_count, 1)
        command = worker.call_args.args[0]
        self.assertEqual(command[:3], [sys.executable, str(self.package / 'model_cleanup.py'), '--export'])
        self.assertNotIn('generate', command)
        self.assertEqual(worker.call_args.kwargs['stderr'], subprocess.DEVNULL)
        self.assertEqual(worker.call_args.kwargs['env']['HF_HUB_OFFLINE'], '1')
        self.assertEqual(original, {p.name: bundle.sha256(p) for p in self.assets.iterdir()})
        derived = self.job / 'work/modeling-images' / result['model_ref']
        new_assets = derived / 'assets'
        self.assertEqual(bundle.sha256(new_assets / 'raw_mesh.json'), original['raw_mesh.json'])
        request = bundle.read_json(derived / 'request.json')
        old = bundle.read_json(self.assets.parent / 'request.json')
        self.assertEqual(request, {**old, 'display_cleanup': 'surface'})
        receipt = bundle.read_json(new_assets / 'receipt.json')
        self.assertEqual(receipt['sampling']['seed'], 17)
        self.assertFalse(receipt['postprocessing']['neural_inference_performed'])
        self.assertEqual(receipt['postprocessing']['execution_device'], 'cpu')
        self.assertEqual(receipt['postprocessing']['source_model_ref'], self.reference)
        self.assertFalse(receipt['semantic_fidelity_verified'])
        self.assertEqual(result['next_action'], {'tool': 'modeling_preview_render',
                                               'model_ref': result['model_ref']})
        self.assertLess((derived / 'cleanup.log').stat().st_size, 512)
        import numpy as np
        for prefix in ('raw', 'display'):
            expected = bundle.read_json(new_assets / (prefix + '_mesh.json'), 128_000_000)
            actual = self.trimesh.load(str(new_assets / (prefix + '.glb')), force='mesh', process=False)
            np.testing.assert_array_equal(actual.faces, expected['faces'])
            np.testing.assert_array_equal(actual.vertices, np.asarray(expected['vertices'], dtype=np.float32))

    def test_cache_is_byte_checked_and_does_not_execute_another_export(self):
        first, _ = self.run_cleanup()
        directory = self.job / 'work/modeling-images' / first['model_ref']
        before = {p.relative_to(directory).as_posix(): bundle.sha256(p)
                  for p in directory.rglob('*') if p.is_file()}
        reused, worker = self.run_cleanup()
        worker.assert_not_called(); self.assertTrue(reused['cleanup_reused'])
        self.assertEqual(reused['model_ref'], first['model_ref'])
        self.assertEqual(before, {p.relative_to(directory).as_posix(): bundle.sha256(p)
                                  for p in directory.rglob('*') if p.is_file()})
        path = directory / 'assets/display_mesh.json'; path.write_bytes(path.read_bytes() + b' ')
        with mock.patch.object(CLEANUP.subprocess, 'run') as worker, self.assertRaisesRegex(ValueError, 'changed'):
            CLEANUP.cleanup_model(self.package, self.job, self.task, self.lock,
                {'model_ref': self.reference}, ADAPTER.atomic, ADAPTER.object_hash)
        worker.assert_not_called()

    def test_legacy_v1_display_mesh_is_preserved_as_raw_source_without_rerunning_inference(self):
        self.reference, self.assets = self.seal(raw_assets=False, extra_request={'seed': 19})
        old = (self.assets / 'display_mesh.json').read_bytes()
        result, _ = self.run_cleanup(display_cleanup='surface')
        derived = self.job / 'work/modeling-images' / result['model_ref'] / 'assets'
        self.assertEqual((derived / 'raw_mesh.json').read_bytes(), old)
        self.assertTrue((derived / 'raw.glb').is_file()); self.assertTrue((derived / 'raw.obj').is_file())

    def test_none_and_conservative_modes_have_separate_refs_and_use_original_raw(self):
        refs = []
        for mode in ('none', 'conservative'):
            result, worker = self.run_cleanup(display_cleanup=mode)
            self.assertEqual(worker.call_count, 1)
            self.assertEqual(result['display_cleanup']['mode'], mode)
            self.assertFalse(result['display_vertices_modified'])
            raw = self.job / 'work/modeling-images' / result['model_ref'] / 'assets/raw_mesh.json'
            self.assertEqual(raw.read_bytes(), (self.assets / 'raw_mesh.json').read_bytes())
            refs.append(result['model_ref'])
        self.assertEqual(len(set(refs)), 2)

    def test_source_request_receipt_image_config_or_runtime_changes_fail_before_export(self):
        targets = [self.assets / 'raw_mesh.json', self.assets / 'receipt.json',
                   self.assets.parent / 'request.json', self.image,
                   self.job / self.task['modeling_runtime']['config_path'],
                   self.job / 'modeling-runtime-pin.json']
        for path in targets:
            before = path.read_bytes(); path.write_bytes(before + b' ')
            if path == self.assets.parent / 'request.json':
                value = json.loads(before); value['seed'] = 18; write(path, value)
            if path == self.job / 'modeling-runtime-pin.json':
                value = json.loads(before); value['python_executable'] = '/arbitrary/worker'; write(path, value)
            with self.subTest(path=path.name), mock.patch.object(CLEANUP.subprocess, 'run') as worker, \
                    self.assertRaises(ValueError):
                CLEANUP.cleanup_model(self.package, self.job, self.task, self.lock,
                    {'model_ref': self.reference}, ADAPTER.atomic, ADAPTER.object_hash)
            worker.assert_not_called(); path.write_bytes(before)

    def test_arbitrary_arguments_identifiers_unknown_mode_and_unadvertised_cap_fail_early(self):
        variants = [{'model_ref': '../other/model'}, {'model_ref': 'https://example.com/model'},
            {'model_ref': True}, {'model_ref': 'b' * 64}, {'path': str(self.assets)},
            {'model_ref': self.reference, 'command': 'never'},
            {'model_ref': self.reference, 'display_cleanup': 'unbounded'},
            {'model_ref': self.reference, 'display_cleanup': True}]
        for arguments in variants:
            with self.subTest(arguments=arguments), mock.patch.object(CLEANUP.subprocess, 'run') as worker, \
                    self.assertRaises((ValueError, OSError)):
                CLEANUP.cleanup_model(self.package, self.job, self.task, self.lock,
                                      arguments, ADAPTER.atomic, ADAPTER.object_hash)
            worker.assert_not_called()
        changed = copy.deepcopy(self.lock); changed['modules']['modeling'].pop('surface_cleanup')
        with mock.patch.object(CLEANUP.subprocess, 'run') as worker, self.assertRaisesRegex(ValueError, 'advertised'):
            CLEANUP.cleanup_model(self.package, self.job, self.task, changed,
                {'model_ref': self.reference}, ADAPTER.atomic, ADAPTER.object_hash)
        worker.assert_not_called()

    def test_previous_request_cannot_add_commands_or_physical_simulation_fields(self):
        for extra in ({'command': 'never'}, {'material': {'density_kg_m3': 1000}},
                      {'schema_version': 'modeling-flow-request/1'}, {'scale_axis': 'x'}):
            reference, _ = self.seal(extra_request={**extra, 'seed': 123 + len(extra)})
            with self.subTest(extra=extra), mock.patch.object(CLEANUP.subprocess, 'run') as worker, \
                    self.assertRaisesRegex(ValueError, 'bounded model-preview request'):
                CLEANUP.cleanup_model(self.package, self.job, self.task, self.lock,
                    {'model_ref': reference}, ADAPTER.atomic, ADAPTER.object_hash)
            worker.assert_not_called()

    def test_changed_module_pin_never_promotes_existing_geometry(self):
        changed = copy.deepcopy(self.lock); changed['modules']['modeling']['digest'] = 'd' * 64
        with mock.patch.object(CLEANUP.subprocess, 'run') as worker, self.assertRaisesRegex(ValueError, 'bundle'):
            CLEANUP.cleanup_model(self.package, self.job, self.task, changed,
                {'model_ref': self.reference}, ADAPTER.atomic, ADAPTER.object_hash)
        worker.assert_not_called()

    def test_resealed_receipt_cannot_omit_artifacts_duplicate_pins_or_change_original_request_digest(self):
        receipt_path = self.assets / 'receipt.json'
        response_path = self.assets.parent / 'response.json'
        original_receipt = receipt_path.read_bytes(); original_response = response_path.read_bytes()
        for change in ('empty', 'missing_raw', 'duplicate', 'request_digest'):
            receipt = json.loads(original_receipt)
            if change == 'empty': receipt['artifacts'] = []
            elif change == 'missing_raw':
                receipt['artifacts'] = [a for a in receipt['artifacts'] if a['path'] != 'raw_mesh.json']
            elif change == 'duplicate': receipt['artifacts'].append(receipt['artifacts'][0])
            else: receipt['request_sha256'] = 'e' * 64
            write(receipt_path, receipt)
            response = json.loads(original_response)
            for pin in response['artifact_pins']:
                if pin['path'] == receipt_path.relative_to(self.job).as_posix():
                    pin['sha256'] = bundle.sha256(receipt_path)
            write(response_path, response)
            with self.subTest(change=change), mock.patch.object(CLEANUP.subprocess, 'run') as worker, \
                    self.assertRaises(ValueError):
                CLEANUP.cleanup_model(self.package, self.job, self.task, self.lock,
                    {'model_ref': self.reference}, ADAPTER.atomic, ADAPTER.object_hash)
            worker.assert_not_called()
        receipt_path.write_bytes(original_receipt); response_path.write_bytes(original_response)

    def test_special_cleanup_lock_is_rejected_before_opening_or_exporting(self):
        os.mkfifo(self.job / 'work/model-cleanup.lock')
        with mock.patch.object(CLEANUP.subprocess, 'run') as worker, self.assertRaisesRegex(
                ValueError, 'regular file'):
            CLEANUP.cleanup_model(self.package, self.job, self.task, self.lock,
                {'model_ref': self.reference}, ADAPTER.atomic, ADAPTER.object_hash)
        worker.assert_not_called()

    def test_processed_source_cannot_claim_legacy_fallback_after_removing_raw_assets(self):
        self.reference, self.assets = self.seal(raw_assets=False, extra_request={'seed': 23})
        receipt_path = self.assets / 'receipt.json'
        receipt = bundle.read_json(receipt_path)
        receipt['display_cleanup'] = {'mode': 'conservative', 'applied': True,
                                      'policy_id': 'bounded-floaters/1', 'summary': {}}
        write(receipt_path, receipt)
        response_path = self.assets.parent / 'response.json'
        response = bundle.read_json(response_path)
        for pin in response['artifact_pins']:
            if pin['path'] == receipt_path.relative_to(self.job).as_posix():
                pin['sha256'] = bundle.sha256(receipt_path)
        write(response_path, response)
        with mock.patch.object(CLEANUP.subprocess, 'run') as worker, self.assertRaisesRegex(
                ValueError, 'original raw mesh'):
            CLEANUP.cleanup_model(self.package, self.job, self.task, self.lock,
                {'model_ref': self.reference}, ADAPTER.atomic, ADAPTER.object_hash)
        worker.assert_not_called()

    def test_incomplete_attempt_is_retained_and_never_replayed(self):
        request = {**bundle.read_json(self.assets.parent / 'request.json'), 'display_cleanup': 'surface'}
        ref = ADAPTER.object_hash({'request': request, 'image_sha256': bundle.sha256(self.image),
            'runtime_sha256': self.task['modeling_runtime']['config_sha256'],
            'module': self.lock['modules']['modeling']})
        directory = self.job / 'work/modeling-images' / ref; directory.mkdir()
        (directory / 'retained.txt').write_text('incomplete CPU export evidence')
        result, worker = self.run_cleanup()
        worker.assert_not_called(); self.assertEqual(result['code'], 'model_cleanup_incomplete')
        self.assertEqual((directory / 'retained.txt').read_text(), 'incomplete CPU export evidence')

    def test_adapter_cleanup_dispatch_never_calls_inference_or_video_stage(self):
        write(self.job / 'work/toolbox-call.json', {'operation': 'modeling_preview_cleanup',
            'arguments': {'model_ref': self.reference, 'display_cleanup': 'surface'}})
        with mock.patch.dict(sys.modules, {'model_cleanup': CLEANUP}), \
                mock.patch.object(ADAPTER, 'image_module', side_effect=AssertionError('no inference')), \
                mock.patch.object(ADAPTER, 'stage', side_effect=AssertionError('no rendering')), \
                mock.patch.dict(os.environ, {'PYTHONPATH': str(self.guard)}):
            ADAPTER.api(self.package, self.package / 'engine', self.job, self.task, self.lock)
        result = bundle.read_json(self.job / 'work/toolbox-response.json')['result']
        self.assertTrue(result['ok']); self.assertFalse(result['neural_inference_performed'])


if __name__ == '__main__':
    unittest.main()
