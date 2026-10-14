"""Derive a display cleanup from an already sealed model, without inference.

The coordinator accepts only a current-task model reference and a advertised
policy. A fixed child invocation exports geometry with the task's pinned
interpreter; it never imports the neural runner or loads model weights.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time

from bundle import inside, read_json, sha256, verify_bundle


MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_PINS = 64


def _pin_file(job: Path, value: dict) -> Path:
    if (not isinstance(value, dict) or not isinstance(value.get('path'), str)
            or not isinstance(value.get('sha256'), str)
            or re.fullmatch(r'[a-f0-9]{64}', value['sha256']) is None):
        raise ValueError('cleanup source has an invalid artifact pin')
    path = inside(job, value['path'])
    if (not path.is_file() or not 0 < path.stat().st_size <= MAX_SOURCE_BYTES
            or sha256(path) != value['sha256']):
        raise ValueError('sealed cleanup source artifact changed')
    return path


def _request(value: dict) -> None:
    allowed = {'schema_version', 'image_path', 'scale_axis', 'physical_extent_m',
               'seed', 'num_inference_steps', 'guidance_scale', 'octree_resolution',
               'num_chunks', 'display_cleanup'}
    if (set(value) - allowed or value.get('schema_version') != 'modeling-flow-preview-request/1'
            or not isinstance(value.get('image_path'), str)
            or value.get('scale_axis') != 'max'):
        raise ValueError('cleanup requires an unchanged bounded model-preview request')


def _source(job: Path, task: dict, lock: dict, reference: str, object_hash, contracts) -> dict:
    """Verify the current task's source, without promoting it across pins."""
    if not isinstance(reference, str) or re.fullmatch(r'[a-f0-9]{64}', reference) is None:
        raise ValueError('cleanup requires a current task model_ref')
    directory = inside(job, 'work/modeling-images/' + reference)
    response = read_json(inside(directory, 'response.json'), 4_000_000)
    public = response.get('public_result')
    if (not isinstance(public, dict) or public.get('ok') is not True
            or public.get('result_kind') != 'model-preview'
            or public.get('ready_to_preview') is not True or public.get('model_ref') != reference
            or public.get('ready_to_simulate') is not False
            or public.get('simulation_performed') is not False):
        raise ValueError('cleanup requires a sealed display-only model')
    pins = response.get('artifact_pins')
    if (not isinstance(pins, list) or not 1 <= len(pins) <= MAX_PINS
            or any(not isinstance(p, dict) or not isinstance(p.get('path'), str) for p in pins)
            or len({p['path'] for p in pins}) != len(pins)):
        raise ValueError('cleanup source lacks unique artifact pins')
    paths = {p['path']: _pin_file(job, p) for p in pins}
    assets = inside(directory, 'assets')
    expected = {str((assets / name).relative_to(job)) for name in
                ('receipt.json', 'display_mesh.json', 'display.glb', 'display.obj')}
    if not expected <= set(paths):
        raise ValueError('cleanup source lacks sealed display assets')
    if any(not p.startswith(str(directory.relative_to(job)) + '/assets/')
           and p != str((directory / 'request.json').relative_to(job)) for p in paths):
        raise ValueError('cleanup source pin is outside its exact model directory')
    runtime = task.get('modeling_runtime')
    if (not isinstance(runtime, dict) or runtime != read_json(job / 'modeling-runtime-pin.json')
            or not isinstance(runtime.get('python_executable'), str)
            or not Path(runtime['python_executable']).is_absolute()):
        raise ValueError('cleanup runtime must match the current task pin')
    config_path = inside(job, runtime['config_path'])
    if sha256(config_path) != runtime.get('config_sha256'):
        raise ValueError('cleanup modeling runtime configuration changed')
    request_path = inside(directory, 'request.json')
    request = read_json(request_path)
    _request(request)
    receipt_path = inside(assets, 'receipt.json')
    receipt = read_json(receipt_path, 4_000_000)
    if (receipt.get('schema_version') != 'modeling-flow-receipt/1'
            or receipt.get('purpose') != 'model_preview' or receipt.get('display_only') is not True
            or receipt.get('ready_to_simulate') is not False):
        raise ValueError('cleanup source has no display-only inference receipt')
    normalized_request = contracts.load_request(request)
    if receipt.get('request_sha256') != contracts.digest(normalized_request):
        raise ValueError('cleanup source receipt differs from its original request')
    configuration = receipt.get('configuration_file_sha256')
    if configuration is None:
        # Legacy file-free configuration receipts use their canonical object.
        canonical = json.dumps(read_json(config_path), sort_keys=True, separators=(',', ':'),
                               ensure_ascii=False, allow_nan=False).encode('utf-8')
        configuration = hashlib.sha256(canonical).hexdigest()
        if receipt.get('configuration_sha256') != configuration:
            raise ValueError('cleanup source configuration differs from its runtime')
    elif configuration != runtime['config_sha256']:
        raise ValueError('cleanup source configuration differs from its runtime')
    images = [image for image in task['request'].get('input_images', [])
              if receipt.get('image', {}).get('sha256') == image.get('sha256')
              and str(inside(job, image['path'])) == request['image_path']]
    if len(images) != 1:
        raise ValueError('cleanup source must identify exactly one current task image')
    image = images[0]
    if sha256(inside(job, image['path'])) != image['sha256']:
        raise ValueError('cleanup source image changed')
    source_receipt = image.get('source_receipt')
    if source_receipt and sha256(inside(job, source_receipt['path'])) != source_receipt['sha256']:
        raise ValueError('cleanup reference admission receipt changed')
    expected_ref = object_hash({'request': request, 'image_sha256': image['sha256'],
        'runtime_sha256': runtime['config_sha256'], 'module': lock['modules']['modeling']})
    if reference != expected_ref:
        raise ValueError('cleanup model_ref differs from its exact request or current module pin')
    raw_name = 'raw_mesh.json' if (assets / 'raw_mesh.json').exists() else 'display_mesh.json'
    if raw_name == 'display_mesh.json' and receipt.get('display_cleanup') is not None:
        raise ValueError('cleanup source with postprocessing must retain its original raw mesh')
    raw_path = inside(assets, raw_name)
    if str(raw_path.relative_to(job)) not in paths:
        raise ValueError('cleanup raw geometry is not sealed')
    raw = read_json(raw_path, MAX_SOURCE_BYTES)
    if (raw.get('schema_version') != 'modeling-flow-display/1'
            or raw.get('units') not in ('m', 'model_unit')
            or not isinstance(raw.get('scale'), dict)
            or raw.get('image_sha256') != image['sha256']):
        raise ValueError('cleanup raw geometry coordinate system or image is invalid')
    artifacts = receipt.get('artifacts')
    required_artifacts = {'display_mesh.json', 'display.glb', 'display.obj', raw_name}
    if (not isinstance(artifacts, list) or not 1 <= len(artifacts) <= MAX_PINS
            or any(not isinstance(a, dict) or not isinstance(a.get('path'), str) for a in artifacts)
            or len({a['path'] for a in artifacts}) != len(artifacts)
            or not required_artifacts <= {a['path'] for a in artifacts}):
        raise ValueError('cleanup source receipt lacks unique required artifact declarations')
    for artifact in artifacts:
        path = inside(assets, artifact['path'])
        if (str(path.relative_to(job)) not in paths or sha256(path) != artifact.get('sha256')
                or path.stat().st_size != artifact.get('bytes')):
            raise ValueError('cleanup modeling receipt differs from its sealed artifacts')
    return {'directory': directory, 'request': request, 'request_path': request_path,
            'receipt': receipt, 'receipt_path': receipt_path, 'raw': raw, 'raw_path': raw_path,
            'runtime': runtime, 'image': image, 'public': public}


@contextmanager
def _locked(job: Path):
    path = inside(job, 'work/model-cleanup.lock')
    if path.is_symlink() or path.exists() and not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('cleanup lock must be a regular file without symlinks')
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def cleanup_model(root: Path, job: Path, task: dict, lock: dict,
                  arguments: dict, atomic, object_hash) -> dict:
    """Reprocess only already generated geometry in this task, then render later."""
    if (not isinstance(arguments, dict) or set(arguments) - {'model_ref', 'display_cleanup'}
            or 'model_ref' not in arguments):
        raise ValueError('cleanup accepts model_ref and display_cleanup only')
    mode = arguments.get('display_cleanup', 'surface')
    modeling = lock['modules']['modeling']
    if (mode not in ('surface', 'conservative', 'none')
            or modeling.get('display_cleanup') != 'bounded-floaters/1'
            or lock['modules']['rendering'].get('preview_geometry_scope') != 'render_input'
            or mode == 'surface' and modeling.get('surface_cleanup') != 'bounded-surface/1'):
        raise ValueError('cleanup mode is not advertised by this task\'s pinned modules')
    if verify_bundle(root) != lock:
        raise ValueError('cleanup component bundle differs from the current task pin')
    # Reuse the immutable stage's exact CPU request contract. It imports only
    # the standard library, without activating the neural inference runner.
    contract_path = inside(root, modeling['path'] + '/modeling_flow/contracts.py')
    spec = importlib.util.spec_from_file_location('_pinned_cleanup_contracts', contract_path)
    contracts = importlib.util.module_from_spec(spec); spec.loader.exec_module(contracts)
    with _locked(job):
        source = _source(job, task, lock, arguments['model_ref'], object_hash, contracts)
        request = {**source['request'], 'display_cleanup': mode}
        reference = object_hash({'request': request, 'image_sha256': source['image']['sha256'],
            'runtime_sha256': source['runtime']['config_sha256'], 'module': modeling})
        directory = inside(job, 'work/modeling-images/' + reference)
        if (directory / 'response.json').exists():
            cached = _source(job, task, lock, reference, object_hash, contracts)
            if object_hash({k: source['raw'][k] for k in ('vertices', 'faces')}) != object_hash(
                    {k: cached['raw'][k] for k in ('vertices', 'faces')}):
                raise ValueError('cached cleanup does not retain the exact source raw geometry')
            return {**cached['public'], 'neural_inference_performed': False,
                    'cleanup_reused': True, 'provider_notice': source['runtime'].get('provider_notice', '')}
        if directory.exists():
            return {'ok': False, 'code': 'model_cleanup_incomplete', 'model_ref': reference,
                    'message': 'An incomplete cleanup attempt is retained; explicit recovery is required.',
                    'neural_inference_performed': False}
        directory.mkdir(parents=True)
        atomic(directory / 'request.json', request)
        worker_request = {'schema_version': 'modeling-preview-cleanup-call/1', 'job': str(job),
            'source_model_ref': arguments['model_ref'], 'source_raw': str(source['raw_path'].relative_to(job)),
            'source_raw_sha256': sha256(source['raw_path']),
            'source_receipt': str(source['receipt_path'].relative_to(job)),
            'source_receipt_sha256': sha256(source['receipt_path']),
            'source_request_sha256': sha256(source['request_path']),
            'request': request, 'runtime': source['runtime'], 'modeling_module': modeling}
        call_path = directory / 'cleanup-call.json'; atomic(call_path, worker_request)
        module = inside(root, modeling['path'])
        script = inside(root, 'model_cleanup.py')
        output = directory / 'assets'
        environment = dict(os.environ)
        environment.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
            HF_DATASETS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', PYTHONDONTWRITEBYTECODE='1',
            PYTORCH_ENABLE_MPS_FALLBACK='0')
        log_path = directory / 'cleanup.log'
        started = time.monotonic()
        try:
            completed = subprocess.run([source['runtime']['python_executable'], str(script),
                '--export', '--module-root', str(module), '--request', str(call_path),
                '--output', str(output)], cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=task['limits']['wall_time_seconds'], check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            atomic(log_path, {'schema_version': 1, 'stage': 'cpu-model-cleanup',
                'failure_category': type(error).__name__, 'neural_inference_performed': False})
            raise ValueError('CPU display cleanup/export could not complete; original model is retained') from error
        atomic(log_path, {'schema_version': 1, 'stage': 'cpu-model-cleanup',
            'returncode': completed.returncode, 'neural_inference_performed': False})
        if completed.returncode:
            raise ValueError('CPU display cleanup/export failed; original model is retained')
        # The process must not silently redirect or alter the accepted source.
        _source(job, task, lock, arguments['model_ref'], object_hash, contracts)
        receipt = read_json(inside(output, 'receipt.json'), 4_000_000)
        if (receipt.get('postprocessing', {}).get('neural_inference_performed') is not False
                or receipt.get('postprocessing', {}).get('source_model_ref') != arguments['model_ref']
                or receipt.get('display_cleanup', {}).get('mode') != mode):
            raise ValueError('cleanup export lacks its source and no-inference disclosure')
        for artifact in receipt['artifacts']:
            path = inside(output, artifact['path'])
            if sha256(path) != artifact['sha256'] or path.stat().st_size != artifact['bytes']:
                raise ValueError('cleanup export differs from its artifact receipt')
        public = {'ok': True, 'result_kind': 'model-preview', 'model_ref': reference,
            'display_model_ref': reference, 'receipt_ref': reference, 'ready_to_preview': True,
            'ready_to_simulate': False, 'display_geometry_available': True,
            'simulation_performed': False, 'neural_inference_performed': False,
            'cleanup_reused': False, 'source_model_ref': arguments['model_ref'],
            'raw_geometry_preserved': True,
            'display_geometry_modified': receipt['display_geometry_modified'],
            'display_vertices_modified': receipt['display_vertices_modified'],
            'semantic_fidelity_verified': False, 'display_cleanup': receipt['display_cleanup'],
            'units': source['raw']['units'], 'audit': receipt['display_audit'],
            'cleanup_seconds': round(time.monotonic() - started, 3),
            'assumptions': receipt['assumptions'],
            'next_action': {'tool': 'modeling_preview_render', 'model_ref': reference}}
        pins = [{'path': p.relative_to(job).as_posix(), 'sha256': sha256(p)}
                for p in (*sorted(output.iterdir()), directory / 'request.json') if p.is_file()]
        atomic(directory / 'response.json', {'public_result': public, 'artifact_pins': pins})
        return {**public, 'provider_notice': source['runtime'].get('provider_notice', '')}


def export_cpu(module: Path, call_path: Path, output: Path) -> None:
    """Fixed child entrypoint: numerical cleanup and inert geometry export only."""
    call = read_json(call_path)
    if call.get('schema_version') != 'modeling-preview-cleanup-call/1':
        raise ValueError('invalid cleanup export request')
    job = Path(call['job'])
    output = inside(job, output.relative_to(job).as_posix())
    if output.exists() or output.is_symlink():
        raise ValueError('cleanup export output already exists')
    raw_path = inside(job, call['source_raw'])
    source_receipt = inside(job, call['source_receipt'])
    if sha256(raw_path) != call['source_raw_sha256'] or sha256(source_receipt) != call['source_receipt_sha256']:
        raise ValueError('cleanup source changed before CPU export')
    runtime = call['runtime']
    config_path = inside(job, runtime['config_path'])
    if sha256(config_path) != runtime['config_sha256']:
        raise ValueError('cleanup pinned configuration changed before export')
    # This path comes from the verified bundle, not the user arguments. Import
    # only CPU geometry helpers; the neural runner is intentionally absent.
    sys.path.insert(0, str(module))
    from modeling_flow.contracts import artifact, digest, load_request, write_json
    from modeling_flow.meshes import audit_mesh, write_obj
    import trimesh
    request = load_request(call['request'])
    raw_document = read_json(raw_path, MAX_SOURCE_BYTES)
    raw = {key: raw_document[key] for key in ('vertices', 'faces')}
    if request['display_cleanup'] == 'surface':
        from modeling_flow.surface_cleanup import clean_surface_mesh
        clean, report = clean_surface_mesh(raw)
    else:
        from modeling_flow.display_cleanup import clean_display_mesh
        clean, report = clean_display_mesh(raw, request['display_cleanup'])
    output.mkdir(mode=0o700)
    shutil.copyfile(raw_path, output / 'raw_mesh.json')
    if sha256(output / 'raw_mesh.json') != call['source_raw_sha256']:
        raise ValueError('cleanup did not preserve exact raw JSON bytes')
    for prefix, geometry in (('raw', raw), ('display', clean)):
        write_obj(output / (prefix + '.obj'), geometry, raw_document['units'])
        trimesh.Trimesh(vertices=geometry['vertices'], faces=geometry['faces'], process=False).export(
            str(output / (prefix + '.glb')), file_type='glb')
    assumptions = list(raw_document.get('assumptions', []))
    assumptions.append('CPU-only display postprocessing of previously generated geometry; no new neural inference. Unseen surfaces and semantic fidelity remain unverified.')
    write_json(output / 'display_mesh.json', {**raw_document, **clean,
        'audit': audit_mesh(clean), 'geometry_scope': 'display_postprocessed', 'assumptions': assumptions})
    write_json(output / 'cleanup-receipt.json', report)
    roles = [('display.glb', 'display_mesh', 'model/gltf-binary'),
        ('display.obj', 'display_mesh_source', 'model/obj'),
        ('display_mesh.json', 'display_mesh_data', 'application/json'),
        ('raw.glb', 'raw_glb', 'model/gltf-binary'), ('raw.obj', 'raw_obj', 'model/obj'),
        ('raw_mesh.json', 'raw_mesh_data', 'application/json'),
        ('cleanup-receipt.json', 'cleanup_receipt', 'application/json')]
    receipt = copy.deepcopy(read_json(source_receipt, 4_000_000))
    receipt.update(purpose='model_preview', display_only=True, ready_to_simulate=False,
        simulation_performed=False, numerical_usable=False, raw_geometry_preserved=True,
        display_geometry_modified=report['applied'],
        display_vertices_modified=bool(report['summary'].get('moved_vertices', 0)),
        semantic_fidelity_verified=False, display_audit=audit_mesh(clean),
        configuration_file_sha256=runtime['config_sha256'], request_sha256=digest(request),
        assumptions=assumptions,
        display_cleanup={key: report[key] for key in ('mode', 'applied', 'policy_id', 'summary')},
        postprocessing={'neural_inference_performed': False, 'execution_device': 'cpu',
            'source_model_ref': call['source_model_ref'],
            'source_raw_sha256': call['source_raw_sha256'],
            'source_request_sha256': call['source_request_sha256'],
            'source_receipt_sha256': call['source_receipt_sha256'],
            'modeling_module': {key: call['modeling_module'][key] for key in ('id', 'version', 'digest')}},
        artifacts=[artifact(output / name, output, role, mime) for name, role, mime in roles])
    write_json(output / 'receipt.json', receipt)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='CPU-only cleanup of a sealed model preview')
    parser.add_argument('--export', action='store_true', required=True)
    parser.add_argument('--module-root', type=Path, required=True)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    export_cpu(args.module_root, args.request, args.output)
