"""Synthetic task-scoped liquefy assets; no ML, network, service or QQ access."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLBOXES = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLBOXES))
from pipeline import bundle
from physics_demo.core.meshes import build_mesh
from modeling_flow import contracts

spec = importlib.util.spec_from_file_location('_test_liquefy', TOOLBOXES / 'pipeline/liquefy.py')
liquefy = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'bundle': bundle}):
    spec.loader.exec_module(liquefy)


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False) + '\n')


def object_hash(value):
    # The existing adapter hash deliberately retains its legacy ASCII escaping.
    return hashlib.sha256(json.dumps(value, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


class LiquefyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='liquefy-contract-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.root = self.directory / 'package'
        self.job = self.directory / 'job'
        (self.job / 'work').mkdir(parents=True)
        self.task = {'task_id': '任务一', 'request': {'input_images': []}}
        self.lock = {'modules': {role: {'id': role, 'version': '1', 'digest': str(index) * 64,
            'path': 'modules/' + role} for index, role in enumerate(('modeling', 'simulation', 'rendering'), 1)}}
        mesh = build_mesh({'type': 'box', 'size': [2, 1, 1], 'subdivisions': 1})
        self.raw = {'vertices': mesh['vertices'], 'faces': mesh['triangles'], 'units': 'model_unit'}
        self.arguments = {'physical_extent_m': 0.4, 'entity_id': 'water-object',
            'position': [0, 0.5, 0], 'velocity': [0, 0, 0], 'spacing_m': 0.04}

    def imported(self, raw=None, producer=None):
        staging = self.job / 'work/import-staging'
        staging.mkdir(exist_ok=True)
        atomic(staging / 'raw.json', self.raw if raw is None else raw)
        (staging / 'LICENSE.txt').write_text('Synthetic fixture license\n')
        members = [{'path': name, 'role': role, 'sha256': bundle.sha256(staging / name),
            'size_bytes': (staging / name).stat().st_size}
            for name, role in [('raw.json', 'raw_mesh'), ('LICENSE.txt', 'license')]]
        manifest = {'schema_version': 'model-asset-import/1', 'task_id': self.task['task_id'],
            'asset_id': 'a' * 64, 'source_model_ref': 'b' * 64,
            'producer': producer or {'id': 'older-modeler', 'version': '0.3.3', 'digest': 'c' * 64,
                'private_path': '/Users/synthetic/private-runtime'}, 'members': members}
        reference = liquefy._hash({key: manifest[key] for key in
            ('task_id', 'asset_id', 'source_model_ref', 'producer', 'members')})
        manifest['imported_asset_ref'] = reference
        directory = self.job / 'work/imported-models' / reference
        directory.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(directory)
        atomic(directory / 'manifest.json', manifest)
        return reference, directory

    def call(self, reference, **overrides):
        arguments = {**self.arguments, 'imported_asset_ref': reference, **overrides}
        return liquefy.liquefy_model(self.root, self.job, self.task, self.lock,
            arguments, atomic, object_hash)

    def current(self, physical=False):
        module = self.root / 'modules/modeling'
        (module / 'modeling_flow/third_party').mkdir(parents=True)
        shutil.copyfile(TOOLBOXES / 'modeling_flow/contracts.py', module / 'modeling_flow/contracts.py')
        (module / 'modeling_flow/third_party/LICENSE.txt').write_text('Synthetic pinned license\n')
        image = self.job / 'inputs/image.png'
        image.parent.mkdir()
        image.write_bytes(b'not a real image; no decoding or inference is performed')
        config = self.job / 'work/modeling-runtime/config.json'
        atomic(config, {'schema_version': 'modeling-flow-config/1'})
        runtime = {'config_path': str(config.relative_to(self.job)),
            'config_sha256': bundle.sha256(config), 'python_executable': '/not/executed/python'}
        atomic(self.job / 'modeling-runtime-pin.json', runtime)
        self.task['modeling_runtime'] = runtime
        self.task['request']['input_images'] = [{'id': 'image1', 'path': str(image.relative_to(self.job)),
            'sha256': bundle.sha256(image)}]
        request = {'schema_version': 'modeling-flow-request/1' if physical else 'modeling-flow-preview-request/1',
            'image_path': str(image), 'scale_axis': 'max', 'seed': 0}
        if physical:
            request.update(physical_extent_m=0.4, material={'name': 'given old solid', 'density_kg_m3': 500})
        reference = object_hash({'request': request, 'image_sha256': bundle.sha256(image),
            'runtime_sha256': runtime['config_sha256'], 'module': self.lock['modules']['modeling']})
        directory = self.job / 'work/modeling-images' / reference
        assets = directory / 'assets'
        assets.mkdir(parents=True)
        atomic(directory / 'request.json', request)
        mesh = {**self.raw, 'image_sha256': bundle.sha256(image)}
        atomic(assets / 'display_mesh.json', mesh)
        if not physical:
            atomic(assets / 'raw_mesh.json', mesh)
        (assets / 'display.glb').write_bytes(b'sealed fixture GLB')
        (assets / 'display.obj').write_text('sealed fixture OBJ\n')
        receipt = {'schema_version': 'modeling-flow-receipt/1',
            'purpose': 'physics_geometry' if physical else 'model_preview',
            'display_only': not physical, 'ready_to_simulate': physical,
            'simulation_performed': False, 'configuration_file_sha256': runtime['config_sha256'],
            'request_sha256': contracts.digest(contracts.load_request(request)),
            'image': {'sha256': bundle.sha256(image)},
            'artifacts': [{'path': path.name, 'sha256': bundle.sha256(path), 'bytes': path.stat().st_size}
                          for path in sorted(assets.iterdir())]}
        atomic(assets / 'receipt.json', receipt)
        public = {'ok': True, 'model_ref': reference, 'ready_to_simulate': False}
        if physical:
            public['geometry_verified'] = True
            legacy = json.loads(json.dumps({'vertices': mesh['vertices'], 'triangles': mesh['faces']}, sort_keys=True))
            encoded = json.dumps(legacy, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            public['mesh_ref'] = hashlib.sha256(encoded.encode()).hexdigest()
            mesh_asset = self.job / 'work/mesh-assets' / (public['mesh_ref'] + '.json')
            atomic(mesh_asset, legacy)
        else:
            public.update(result_kind='model-preview', ready_to_preview=True, simulation_performed=False)
        pin_paths = sorted(assets.iterdir()) + ([mesh_asset] if physical else [directory / 'request.json'])
        atomic(directory / 'response.json', {'public_result': public,
            'artifact_pins': [{'path': str(path.relative_to(self.job)), 'sha256': bundle.sha256(path)}
                              for path in pin_paths]})
        return reference, directory

    def call_current(self, reference):
        return liquefy.liquefy_model(self.root, self.job, self.task, self.lock,
            {**self.arguments, 'model_ref': reference}, atomic, object_hash)

    def test_imported_utf8_ref_preserves_shape_orientation_scale_and_source_bytes(self):
        reference, source = self.imported()
        original = (source / 'raw.json').read_bytes()
        result = self.call(reference)
        self.assertTrue(result['ok'])
        self.assertFalse(result['ready_to_simulate'])
        self.assertFalse(result['simulation_performed'])
        self.assertAlmostEqual(result['source_volume_m3'], 0.016)
        self.assertAlmostEqual(result['target_mass_kg'], 16)
        entity = liquefy.load_liquefied_entity(self.job, result['fluid_ref'], self.task, self.lock)
        self.assertEqual(entity['shape']['center'], [0, 0.5, 0])
        self.assertEqual(entity['shape']['mesh']['metadata']['bounds'], [[-0.2, -0.1, -0.1], [0.2, 0.1, 0.1]])
        self.assertEqual(entity['shape']['reference_density_kg_m3'], 1000)
        self.assertEqual((source / 'raw.json').read_bytes(), original)

    def test_deterministic_complete_attempt_reuses_and_does_not_change_files(self):
        reference, _ = self.imported()
        first = self.call(reference)
        directory = self.job / 'work/liquefy' / first['fluid_ref']
        before = {path.name: path.read_bytes() for path in directory.iterdir()}
        second = self.call(reference)
        self.assertEqual(first['fluid_ref'], second['fluid_ref'])
        self.assertTrue(second['reused'])
        self.assertEqual(before, {path.name: path.read_bytes() for path in directory.iterdir()})

    def test_incomplete_attempt_is_retained_and_rejected(self):
        reference, _ = self.imported()
        first = self.call(reference)
        response = self.job / 'work/liquefy' / first['fluid_ref'] / 'response.json'
        response.unlink()
        result = self.call(reference)
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'], 'model_liquefy_incomplete')
        self.assertFalse(response.exists())

    def test_source_member_tamper_rejected_before_new_attempt(self):
        reference, directory = self.imported()
        (directory / 'raw.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'size changed|sealed asset changed'):
            self.call(reference)

    def test_cross_task_ref_and_changed_producer_or_model_ref_rejected(self):
        reference, directory = self.imported()
        path = directory / 'manifest.json'
        original = bundle.read_json(path)
        mutations = [('task_id', '任务二'), ('source_model_ref', 'd' * 64),
            ('producer', {**original['producer'], 'version': '99'})]
        for key, value in mutations:
            with self.subTest(key=key):
                atomic(path, {**original, key: value})
                with self.assertRaisesRegex(ValueError, 'different task|canonical manifest'):
                    self.call(reference)

    def test_manifest_path_traversal_and_symlink_parent_rejected(self):
        reference, directory = self.imported()
        path = directory / 'manifest.json'
        manifest = bundle.read_json(path)
        manifest['members'][0]['path'] = '../raw.json'
        new_ref = liquefy._hash({key: manifest[key] for key in
            ('task_id', 'asset_id', 'source_model_ref', 'producer', 'members')})
        moved = directory.with_name(new_ref)
        directory.rename(moved)
        manifest['imported_asset_ref'] = new_ref
        atomic(moved / 'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, 'traversal'):
            self.call(new_ref)
        (moved / 'manifest.json').unlink()
        (moved / 'manifest.json').symlink_to(self.directory / 'outside.json')
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            self.call(new_ref)

    def test_symlink_source_member_rejected(self):
        reference, directory = self.imported()
        source = directory / 'raw.json'
        external = self.directory / 'outside.json'
        source.rename(external)
        source.symlink_to(external)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            self.call(reference)

    def test_units_missing_and_open_surface_rejected(self):
        reference, _ = self.imported(raw={key: value for key, value in self.raw.items() if key != 'units'})
        with self.assertRaisesRegex(ValueError, 'explicitly declare units'):
            self.call(reference)
        # An independent source for the second failed-attempt geometry.
        raw = copy.deepcopy(self.raw)
        raw['faces'].pop()
        reference, _ = self.imported(raw=raw)
        with self.assertRaisesRegex(ValueError, 'closed'):
            self.call(reference)

    def test_negative_global_winding_is_explicitly_reversed(self):
        raw = {**self.raw, 'faces': [list(reversed(face)) for face in self.raw['faces']]}
        reference, _ = self.imported(raw=raw)
        result = self.call(reference)
        receipt = bundle.read_json(self.job / 'work/liquefy' / result['fluid_ref'] / 'receipt.json')
        self.assertTrue(receipt['transform']['global_winding_reversed'])
        self.assertLess(receipt['transform']['source_signed_volume'], 0)
        self.assertGreater(receipt['source_volume_m3'], 0)

    def test_explicit_extent_and_fixed_water_contract(self):
        reference, _ = self.imported()
        for key, value in [('physical_extent_m', None), ('preset', 'honey'),
                ('reference_density_kg_m3', 900), ('conservation', 'mass'),
                ('conversion', 'thermal_melting'), ('shell', 'true'), ('model_ref', 'd' * 64)]:
            with self.subTest(key=key):
                arguments = {**self.arguments, 'imported_asset_ref': reference}
                if value is None:
                    arguments.pop(key)
                else:
                    arguments[key] = value
                with self.assertRaises(ValueError):
                    liquefy.liquefy_model(self.root, self.job, self.task, self.lock,
                        arguments, atomic, object_hash)

    def test_default_spacing_position_and_velocity_are_disclosed(self):
        reference, _ = self.imported()
        result = liquefy.liquefy_model(self.root, self.job, self.task, self.lock,
            {'imported_asset_ref': reference, 'physical_extent_m': 0.4, 'entity_id': 'water'}, atomic, object_hash)
        for key in ('position', 'velocity', 'spacing_m'):
            self.assertTrue(any('Default ' + key in item for item in result['assumptions']))

    def test_derived_output_tamper_and_consumer_pin_change_rejected(self):
        reference, _ = self.imported()
        result = self.call(reference)
        directory = self.job / 'work/liquefy' / result['fluid_ref']
        entity = directory / 'entity.json'
        original = entity.read_bytes()
        entity.write_text('{}')
        with self.assertRaises(ValueError):
            liquefy.load_liquefied_entity(self.job, result['fluid_ref'], self.task, self.lock)
        entity.write_bytes(original)
        changed = copy.deepcopy(self.lock)
        changed['modules']['simulation']['digest'] = '9' * 64
        with self.assertRaisesRegex(ValueError, 'consumer module pins'):
            liquefy.load_liquefied_entity(self.job, result['fluid_ref'], self.task, changed)

    def test_untrusted_response_hash_rewrite_cannot_change_qualified_geometry(self):
        reference, _ = self.imported()
        result = self.call(reference)
        directory = self.job / 'work/liquefy' / result['fluid_ref']
        path = directory / 'qualified-mesh.json'
        mesh = bundle.read_json(path)
        mesh['vertices'][0][0] += 0.001
        atomic(path, mesh)
        response_path = directory / 'response.json'
        response = bundle.read_json(response_path)
        response['artifact_pins'] = [liquefy._pin(self.job, self.job / item['path']) for item in response['artifact_pins']]
        atomic(response_path, response)
        with self.assertRaisesRegex(ValueError, 'independently qualified'):
            liquefy.load_liquefied_entity(self.job, result['fluid_ref'], self.task, self.lock)

    def test_export_only_referenced_assets_public_receipt_and_license(self):
        reference, _ = self.imported()
        result = self.call(reference)
        entity = liquefy.load_liquefied_entity(self.job, result['fluid_ref'], self.task, self.lock)
        self.assertEqual(liquefy.used_liquefy_assets(self.job, {'entities': []}, self.root), [])
        exports = liquefy.used_liquefy_assets(self.job, {'entities': [entity]}, self.root, self.task, self.lock)
        self.assertEqual(len(exports), 4)
        self.assertTrue(any(item['name'].endswith('/licenses/0.txt') for item in exports))
        for item in exports:
            text = Path(item['path']).read_text()
            self.assertNotIn('/Users/', text)
            self.assertNotIn('private_path', text)
            self.assertNotIn('task_id', text)
            self.assertNotIn('任务一', text)
        bad = copy.deepcopy(entity)
        bad['shape']['receipt_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'scene liquid geometry'):
            liquefy.used_liquefy_assets(self.job, {'entities': [bad]}, self.root)

    def test_current_preview_requires_current_event_runtime_and_exact_pins(self):
        reference, _ = self.current()
        result = self.call_current(reference)
        self.assertTrue(result['ok'])
        self.task['request']['input_images'] = []
        with self.assertRaisesRegex(ValueError, 'admitted image'):
            self.call_current(reference)

    def test_current_preview_modified_source_and_runtime_rejected(self):
        reference, directory = self.current()
        raw = directory / 'assets/raw_mesh.json'
        original = raw.read_bytes()
        raw.write_text('{}')
        with self.assertRaises(ValueError):
            self.call_current(reference)
        raw.write_bytes(original)
        self.task['modeling_runtime']['config_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'runtime differs'):
            self.call_current(reference)

    def test_current_model_pin_change_rejected_and_physical_display_role_disclosed(self):
        reference, _ = self.current(physical=True)
        result = self.call_current(reference)
        receipt = bundle.read_json(self.job / 'work/liquefy' / result['fluid_ref'] / 'receipt.json')
        self.assertEqual(receipt['source']['mesh_role'], 'unmodified_display_mesh')
        self.lock['modules']['modeling']['digest'] = 'f' * 64
        with self.assertRaisesRegex(ValueError, 'request and module pin'):
            self.call_current(reference)


if __name__ == '__main__':
    unittest.main()
