"""Image admission, backend receipts and engine gates, with no ML execution."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


TOOLBOXES = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLBOXES))
from pipeline import bundle

_spec = importlib.util.spec_from_file_location('_pipeline_image_modeling_test',
                                             TOOLBOXES / 'pipeline/image_modeling.py')
image_modeling = importlib.util.module_from_spec(_spec)
_adapter_spec = importlib.util.spec_from_file_location('_pipeline_image_adapter_test',
                                                     TOOLBOXES / 'pipeline/adapter.py')
adapter = importlib.util.module_from_spec(_adapter_spec)
with patch.dict(sys.modules, {'bundle': bundle}):
    _spec.loader.exec_module(image_modeling)
    _adapter_spec.loader.exec_module(adapter)


save = adapter.atomic
object_hash = adapter.object_hash


class ImageModelingGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='pipeline-image-gate-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory / 'package'
        self.job = self.directory / 'job'
        (self.root / 'modules/modeling').mkdir(parents=True)
        save(self.root / 'modules/modeling/module.json', {'version': '0.2.1'})
        (self.root / 'modules/modeling/flow_runner.py').write_text('# never executed\n')
        (self.job / 'inputs').mkdir(parents=True)
        self.input = self.job / 'inputs/fixture.png'
        self.input.write_bytes(b'synthetic image bytes; backend is mocked')
        config = self.job / 'work/modeling-runtime/config.json'
        save(config, {'schema_version': 'modeling-flow-config/1'})
        self.task = {'request': {'input_images': [{'id': 'image-1',
            'path': 'inputs/fixture.png', 'sha256': bundle.sha256(self.input)}]},
            'limits': {'wall_time_seconds': 180},
            'modeling_runtime': {'python_executable': '/never/execute/python',
                'config_path': 'work/modeling-runtime/config.json',
                'config_sha256': bundle.sha256(config),
                'provider_notice': 'Synthetic Test operator; no Tencent affiliation.'}}
        self.lock = {'modules': {'modeling': {'path': 'modules/modeling', 'digest': 'test-pin'}}}
        self.arguments = {'image_id': 'image-1', 'physical_extent_m': 0.2,
            'material': {'name': 'explicit assumption', 'density_kg_m3': 1000}}
        self.generated = {'vertices': [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
                          'faces': [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]],
                          'audit': {'volume': 0.01}}
        self.context = ExitStack()
        self.addCleanup(self.context.close)
        self.engine_call = Mock(return_value={'ok': True, 'mesh_json': json.dumps({
            'vertices': self.generated['vertices'], 'triangles': self.generated['faces']}),
            'audit': {'connected_components': 1}})
        api = types.ModuleType('physics_demo.api')
        api.call_tool = self.engine_call
        self.context.enter_context(patch.dict(sys.modules, {
            'physics_demo': types.ModuleType('physics_demo'), 'physics_demo.api': api}))
        self.output = None

    def call(self, task=None, arguments=None):
        return image_modeling.generate_image_model(self.root, self.job, task or self.task,
            self.lock, arguments or self.arguments, save, object_hash)

    def backend(self, command, **kwargs):
        output = Path(command[command.index('--output') + 1])
        output.mkdir()
        self.output = output
        vertices, faces = self.generated['vertices'], self.generated['faces']
        positions = struct.pack('<' + 'f' * (3 * len(vertices)),
                                *(coordinate for vertex in vertices for coordinate in vertex))
        indices = struct.pack('<' + 'H' * (3 * len(faces)),
                              *(index for face in faces for index in face))
        binary = positions + indices
        gltf = {'asset': {'version': '2.0', 'generator': 'Synthetic tetrahedron test fixture'},
            'scene': 0, 'scenes': [{'nodes': [0]}], 'nodes': [{'mesh': 0}],
            'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1}]}],
            'buffers': [{'byteLength': len(binary)}],
            'bufferViews': [{'buffer': 0, 'byteOffset': 0, 'byteLength': len(positions),
                             'target': 34962},
                            {'buffer': 0, 'byteOffset': len(positions),
                             'byteLength': len(indices), 'target': 34963}],
            'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': len(vertices),
                           'type': 'VEC3', 'min': [0, 0, 0], 'max': [1, 1, 1]},
                          {'bufferView': 1, 'componentType': 5123, 'count': 3 * len(faces),
                           'type': 'SCALAR'}]}
        gltf_json = json.dumps(gltf, separators=(',', ':')).encode()
        gltf_json += b' ' * (-len(gltf_json) % 4)
        glb = (struct.pack('<III', 0x46546C67, 2, 28 + len(gltf_json) + len(binary)) +
               struct.pack('<II', len(gltf_json), 0x4E4F534A) + gltf_json +
               struct.pack('<II', len(binary), 0x004E4942) + binary)
        (output / 'display.glb').write_bytes(glb)
        obj = '\n'.join(['# Synthetic tetrahedron, not a model quality test'] +
            ['v ' + ' '.join(map(str, vertex)) for vertex in vertices] +
            ['f ' + ' '.join(str(index + 1) for index in face) for face in faces]) + '\n'
        for name in ('display.obj', 'simulation.obj'):
            (output / name).write_text(obj)
        save(output / 'display_mesh.json', self.generated)
        save(output / 'simulation_mesh.json', self.generated)
        artifacts = [{'path': p.name, 'sha256': bundle.sha256(p)} for p in output.iterdir()]
        save(output / 'receipt.json', {'schema_version': 'modeling-flow-receipt/1',
            'artifacts': artifacts, 'simulation_audit': {'connected_components': 1}})
        json.dump({'ok': True}, kwargs['stdout'])
        return subprocess.CompletedProcess(command, 0)

    def test_missing_runtime_fails_before_any_process(self):
        with patch.object(image_modeling.subprocess, 'run') as execute:
            result = self.call(task={**self.task, 'modeling_runtime': None})
        self.assertEqual(result['code'], 'image_modeling_not_configured')
        execute.assert_not_called()

    def test_attachment_change_is_rejected_before_execution(self):
        self.input.write_bytes(b'changed after admission')
        with patch.object(image_modeling.subprocess, 'run') as execute:
            with self.assertRaisesRegex(ValueError, 'changed after admission'):
                self.call()
        execute.assert_not_called()

    def test_prompt_cannot_select_operator_paths(self):
        for key in ('image_path', 'python_executable', 'upstream_root', 'weights_root'):
            with self.subTest(key=key), patch.object(image_modeling.subprocess, 'run') as execute:
                with self.assertRaisesRegex(ValueError, 'attachment IDs'):
                    self.call(arguments={**self.arguments, key: '/foreign/path'})
                execute.assert_not_called()

    def test_receipt_artifact_change_is_rejected(self):
        def changed(command, **kwargs):
            completed = self.backend(command, **kwargs)
            (self.output / 'display.glb').write_bytes(b'tampered')
            return completed
        with patch.object(image_modeling.subprocess, 'run', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'differs from receipt'):
                self.call()
        self.engine_call.assert_not_called()

    def test_engine_gate_is_required_and_does_not_claim_prepared_scene(self):
        with patch.object(image_modeling.subprocess, 'run', side_effect=self.backend) as execute:
            result = self.call()
        self.assertTrue(result['geometry_verified'])
        self.assertFalse(result['ready_to_simulate'])
        self.assertEqual(result['next_action']['tool'], 'physics_patch')
        self.assertEqual(result['mass_estimate_kg'], 10)
        self.assertEqual(result['provider_notice'], self.task['modeling_runtime']['provider_notice'])
        self.assertTrue((self.job / 'work/mesh-assets' / (result['mesh_ref'] + '.json')).is_file())
        self.engine_call.assert_called_once()
        self.assertEqual(self.engine_call.call_args.args[0], 'physics_mesh')
        self.assertEqual(execute.call_args.kwargs['timeout'], 180)
        self.assertEqual(execute.call_args.kwargs['env']['HF_HUB_OFFLINE'], '1')

    def test_cached_artifact_change_never_restarts_inference(self):
        with patch.object(image_modeling.subprocess, 'run', side_effect=self.backend) as execute:
            first = self.call()
            self.assertEqual(self.call(), first)
            self.assertEqual(execute.call_count, 1)
            (self.output / 'display.glb').write_bytes(b'changed cached mesh')
            with self.assertRaisesRegex(ValueError, 'saved image model changed'):
                self.call()
            self.assertEqual(execute.call_count, 1)

    def test_sorted_atomic_mesh_reference_survives_original_physics_patch_contract(self):
        # The engine's insertion order differs from the adapter's on-disk order.
        # Nested metadata and Unicode also pass through the original verifier.
        mesh = {'vertices': self.generated['vertices'],
                'triangles': self.generated['faces'],
                'metadata': {'provenance': {'notes': '测试来源', 'method': 'synthetic'},
                             'length_unit': 'm'}}
        original_encoded = json.dumps(mesh, ensure_ascii=False, separators=(',', ':'),
                                      allow_nan=False)
        self.engine_call.return_value = {'ok': True, 'mesh_json': json.dumps(mesh),
                                        'audit': {'connected_components': 1}}
        with patch.object(image_modeling.subprocess, 'run', side_effect=self.backend):
            result = self.call()
        reference = result['mesh_ref']
        self.assertNotEqual(reference, hashlib.sha256(original_encoded.encode()).hexdigest())
        mesh_path = self.job / 'work/mesh-assets' / (reference + '.json')
        saved_mesh = json.loads(mesh_path.read_text())
        self.assertEqual(list(saved_mesh), sorted(mesh))

        # Use both original runtime modules, while restoring run_simulation's
        # import-time engine path so this test cannot select an installed engine.
        runtime_spec = importlib.util.spec_from_file_location('_physics_run_simulation_test',
            TOOLBOXES / 'physics/run_simulation.py')
        runtime = importlib.util.module_from_spec(runtime_spec)
        with patch.object(sys, 'path', list(sys.path)):
            runtime_spec.loader.exec_module(runtime)
        modeling_spec = importlib.util.spec_from_file_location('_physics_modeling_test',
            TOOLBOXES / 'physics/modeling.py')
        physics_modeling = importlib.util.module_from_spec(modeling_spec)
        with patch.dict(sys.modules, {'run_simulation': runtime}):
            modeling_spec.loader.exec_module(physics_modeling)

        encoded = physics_modeling._mesh_value(self.job, reference)
        self.assertEqual(json.loads(encoded), mesh)
        self.assertEqual(hashlib.sha256(encoded.encode()).hexdigest(), reference)
        scene = {'entities': [{'id': 'toy'}]}
        save(self.job / 'work/toolbox-call.json', {'operation': 'physics_patch',
            'arguments': {'scene_json': json.dumps(scene), 'operations': [{
                'op': 'add', 'path': '/entities/@toy/mesh', 'mesh_ref': reference}]}})
        self.engine_call.reset_mock()
        self.engine_call.return_value = {'ok': True, 'scene_json': json.dumps(scene)}
        physics_modeling.api_call(self.root, self.job, self.task)
        self.engine_call.assert_called_once_with('physics_patch', {
            'scene_json': json.dumps(scene), 'operations': [{
                'op': 'add', 'path': '/entities/@toy/mesh', 'value_json': encoded}]},
            unlimited=False)
        response = json.loads((self.job / 'work/toolbox-response.json').read_text())
        self.assertTrue(response['result']['scene_saved'])
        self.assertEqual(json.loads((self.job / 'work/draft-scene.json').read_text()), scene)
        save(mesh_path, {**saved_mesh, 'vertices': [[0, 0, 0]]})
        with self.assertRaisesRegex(ValueError, 'mesh_ref integrity check failed'):
            physics_modeling._mesh_value(self.job, reference)

    def test_visual_mesh_survives_physics_rejection_without_discarding_parts(self):
        def disconnected(command, **kwargs):
            completed = self.backend(command, **kwargs)
            (self.output / 'simulation_mesh.json').unlink()
            save(self.output / 'receipt.json', {'schema_version': 'modeling-flow-receipt/1',
                'simulation_audit': {'connected_components': 92},
                'artifacts': [{'path': p.name, 'sha256': bundle.sha256(p)}
                    for p in self.output.iterdir() if p.name != 'receipt.json']})
            return subprocess.CompletedProcess(command, 2)
        with patch.object(image_modeling.subprocess, 'run', side_effect=disconnected):
            result = self.call()
        self.assertFalse(result['ready_to_simulate'])
        self.assertEqual(result['audit']['connected_components'], 92)
        self.assertNotIn('mesh_ref', result)
        self.engine_call.assert_not_called()
        for key in ('display_model_ref', 'receipt_ref'):
            self.assertIn(key, result)
            self.assertRegex(result[key], r'^[0-9a-f]{64}$')
            self.assertEqual(result[key], result['model_ref'])
        self.assertTrue(result['display_geometry_available'])
        self.assertTrue((self.output / 'display_mesh.json').is_file())
        self.assertTrue((self.output / 'receipt.json').is_file())

    def test_geometry_export_requires_scene_receipt_provenance(self):
        with patch.object(image_modeling.subprocess, 'run', side_effect=self.backend):
            result = self.call()
        self.assertEqual(image_modeling.used_modeling_assets(self.job, {'entities': []}), [])
        receipt_sha = bundle.sha256(self.output / 'receipt.json')
        model = {'entities': [{'mesh': {'metadata': {'provenance': {
            'notes': 'model receipt SHA256=' + receipt_sha}}}}]}
        exports = image_modeling.used_modeling_assets(self.job, model)
        self.assertEqual(len(exports), 6)
        self.assertEqual({Path(item['name']).name for item in exports},
                         {'receipt.json', 'display_mesh.json', 'simulation_mesh.json',
                          'display.glb', 'display.obj', 'simulation.obj'})
        for item in exports:
            self.assertEqual(item['sha256'], bundle.sha256(Path(item['path'])))
            self.assertTrue(item['name'].startswith('modeling/' + result['model_ref'] + '/'))

    def test_geometry_export_revalidates_artifacts_after_modeling(self):
        with patch.object(image_modeling.subprocess, 'run', side_effect=self.backend):
            self.call()
        receipt_sha = bundle.sha256(self.output / 'receipt.json')
        model = {'entities': [{'mesh': {'metadata': {'provenance': {'notes': receipt_sha}}}}]}
        for name in ('display_mesh.json', 'simulation_mesh.json',
                     'display.glb', 'display.obj', 'simulation.obj'):
            with self.subTest(name=name):
                path = self.output / name
                original = path.read_bytes()
                try:
                    path.write_bytes(b'changed after modeling')
                    with self.assertRaisesRegex(ValueError, 'provenance changed before export'):
                        image_modeling.used_modeling_assets(self.job, model)
                finally:
                    path.write_bytes(original)

    def test_geometry_export_includes_all_modeling_licenses_only_for_used_model(self):
        license_names = {'Hunyuan3D-LICENSE.txt', 'MLX-port-LICENSE.txt',
                         'U2Net-APACHE-2.0.txt', 'rembg-MIT.txt', 'PyMeshLab-GPL-3.0.txt'}
        license_directory = self.root / 'modules/modeling/modeling_flow/third_party'
        license_directory.mkdir(parents=True)
        for name in license_names:
            (license_directory / name).write_text('Synthetic test license fixture: ' + name)
        with patch.object(image_modeling.subprocess, 'run', side_effect=self.backend):
            self.call()
        self.assertEqual(image_modeling.used_modeling_assets(self.job,
                         {'entities': []}, root=self.root), [])
        receipt_sha = bundle.sha256(self.output / 'receipt.json')
        model = {'entities': [{'mesh': {'metadata': {'provenance': {'notes': receipt_sha}}}}]}
        exports = image_modeling.used_modeling_assets(self.job, model, root=self.root)
        self.assertEqual(len(exports), 11)
        licenses = [item for item in exports if item['name'].startswith('modeling/licenses/')]
        self.assertEqual({Path(item['name']).name for item in licenses}, license_names)
        for item in licenses:
            self.assertEqual(Path(item['path']).parent, license_directory)
            self.assertEqual(item['sha256'], bundle.sha256(Path(item['path'])))

    def test_legacy_pinned_modeling_stage_exports_its_four_original_licenses(self):
        license_names = {'Hunyuan3D-LICENSE.txt', 'MLX-port-LICENSE.txt',
                         'U2Net-APACHE-2.0.txt', 'rembg-MIT.txt'}
        module = self.root / 'modules/modeling'
        save(module / 'module.json', {'version': '0.2.0'})
        directory = module / 'modeling_flow/third_party'
        directory.mkdir(parents=True)
        for name in license_names:
            (directory / name).write_text('Synthetic legacy license: ' + name)
        with patch.object(image_modeling.subprocess, 'run', side_effect=self.backend):
            self.call()
        receipt_sha = bundle.sha256(self.output / 'receipt.json')
        model = {'entities': [{'mesh': {'metadata': {'provenance': {'notes': receipt_sha}}}}]}
        exports = image_modeling.used_modeling_assets(self.job, model, root=self.root)
        self.assertEqual(len(exports), 10)
        self.assertEqual({Path(item['name']).name for item in exports
                          if item['name'].startswith('modeling/licenses/')}, license_names)
        for version in ('0.2.1', 'custom-stage'):
            with self.subTest(version=version):
                save(module / 'module.json', {'version': version})
                with self.assertRaises(FileNotFoundError):
                    image_modeling.used_modeling_assets(self.job, model, root=self.root)
        save(module / 'module.json', {'version': '0.2.0'})
        (module / 'modeling_flow/reduction.py').write_text('# synthetic custom reducer marker')
        with self.assertRaises(FileNotFoundError):
            image_modeling.used_modeling_assets(self.job, model, root=self.root)

    def test_engine_rejection_cannot_create_usable_mesh_reference(self):
        self.engine_call.return_value = {'ok': False, 'code': 'self_intersection'}
        with patch.object(image_modeling.subprocess, 'run', side_effect=self.backend):
            result = self.call()
        self.assertEqual(result['code'], 'engine_mesh_rejected')
        self.assertFalse(result['ready_to_simulate'])
        self.assertNotIn('mesh_ref', result)
        self.assertFalse((self.job / 'work/mesh-assets').exists())

    def test_timeout_retains_attempt_and_refuses_unreviewed_replay(self):
        with patch.object(image_modeling.subprocess, 'run',
                          side_effect=subprocess.TimeoutExpired(['synthetic'], 180)) as execute:
            with self.assertRaises(subprocess.TimeoutExpired):
                self.call()
            result = self.call()
            self.assertEqual(result['code'], 'image_modeling_incomplete')
            self.assertEqual(execute.call_count, 1)


if __name__ == '__main__':
    unittest.main()
