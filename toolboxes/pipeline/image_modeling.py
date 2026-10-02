"""Task-confined image reconstruction adapter; never accepts host paths."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from bundle import inside, read_json, sha256


def generate_image_model(root: Path, job: Path, task: dict, lock: dict,
                         arguments: dict, atomic, object_hash, *, preview: bool = False) -> dict:
    runtime = task.get('modeling_runtime')
    if not runtime:
        return {'ok': False, 'code': 'image_modeling_not_configured',
                'message': 'A verified Mac-local flow matching runtime must be installed by the operator.'}
    notice = runtime.get('provider_notice', '')
    allowed = {'image_id', 'physical_extent_m', 'seed', 'num_inference_steps',
               'guidance_scale', 'octree_resolution', 'num_chunks'}
    if not preview:
        allowed.add('material')
    else:
        allowed.add('display_cleanup')
    if set(arguments) - allowed:
        raise ValueError('image modeling accepts attachment IDs and physical parameters only')
    if arguments.get('display_cleanup') == 'surface' and (
            not preview or lock['modules']['modeling'].get('surface_cleanup') != 'bounded-surface/1'
            or lock['modules']['rendering'].get('preview_geometry_scope') != 'render_input'):
        raise ValueError('surface cleanup requires its explicitly pinned modeling capability and renderer')
    images = [image for image in task['request'].get('input_images', [])
              if image.get('id') == arguments.get('image_id')]
    if len(images) != 1:
        raise ValueError('image_id must identify one validated attachment of this event')
    image = images[0]
    image_path = inside(job, image['path'])
    if sha256(image_path) != image['sha256']:
        raise ValueError('input image changed after admission')
    config_path = inside(job, runtime['config_path'])
    if sha256(config_path) != runtime['config_sha256']:
        raise ValueError('pinned modeling runtime configuration changed')
    request = {'schema_version': 'modeling-flow-preview-request/1' if preview else 'modeling-flow-request/1', 'image_path': str(image_path),
               'scale_axis': 'max', **{k: v for k, v in arguments.items() if k != 'image_id'}}
    request.setdefault('seed', 0)
    reference = object_hash({'request': request, 'image_sha256': image['sha256'],
                             'runtime_sha256': runtime['config_sha256'],
                             'module': lock['modules']['modeling']})
    work = job / 'work/modeling-images' / reference
    response = work / 'response.json'
    if response.exists():
        result = read_json(response)
        for entry in result.get('artifact_pins', []):
            if sha256(inside(job, entry['path'])) != entry['sha256']:
                raise ValueError('saved image model changed')
        return {**result['public_result'], 'provider_notice': notice}
    if work.exists():
        return {'ok': False, 'code': 'image_modeling_incomplete', 'model_ref': reference,
                'message': 'An incomplete attempt is retained; explicit recovery is required.'}
    work.mkdir(parents=True)
    request_path = work / 'request.json'
    atomic(request_path, request)
    module = inside(root, lock['modules']['modeling']['path'])
    script = inside(module, 'flow_runner.py')
    output = work / 'assets'
    env = dict(os.environ)
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HY3DGEN_DEBUG='0',
               PYTHONDONTWRITEBYTECODE='1', PYTORCH_ENABLE_MPS_FALLBACK='0',
               HOME=str(job / 'work/tmp'), XDG_CACHE_HOME=str(job / 'work/tmp/cache'),
               HF_HOME=str(job / 'work/tmp/huggingface'))
    started = time.monotonic()
    runner_result = work / 'runner-result.json'
    with (work / 'inference.log').open('w') as log, runner_result.open('w') as stdout:
        completed = subprocess.run([runtime['python_executable'], str(script), 'generate',
            '--config', str(config_path), '--request', str(request_path), '--output', str(output)],
            cwd=work, env=env, input=b'', stdout=stdout, stderr=log,
            timeout=task['limits']['wall_time_seconds'], check=False)
    receipt_path = output / 'receipt.json'
    if not receipt_path.exists():
        detail = read_json(runner_result) if runner_result.stat().st_size else {}
        public = {'ok': False, 'code': detail.get('code', 'image_inference_failed'), 'model_ref': reference,
                  'message': detail.get('error', 'The local model failed before producing verified geometry.')}
        atomic(response, {'public_result': public, 'artifact_pins': []})
        return public
    receipt = read_json(receipt_path, 4_000_000)
    if receipt.get('schema_version') != 'modeling-flow-receipt/1':
        raise ValueError('incompatible image modeling receipt')
    for entry in receipt.get('artifacts', []):
        if sha256(inside(output, entry['path'])) != entry['sha256']:
            raise ValueError('image modeling artifact differs from receipt')
    # An inference receipt describes generated geometry, never measured anatomy,
    # hidden surfaces, physical scale, material identification or CAD accuracy.
    mesh_path = output / 'simulation_mesh.json'
    if preview and completed.returncode == 0 and receipt.get('display_only') is True:
        display = read_json(output / 'display_mesh.json', 128_000_000)
        if (receipt.get('image', {}).get('sha256') != image['sha256']
                or display.get('image_sha256') != image['sha256']
                or display.get('units') not in ('m', 'model_unit')
                or receipt.get('purpose') != 'model_preview'):
            raise ValueError('display-only model receipt is not bound to the current image')
        public = {'ok': True, 'result_kind': 'model-preview', 'model_ref': reference,
                  'ready_to_preview': True, 'ready_to_simulate': False,
                  'display_geometry_available': True, 'simulation_performed': False,
                  'units': display['units'], 'audit': receipt.get('display_audit'),
                  'display_cleanup': receipt.get('display_cleanup'),
                  'raw_geometry_preserved': receipt.get('raw_geometry_preserved', True),
                  'display_geometry_modified': receipt.get('display_geometry_modified', False),
                  'inference_seconds': round(time.monotonic() - started, 3),
                  'assumptions': receipt.get('assumptions', []),
                  'next_action': {'tool': 'modeling_preview_render', 'model_ref': reference}}
    elif completed.returncode or not mesh_path.is_file():
        public = {'ok': False, 'code': receipt.get('code', 'simulation_mesh_rejected'),
                  'model_ref': reference, 'ready_to_simulate': False,
                  'message': 'Generated geometry did not pass the simulation mesh gate.',
                  'audit': receipt.get('simulation_audit', {})}
    else:
        mesh = read_json(mesh_path, 4_000_000)
        from physics_demo.api import call_tool
        # Engine geometry audit additionally checks connectedness/intersections.
        prepared = call_tool('physics_mesh', {'spec_json': json.dumps({
            'type': 'raw', 'vertices': mesh['vertices'], 'triangles': mesh['faces'],
            'provenance': {'source': 'image', 'notes':
                'Flow matching approximation; source image SHA256=' + image['sha256'] +
                '; model receipt SHA256=' + sha256(receipt_path) +
                '; scale/material are explicit assumptions, hidden surfaces are inferred.'}})})
        if not prepared.get('ok'):
            public = {'ok': False, 'code': 'engine_mesh_rejected', 'model_ref': reference,
                      'ready_to_simulate': False, 'audit': prepared}
        else:
            # Match the sorted-key storage writer before creating the legacy
            # mesh_ref, whose verifier hashes the stored object's key order.
            canonical = json.loads(json.dumps(json.loads(prepared['mesh_json']),
                ensure_ascii=False, sort_keys=True, allow_nan=False))
            encoded = json.dumps(canonical, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            mesh_ref = hashlib.sha256(encoded.encode()).hexdigest()
            assets = job / 'work/mesh-assets'
            assets.mkdir(exist_ok=True)
            atomic(assets / (mesh_ref + '.json'), canonical)
            public = {'ok': True, 'model_ref': reference, 'mesh_ref': mesh_ref,
                      'geometry_verified': True, 'ready_to_simulate': False,
                      'audit': prepared.get('audit'), 'material_assumption': arguments['material'],
                      'physical_extent_m': arguments['physical_extent_m'],
                      'mass_estimate_kg': mesh['audit']['volume'] * arguments['material']['density_kg_m3'],
                      'inference_seconds': round(time.monotonic() - started, 3),
                      'assumptions': ['Single-image hidden geometry is generated, not measured.',
                                      'Scale and material come from explicit supplied assumptions.'],
                      'next_action': {'tool': 'physics_patch',
                          'reason': 'Insert mesh_ref into the authored mesh scene, set mass and physical parameters, then physics_prepare.'}}
    pins = [{'path': p.relative_to(job).as_posix(), 'sha256': sha256(p)}
            for p in output.iterdir() if p.is_file() and not p.is_symlink()]
    if public.get('mesh_ref'):
        p = job / 'work/mesh-assets' / (public['mesh_ref'] + '.json')
        pins.append({'path': p.relative_to(job).as_posix(), 'sha256': sha256(p)})
    public.update(display_model_ref=reference, receipt_ref=reference,
                  display_geometry_available=(output / 'display.glb').is_file())
    atomic(response, {'public_result': public, 'artifact_pins': pins})
    return {**public, 'provider_notice': notice}


def used_modeling_assets(job: Path, model: dict, root: Path | None = None) -> list[dict]:
    """Export only image assets whose receipt is referenced by this scene."""
    notes = [entity.get('mesh', {}).get('metadata', {}).get('provenance', {}).get('notes', '')
             for entity in model.get('entities', []) if isinstance(entity, dict)]
    result = []
    directory = job / 'work/modeling-images'
    if directory.is_symlink():
        raise ValueError('invalid image modeling history')
    for response in directory.glob('*/response.json'):
        data = read_json(response, 4_000_000)
        if not data['public_result'].get('ok'):
            continue
        pins = data.get('artifact_pins', [])
        receipts = [item for item in pins if item['path'].endswith('/receipt.json')]
        if len(receipts) != 1 or not any(receipts[0]['sha256'] in note for note in notes):
            continue
        reference = data['public_result']['model_ref']
        for item in pins:
            name = Path(item['path']).name
            if name in ('receipt.json', 'display_mesh.json', 'simulation_mesh.json',
                        'display.glb', 'display.obj', 'simulation.obj'):
                path = inside(job, item['path'])
                if sha256(path) != item['sha256']:
                    raise ValueError('image modeling provenance changed before export')
                result.append({'path': str(path), 'name': 'modeling/' + reference + '/' + name,
                               'sha256': item['sha256']})
    if result and root is not None:
        module_root = inside(root, 'modules/modeling')
        module = read_json(inside(module_root, 'module.json'), 65536)
        licenses = module_root / 'modeling_flow/third_party'
        names = ['Hunyuan3D-LICENSE.txt', 'MLX-port-LICENSE.txt',
                 'U2Net-APACHE-2.0.txt', 'rembg-MIT.txt']
        # An independently pinned 0.2.0 stage predates the PyMeshLab reducer.
        # Only that known legacy stage can omit its new dependency license.
        # Newer/custom reducer stages must fail if their license is missing.
        if (module.get('version') != '0.2.0'
                or (module_root / 'modeling_flow/reduction.py').exists()):
            names.append('PyMeshLab-GPL-3.0.txt')
        for name in names:
            path = inside(licenses, name)
            result.append({'path': str(path), 'name': 'modeling/licenses/' + name, 'sha256': sha256(path)})
    return result
