"""Task-owned, CPU-only geometric conversion into a water initial volume.

The operation does not melt material or run a solver. It preserves source bytes,
records an explicit metre scale, and leaves particle sampling to scene prepare.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import stat

from bundle import inside, read_json, sha256


MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 64
MAX_DERIVED_BYTES = 4 * 1024 * 1024
HEX = re.compile(r'[a-f0-9]{64}')


def _hash(value) -> str:
    """Host import canonicalization explicitly uses UTF-8, not ASCII escapes."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _reference(value, label: str) -> str:
    if not isinstance(value, str) or HEX.fullmatch(value) is None:
        raise ValueError(label + ' must be 64 lowercase hexadecimal characters')
    return value


def _regular(root: Path, relative: str, expected: str | None = None,
             size: int | None = None, limit: int = MAX_SOURCE_BYTES) -> Path:
    if (not isinstance(relative, str) or relative != Path(relative).as_posix()
            or relative in ('', '.') or '\\' in relative):
        raise ValueError('asset path must be a canonical relative path')
    path = inside(root, relative)
    if not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('asset must be a regular file')
    actual = path.stat().st_size
    if not 0 < actual <= limit or size is not None and (type(size) is not int or actual != size):
        raise ValueError('asset is oversized or its size changed')
    if expected is not None and sha256(path) != _reference(expected, 'asset hash'):
        raise ValueError('sealed asset changed')
    return path


def _pin(job: Path, path: Path) -> dict:
    checked = _regular(job, path.relative_to(job).as_posix())
    return {'path': checked.relative_to(job).as_posix(), 'sha256': sha256(checked),
            'size_bytes': checked.stat().st_size}


def _pins(job: Path, values: object) -> list[dict]:
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_MEMBERS + 8:
        raise ValueError('source must have bounded artifact pins')
    paths, total, result = set(), 0, []
    for value in values:
        if (not isinstance(value, dict) or not isinstance(value.get('path'), str)
                or value['path'] in paths):
            raise ValueError('source artifact pins must have unique paths')
        path = _regular(job, value['path'], _reference(value.get('sha256'), 'artifact hash'), value.get('size_bytes'))
        paths.add(value['path'])
        total += path.stat().st_size
        if total > MAX_SOURCE_BYTES:
            raise ValueError('source artifacts exceed their aggregate byte budget')
        result.append(_pin(job, path))
    return result


def _producer(value: object) -> dict:
    if (not isinstance(value, dict) or any(not isinstance(value.get(key), str)
            or re.fullmatch(r'[\w.+-]{1,128}', value[key]) is None for key in ('id', 'version'))):
        raise ValueError('source producer requires id, version and digest')
    return {key: value[key] for key in ('id', 'version')} | {
        'digest': _reference(value.get('digest'), 'producer digest')}


def _imported_source(job: Path, task: dict, reference: str) -> dict:
    directory = inside(job, 'work/imported-models/' + reference)
    manifest_path = _regular(directory, 'manifest.json', limit=1_000_000)
    manifest = read_json(manifest_path)
    required = {'schema_version', 'task_id', 'imported_asset_ref', 'asset_id',
                'source_model_ref', 'producer', 'members'}
    if set(manifest) != required or manifest.get('schema_version') != 'model-asset-import/1':
        raise ValueError('unsupported imported model manifest')
    if manifest['task_id'] != task['task_id'] or manifest['imported_asset_ref'] != reference:
        raise ValueError('imported asset belongs to a different task or reference')
    asset_id = _reference(manifest['asset_id'], 'asset_id')
    source_model_ref = _reference(manifest['source_model_ref'], 'source_model_ref')
    members = manifest['members']
    if not isinstance(members, list) or not 1 <= len(members) <= MAX_MEMBERS:
        raise ValueError('imported asset requires bounded members')
    if _hash({key: manifest[key] for key in
              ('task_id', 'asset_id', 'source_model_ref', 'producer', 'members')}) != reference:
        raise ValueError('imported asset reference differs from its canonical manifest')
    paths, roles, pins, total = set(), {}, [], 0
    licenses = []
    for member in members:
        if (not isinstance(member, dict) or set(member) != {'path', 'role', 'sha256', 'size_bytes'}
                or not isinstance(member['role'], str) or not member['role']
                or not isinstance(member['path'], str) or member['path'] in paths):
            raise ValueError('imported members require unique paths and typed pins')
        path = _regular(directory, member['path'], member['sha256'], member['size_bytes'])
        paths.add(member['path'])
        total += path.stat().st_size
        if total > MAX_SOURCE_BYTES:
            raise ValueError('imported asset exceeds its aggregate byte budget')
        pins.append(_pin(job, path))
        roles.setdefault(member['role'], []).append(path)
        if member['role'] == 'license':
            licenses.append(_pin(job, path))
    # A display-only cleanup cannot silently substitute for the immutable raw.
    if len(roles.get('raw_mesh', [])) != 1:
        raise ValueError('imported model must seal exactly one raw_mesh member')
    if len(roles.get('display_mesh', [])) > 1:
        raise ValueError('imported model has ambiguous display_mesh members')
    raw = roles['raw_mesh'][0]
    pins.append(_pin(job, manifest_path))
    return {'kind': 'imported_asset', 'reference': reference, 'asset_id': asset_id,
        'source_model_ref': source_model_ref, 'producer': _producer(manifest['producer']),
        'source_manifest_sha256': sha256(manifest_path), 'mesh_path': raw,
        'mesh_role': 'raw_mesh', 'pins': pins, 'licenses': licenses}


def _current_source(root: Path, job: Path, task: dict, lock: dict,
                    reference: str, object_hash) -> dict:
    directory = inside(job, 'work/modeling-images/' + reference)
    response_path = _regular(directory, 'response.json', limit=4_000_000)
    response = read_json(response_path, 4_000_000)
    public = response.get('public_result', {})
    if public.get('ok') is not True or public.get('model_ref') != reference:
        raise ValueError('current model_ref requires a successfully sealed model')
    preview = public.get('result_kind') == 'model-preview'
    if preview and (public.get('ready_to_preview') is not True
                    or public.get('ready_to_simulate') is not False):
        raise ValueError('current model is not a completed display-only preview')
    if not preview and public.get('geometry_verified') is not True:
        raise ValueError('current physical model has not passed its geometry gate')
    pins = _pins(job, response.get('artifact_pins'))
    by_path = {item['path']: item for item in pins}
    prefix = directory.relative_to(job).as_posix() + '/'
    extra_allowed = {prefix + 'request.json'}
    if not preview:
        mesh_ref = _reference(public.get('mesh_ref'), 'current physical mesh_ref')
        mesh_relative = 'work/mesh-assets/' + mesh_ref + '.json'
        extra_allowed.add(mesh_relative)
        if mesh_relative not in by_path:
            raise ValueError('current physical mesh_ref was not sealed')
        legacy = read_json(_regular(job, mesh_relative))
        encoded = json.dumps(legacy, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        if hashlib.sha256(encoded.encode()).hexdigest() != mesh_ref:
            raise ValueError('current physical mesh_ref integrity check failed')
    if any(not item['path'].startswith(prefix + 'assets/') and item['path'] not in extra_allowed
           for item in pins):
        raise ValueError('current model pins must remain in the exact model asset directory')
    assets = inside(directory, 'assets')
    receipt_path = _regular(assets, 'receipt.json', limit=4_000_000)
    request_path = _regular(directory, 'request.json', limit=1_000_000)
    receipt = read_json(receipt_path, 4_000_000)
    request = read_json(request_path)
    required_sources = {str((assets / name).relative_to(job)) for name in
                        ('receipt.json', 'display_mesh.json', 'display.glb', 'display.obj')}
    if not required_sources <= set(by_path):
        raise ValueError('current model lacks its complete sealed display assets')
    runtime = task.get('modeling_runtime')
    if not isinstance(runtime, dict):
        raise ValueError('current model requires its task modeling runtime pin')
    runtime_pin_path = _regular(job, 'modeling-runtime-pin.json', limit=65536)
    if read_json(runtime_pin_path, 65536) != runtime:
        raise ValueError('current model runtime differs from this task pin')
    config = _regular(job, runtime.get('config_path'), runtime.get('config_sha256'), limit=65536)
    images = [image for image in task['request'].get('input_images', [])
        if image.get('sha256') == receipt.get('image', {}).get('sha256')
        and str(inside(job, image.get('path'))) == request.get('image_path')]
    if len(images) != 1:
        raise ValueError('current model must identify one admitted image of this task')
    image = images[0]
    image_path = _regular(job, image['path'], image['sha256'])
    if reference != object_hash({'request': request, 'image_sha256': image['sha256'],
            'runtime_sha256': runtime['config_sha256'], 'module': lock['modules']['modeling']}):
        raise ValueError('current model reference differs from its request and module pin')
    if (receipt.get('schema_version') != 'modeling-flow-receipt/1'
            or receipt.get('configuration_file_sha256') != runtime['config_sha256']
            or preview and (receipt.get('purpose') != 'model_preview' or receipt.get('display_only') is not True
                or receipt.get('simulation_performed') is not False or receipt.get('ready_to_simulate') is not False)
            or not preview and (receipt.get('display_only') is not False
                or receipt.get('purpose') != 'physics_geometry')):
        raise ValueError('current model receipt differs from its runtime or purpose')
    module = inside(root, lock['modules']['modeling']['path'])
    contract_path = _regular(module, 'modeling_flow/contracts.py', limit=1_000_000)
    spec = importlib.util.spec_from_file_location('_liquefy_pinned_contracts', contract_path)
    contracts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contracts)
    if receipt.get('request_sha256') != contracts.digest(contracts.load_request(request)):
        raise ValueError('current model receipt differs from its normalized request')
    artifacts = receipt.get('artifacts')
    if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= MAX_MEMBERS:
        raise ValueError('current model receipt lacks bounded source declarations')
    declared = set()
    for item in artifacts:
        if not isinstance(item, dict) or item.get('path') in declared:
            raise ValueError('current model receipt has duplicate source declarations')
        path = _regular(assets, item.get('path'), item.get('sha256'), item.get('bytes'))
        if path.relative_to(job).as_posix() not in by_path:
            raise ValueError('current model receipt source was not sealed')
        declared.add(item['path'])
    raw_name = 'raw_mesh.json' if (assets / 'raw_mesh.json').exists() else 'display_mesh.json'
    if (raw_name == 'display_mesh.json' and receipt.get('display_cleanup') is not None
            or raw_name not in declared
            or receipt_path.relative_to(job).as_posix() not in by_path):
        raise ValueError('current model lacks its sealed original raw geometry')
    raw = _regular(assets, raw_name)
    mesh = read_json(raw, MAX_SOURCE_BYTES)
    if mesh.get('image_sha256') != image['sha256']:
        raise ValueError('current model geometry differs from its admitted image')
    extra = [request_path, response_path, runtime_pin_path, config, image_path]
    if image.get('source_receipt'):
        source = image['source_receipt']
        extra.append(_regular(job, source['path'], source['sha256']))
    pins = list({item['path']: item for item in pins + [_pin(job, path) for path in extra]}.values())
    licenses = []
    license_dir = inside(module, 'modeling_flow/third_party')
    if license_dir.is_dir():
        for path in sorted(license_dir.iterdir()):
            checked = _regular(license_dir, path.name)
            licenses.append({'path': str(checked), 'sha256': sha256(checked),
                             'size_bytes': checked.stat().st_size, 'external': True})
    return {'kind': 'current_model', 'reference': reference, 'source_model_ref': reference,
        'producer': _producer(lock['modules']['modeling']),
        'source_manifest_sha256': sha256(response_path), 'mesh_path': raw,
        'mesh_role': 'raw_mesh' if raw_name == 'raw_mesh.json' else 'unmodified_display_mesh',
        'pins': pins, 'licenses': licenses}


def _number(value, label: str, low: float, high: float) -> float:
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and low <= value <= high
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(label + ' must be a bounded finite number')
    return float(value)


def _vector(value, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(label + ' must contain three finite numbers')
    return [_number(item, label, -1000.0, 1000.0) for item in value]


def _request(arguments: dict) -> tuple[dict, list[str]]:
    allowed = {'imported_asset_ref', 'model_ref', 'physical_extent_m', 'entity_id',
        'position', 'velocity', 'spacing_m', 'preset', 'reference_density_kg_m3',
        'conversion', 'conservation'}
    if not isinstance(arguments, dict) or set(arguments) - allowed:
        raise ValueError('liquefy accepts typed model references and explicit physical parameters only')
    if ('imported_asset_ref' in arguments) == ('model_ref' in arguments):
        raise ValueError('choose exactly one imported_asset_ref or current model_ref')
    if 'physical_extent_m' not in arguments:
        raise ValueError('physical_extent_m must be explicitly confirmed; image scale is not measured')
    extent = _number(arguments['physical_extent_m'], 'physical_extent_m', 1e-6, 1000.0)
    entity_id = arguments.get('entity_id')
    if not isinstance(entity_id, str) or not entity_id.strip() or len(entity_id) > 128:
        raise ValueError('entity_id must be a nonempty string of at most 128 characters')
    fixed = {'preset': 'water', 'reference_density_kg_m3': 1000.0,
             'conversion': 'instant', 'conservation': 'volume'}
    for key, expected in fixed.items():
        if key in arguments and (arguments[key] != expected or type(arguments[key]) is bool):
            raise ValueError('liquefy currently requires fixed ' + key + '=' + str(expected))
    assumptions = ['Instant geometric replacement by water; no heating, melting, latent heat or phase transition.',
        'Density is the fixed water reference 1000 kg/m^3, not inferred from the source.',
        'Conservation target is the explicitly scaled source volume; source solid mass is not conserved.']
    position = _vector(arguments.get('position', [0, 0, 0]), 'position')
    velocity = _vector(arguments.get('velocity', [0, 0, 0]), 'velocity')
    spacing = _number(arguments.get('spacing_m', max(1e-4, min(0.5, extent / 10))),
        'spacing_m', 1e-4, 0.5)
    for key in ('position', 'velocity', 'spacing_m'):
        if key not in arguments:
            assumptions.append('Default ' + key + '=' + json.dumps(
                {'position': position, 'velocity': velocity, 'spacing_m': spacing}[key]) + ' used.')
    key = 'imported_asset_ref' if 'imported_asset_ref' in arguments else 'model_ref'
    request = {key: _reference(arguments[key], key), 'physical_extent_m': extent,
        'entity_id': entity_id, 'position': position, 'velocity': velocity,
        'spacing_m': spacing, **fixed}
    return request, assumptions


def _consumer(lock: dict) -> dict:
    return {role: _producer(lock['modules'][role]) for role in ('modeling', 'simulation', 'rendering')}


def _qualified_source(path: Path, extent: float) -> tuple[dict, dict, dict]:
    value = read_json(path, MAX_SOURCE_BYTES)
    if value.get('units') not in ('m', 'model_unit'):
        raise ValueError('source mesh must explicitly declare units m or model_unit')
    vertices, faces = value.get('vertices'), value.get('faces')
    if (not isinstance(vertices, list) or not 4 <= len(vertices) <= 4096
            or not isinstance(faces, list) or not 4 <= len(faces) <= 8192):
        raise ValueError('liquefy volume requires 4-4096 vertices and 4-8192 faces; simplify explicitly first')
    points = [_vector(point, 'source vertex') for point in vertices]
    bounds = [[min(point[k] for point in points) for k in range(3)],
              [max(point[k] for point in points) for k in range(3)]]
    center = [(bounds[0][k] + bounds[1][k]) / 2 for k in range(3)]
    maximum = max(bounds[1][k] - bounds[0][k] for k in range(3))
    if maximum <= 0:
        raise ValueError('source geometry must have nonzero extent')
    factor = extent / maximum
    si = [[(point[k] - center[k]) * factor for k in range(3)] for point in points]
    from physics_demo.core.meshes import audit_mesh
    from physics_demo.core.volume_sampling import qualify_mesh
    audit = audit_mesh(si, faces, require_closed=True)
    flipped = audit['signed_volume'] < 0
    corrected = [list(reversed(face)) for face in faces] if flipped else faces
    qualified = qualify_mesh({'vertices': si, 'triangles': corrected}, units='m')
    source_volume = audit['signed_volume'] / factor ** 3
    transform = {'source_units': value['units'], 'target_units': 'm',
        'source_bbox': bounds, 'source_bbox_center': center, 'uniform_scale': factor,
        'physical_maximum_extent_m': extent, 'orientation_preserved': True,
        'global_winding_reversed': flipped, 'source_signed_volume': source_volume,
        'source_volume': abs(source_volume), 'source_volume_units': value['units'] + '^3'}
    # This geometry-only projection contains no task identity, private paths or prompts.
    export_source = {'schema_version': 'liquefy-source-geometry/1', 'units': value['units'],
        'vertices': vertices, 'faces': faces, 'source_sha256': sha256(path)}
    return qualified, transform, export_source


def _receipt(reference: str, request: dict, source: dict, consumer: dict,
             qualified: dict, transform: dict, directory: Path, assumptions: list[str]) -> dict:
    volume = qualified['metadata']['signed_volume']
    return {'schema_version': 'model-liquefy-receipt/1', 'fluid_ref': reference,
        'operation': 'instant_geometric_conversion', 'source': source,
        'consumer': consumer, 'request': request, 'transform': transform,
        'qualified_mesh_sha256': sha256(directory / 'qualified-mesh.json'),
        'source_geometry_projection_sha256': sha256(directory / 'source-mesh.json'),
        'source_volume_m3': volume, 'reference_density_kg_m3': 1000.0,
        'target_mass_kg': volume * 1000.0, 'conservation': 'volume',
        'particle_sampling': 'deferred_to_physics_prepare', 'simulation_performed': False,
        'neural_inference_performed': False, 'ready_to_simulate': False,
        'geometry_audit': qualified['metadata'], 'assumptions': assumptions}


def _entity(reference: str, request: dict, qualified: dict, receipt_hash: str) -> dict:
    return {'id': request['entity_id'], 'type': 'fluid', 'preset': 'water',
        'shape': {'type': 'mesh_volume', 'mesh': qualified, 'center': request['position'],
            'units': 'm', 'conversion': 'instant', 'conservation': 'volume',
            'reference_density_kg_m3': 1000.0, 'source_ref': reference, 'receipt_sha256': receipt_hash},
        'spacing': request['spacing_m'], 'velocity': request['velocity']}


def _public(reference: str, request: dict, directory: Path, job: Path,
            receipt_hash: str, volume: float, assumptions: list[str]) -> dict:
    return {'ok': True, 'fluid_ref': reference, 'entity_ref': reference,
        'entity_path': str((directory / 'entity.json').relative_to(job)),
        'receipt_ref': reference, 'receipt_sha256': receipt_hash,
        'source_volume_m3': volume, 'target_mass_kg': volume * 1000.0,
        'reference_density_kg_m3': 1000.0, 'spacing_m': request['spacing_m'],
        'ready_to_simulate': False, 'simulation_performed': False,
        'neural_inference_performed': False, 'conversion': 'instant', 'conservation': 'volume',
        'assumptions': assumptions, 'next_action': 'Insert entity into the current scene, then physics_prepare.'}


def _verify_saved(job: Path, directory: Path, task: dict | None, lock: dict | None) -> dict:
    response_path = _regular(directory, 'response.json', limit=1_000_000)
    response = read_json(response_path)
    if response.get('schema_version') != 'model-liquefy-response/1':
        raise ValueError('invalid saved liquid conversion')
    if task is not None and response.get('task_id') != task['task_id']:
        raise ValueError('liquid conversion belongs to a different task')
    if lock is not None and response.get('consumer') != _consumer(lock):
        raise ValueError('liquid conversion belongs to different consumer module pins')
    outputs = _pins(job, response.get('artifact_pins'))
    sources = _pins(job, response.get('source_pins'))
    required = {str((directory / name).relative_to(job)) for name in
                ('entity.json', 'receipt.json', 'qualified-mesh.json', 'source-mesh.json', 'request.json')}
    licenses = response.get('licenses', [])
    if not isinstance(licenses, list) or len(licenses) > MAX_MEMBERS:
        raise ValueError('liquid conversion has invalid license pins')
    for index, item in enumerate(licenses):
        if item.get('path') != str((directory / ('license-' + str(index) + '.txt')).relative_to(job)):
            raise ValueError('liquid conversion license must be a sealed local copy')
    required.update(item['path'] for item in licenses)
    if required != {item['path'] for item in outputs}:
        raise ValueError('liquid conversion lacks exact sealed output files')
    reference = directory.name
    receipt = read_json(directory / 'receipt.json')
    stored_request = read_json(directory / 'request.json')
    request, assumptions = _request(stored_request)
    # Default disclosures are bound explicitly because normalized requests carry defaults.
    assumptions = response.get('assumptions')
    if not isinstance(assumptions, list) or any(not isinstance(item, str) for item in assumptions):
        raise ValueError('liquid conversion lacks its explicit assumptions')
    binding = {'schema_version': 'model-liquefy/1', 'task_id': response['task_id'],
        'request': request, 'source': response['source'], 'consumer': response['consumer'],
        'assumptions': assumptions}
    if reference != _hash(binding) or receipt.get('fluid_ref') != reference:
        raise ValueError('liquid conversion reference differs from its sealed binding')
    raw_pins = [item for item in sources if item['sha256'] == response['source']['mesh_sha256']]
    if not raw_pins or not any(item['sha256'] == response['source']['source_manifest_sha256'] for item in sources):
        raise ValueError('liquid conversion lacks its exact sealed source')
    raw_path = inside(job, raw_pins[0]['path'])
    qualified, transform, export_source = _qualified_source(raw_path, request['physical_extent_m'])
    if (qualified != read_json(directory / 'qualified-mesh.json', MAX_DERIVED_BYTES)
            or export_source != read_json(directory / 'source-mesh.json', MAX_DERIVED_BYTES)
            or receipt != _receipt(reference, request, response['source'], response['consumer'],
                qualified, transform, directory, assumptions)):
        raise ValueError('liquid conversion differs from independently qualified source geometry')
    receipt_hash = sha256(directory / 'receipt.json')
    if (read_json(directory / 'entity.json', MAX_DERIVED_BYTES) != _entity(reference, request, qualified, receipt_hash)
            or response.get('public_result') != _public(reference, request, directory, job,
                receipt_hash, qualified['metadata']['signed_volume'], assumptions)):
        raise ValueError('liquid entity differs from its sealed conversion')
    if ([{key: item[key] for key in ('sha256', 'size_bytes')} for item in licenses]
            != response['source']['licenses']):
        raise ValueError('liquid conversion license inventory differs from its source binding')
    return response


def liquefy_model(root: Path, job: Path, task: dict, lock: dict,
                  arguments: dict, atomic, object_hash) -> dict:
    request, assumptions = _request(arguments)
    if 'imported_asset_ref' in request:
        source = _imported_source(job, task, request['imported_asset_ref'])
    else:
        source = _current_source(root, job, task, lock, request['model_ref'], object_hash)
    consumer = _consumer(lock)
    source_binding = {key: source[key] for key in ('kind', 'reference', 'source_model_ref',
        'producer', 'source_manifest_sha256', 'mesh_role')}
    if 'asset_id' in source:
        source_binding['asset_id'] = source['asset_id']
    source_binding['mesh_sha256'] = sha256(source['mesh_path'])
    source_binding['licenses'] = [{key: item[key] for key in ('sha256', 'size_bytes')}
                                  for item in source['licenses']]
    binding = {'schema_version': 'model-liquefy/1', 'task_id': task['task_id'],
        'request': request, 'source': source_binding, 'consumer': consumer,
        'assumptions': assumptions}
    reference = _hash(binding)
    directory = inside(job, 'work/liquefy/' + reference)
    if (directory / 'response.json').exists():
        saved = _verify_saved(job, directory, task, lock)
        return {**saved['public_result'], 'reused': True}
    if directory.exists():
        return {'ok': False, 'code': 'model_liquefy_incomplete', 'fluid_ref': reference,
            'ready_to_simulate': False, 'message': 'An incomplete attempt is retained; explicit recovery is required.'}
    directory.mkdir(parents=True)
    atomic(directory / 'request.json', request)
    qualified, transform, export_source = _qualified_source(source['mesh_path'], request['physical_extent_m'])
    atomic(directory / 'qualified-mesh.json', qualified)
    atomic(directory / 'source-mesh.json', export_source)
    volume = qualified['metadata']['signed_volume']
    receipt = _receipt(reference, request, source_binding, consumer, qualified, transform, directory, assumptions)
    atomic(directory / 'receipt.json', receipt)
    receipt_hash = sha256(directory / 'receipt.json')
    entity = _entity(reference, request, qualified, receipt_hash)
    atomic(directory / 'entity.json', entity)
    public = _public(reference, request, directory, job, receipt_hash, volume, assumptions)
    licenses = []
    for index, item in enumerate(source['licenses']):
        path = Path(item['path']) if item.get('external') else inside(job, item['path'])
        destination = directory / ('license-' + str(index) + '.txt')
        shutil.copyfile(path, destination)
        if sha256(destination) != item['sha256'] or sha256(path) != item['sha256']:
            raise ValueError('liquefy source license changed during export')
        licenses.append(_pin(job, destination))
    response = {'schema_version': 'model-liquefy-response/1', 'task_id': task['task_id'],
        'source': source_binding, 'consumer': consumer, 'source_pins': source['pins'],
        'licenses': licenses, 'public_result': public, 'assumptions': assumptions,
        'artifact_pins': [_pin(job, directory / name) for name in
            ('entity.json', 'receipt.json', 'qualified-mesh.json', 'source-mesh.json', 'request.json')] + licenses}
    atomic(directory / 'response.json', response)
    _verify_saved(job, directory, task, lock)
    return public


def load_liquefied_entity(job: Path, fluid_ref: str, task: dict, lock: dict) -> dict:
    directory = inside(job, 'work/liquefy/' + _reference(fluid_ref, 'fluid_ref'))
    _verify_saved(job, directory, task, lock)
    return read_json(directory / 'entity.json', MAX_DERIVED_BYTES)


def used_liquefy_assets(job: Path, scene: dict, root: Path,
                        task: dict | None = None, lock: dict | None = None, *, readonly: bool = False) -> list[dict]:
    """Export only sealed geometry referenced by the prepared scene.

    Import manifests, original private producer paths and task IDs stay local.
    The public conversion receipt contains hashes and a whitelisted producer pin.
    """
    references = {}
    for entity in scene.get('entities', []):
        shape = entity.get('shape', {}) if isinstance(entity, dict) else {}
        if shape.get('type') != 'mesh_volume' or 'source_ref' not in shape:
            continue
        reference = _reference(shape['source_ref'], 'scene source_ref')
        if reference in references and references[reference] != shape['receipt_sha256']:
            raise ValueError('scene uses conflicting liquid receipt bindings')
        directory = inside(job, 'work/liquefy/' + reference)
        saved = _verify_saved(job, directory, task, lock)
        if (shape.get('receipt_sha256') != sha256(directory / 'receipt.json')
                or shape.get('mesh') != read_json(directory / 'qualified-mesh.json', MAX_DERIVED_BYTES)):
            raise ValueError('scene liquid geometry differs from its sealed conversion')
        references[reference] = shape['receipt_sha256']
    result = []
    for reference in sorted(references):
        directory = inside(job, 'work/liquefy/' + reference)
        saved = _verify_saved(job, directory, task, lock)
        for name in ('receipt.json', 'qualified-mesh.json', 'source-mesh.json'):
            path = directory / name
            result.append({'path': str(path), 'name': 'modeling/liquefy/' + reference + '/' + name,
                           'sha256': sha256(path)})
        for index, item in enumerate(saved.get('licenses', [])):
            path = inside(job, item['path'])
            result.append({'path': str(path), 'name': 'modeling/liquefy/' + reference + '/licenses/' + str(index) + '.txt',
                           'sha256': item['sha256']})
    return result
