"""Package a task-pinned original image model and its native turntable video."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import time
import zipfile

from bundle import inside, read_json, sha256

SOURCE_LIMIT = 128 * 1024 * 1024


def pin(job: Path, path: Path, **extra) -> dict:
    confined = inside(job, path.relative_to(job).as_posix())
    if not confined.is_file() or not stat.S_ISREG(confined.stat().st_mode):
        raise ValueError('preview source must be a regular task file')
    return {'path': confined.relative_to(job).as_posix(), 'sha256': sha256(confined),
            'size_bytes': confined.stat().st_size, **extra}


def copy_exact(source: Path, target: Path, expected: str) -> None:
    if target.exists():
        if sha256(target) != expected:
            raise ValueError('preview delivery artifact was changed')
        return
    descriptor, temporary = tempfile.mkstemp(prefix='.preview-', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'wb') as out, source.open('rb') as incoming:
            shutil.copyfileobj(incoming, out, 1024 * 1024)
            out.flush(); os.fsync(out.fileno())
        if sha256(Path(temporary)) != expected:
            raise ValueError('preview source changed during export')
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def make_archive(target: Path, members: list[tuple[Path, str, str]], notice: str,
                 max_bytes: int) -> None:
    if target.exists():
        raise ValueError('unsealed preview archive already exists')
    declarations = []
    expanded = 0
    for source, name, role in members:
        size = source.stat().st_size
        expanded += size
        if not 0 < size <= SOURCE_LIMIT or expanded > SOURCE_LIMIT:
            raise ValueError('preview archive exceeds its expanded byte budget')
        declarations.append({'path': name, 'sha256': sha256(source), 'size_bytes': size, 'role': role})
    note = (notice + '\n\nAI-generated single-image shape approximation. Hidden surfaces are inferred.\n'
            'No physics simulation or measurement is supplied. GLB/OBJ are untextured geometry.\n'
            'See display_mesh.json for units; model_unit does not establish real-world dimensions.\n').encode()
    import hashlib
    declarations.append({'path': 'PROVIDER-NOTICE.txt', 'sha256': hashlib.sha256(note).hexdigest(),
                         'size_bytes': len(note), 'role': 'notice'})
    manifest = {'schema_version': 'model-preview-data/1', 'result_kind': 'model-preview',
                'simulation_performed': False, 'numerical_usable': False, 'members': declarations}
    descriptor, temporary = tempfile.mkstemp(prefix='.preview-zip-', dir=target.parent)
    os.close(descriptor)
    def entry(name):
        value = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
        value.create_system = 3; value.external_attr = 0o100600 << 16
        value.compress_type = zipfile.ZIP_DEFLATED
        return value
    try:
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for (source, name, _), declaration in zip(members, declarations):
                with source.open('rb') as incoming, archive.open(entry(name), 'w') as out:
                    shutil.copyfileobj(incoming, out, 1024 * 1024)
                if sha256(source) != declaration['sha256']:
                    raise ValueError('preview source changed during ZIP export')
            archive.writestr(entry('PROVIDER-NOTICE.txt'), note)
            archive.writestr(entry('archive-manifest.json'), json.dumps(manifest, ensure_ascii=False,
                             sort_keys=True, allow_nan=False, separators=(',', ':')).encode())
        if Path(temporary).stat().st_size > max_bytes:
            raise ValueError('preview archive exceeds its delivery byte budget')
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError('preview archive failed complete CRC verification')
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def render_preview(root: Path, engine: Path, job: Path, task: dict, lock: dict,
                   arguments: dict, atomic, object_hash, stage) -> dict:
    if set(arguments) - {'model_ref', 'width', 'height', 'fps', 'duration_seconds', 'pitch_degrees', 'start_yaw_degrees'}:
        raise ValueError('preview rendering accepts a model reference and bounded camera parameters only')
    reference = arguments.get('model_ref')
    if not isinstance(reference, str) or re.fullmatch(r'[0-9a-f]{64}', reference) is None:
        raise ValueError('preview requires the current task model_ref')
    directory = inside(job, 'work/modeling-images/' + reference)
    saved = read_json(directory / 'response.json', 4_000_000)
    public = saved['public_result']
    if (public.get('ok') is not True or public.get('result_kind') != 'model-preview'
            or public.get('ready_to_preview') is not True or public.get('model_ref') != reference):
        raise ValueError('preview requires a completed display-only model')
    pins = saved.get('artifact_pins', [])
    for item in pins:
        if sha256(inside(job, item['path'])) != item['sha256']:
            raise ValueError('saved original model was modified')
    assets = inside(job, 'work/modeling-images/' + reference + '/assets')
    sources = {key: inside(assets, filename) for key, filename in
               (('receipt', 'receipt.json'), ('display_mesh', 'display_mesh.json'),
                ('display_glb', 'display.glb'), ('display_obj', 'display.obj'))}
    if any(not any(p['path'] == path.relative_to(job).as_posix() for p in pins) for path in sources.values()):
        raise ValueError('original model source was not sealed')
    receipt = read_json(sources['receipt'], 4_000_000)
    runtime = task['modeling_runtime']
    request = read_json(directory / 'request.json')
    if (receipt.get('purpose') != 'model_preview' or receipt.get('display_only') is not True
            or receipt.get('simulation_performed') is not False
            or receipt.get('configuration_file_sha256') != runtime['config_sha256']
            or sha256(inside(job, runtime['config_path'])) != runtime['config_sha256']):
        raise ValueError('model preview is not bound to the pinned display-only runtime')
    images = [image for image in task['request'].get('input_images', [])
              if image.get('sha256') == receipt.get('image', {}).get('sha256')
              and str(inside(job, image['path'])) == request.get('image_path')]
    if len(images) != 1:
        raise ValueError('model preview must identify one current admitted image')
    image = images[0]
    if (request.get('schema_version') != 'modeling-flow-preview-request/1'
            or reference != object_hash({'request': request, 'image_sha256': image['sha256'],
                'runtime_sha256': runtime['config_sha256'], 'module': lock['modules']['modeling']})):
        raise ValueError('model reference does not match its exact request and module pin')
    image_path = inside(job, image['path'])
    if sha256(image_path) != image['sha256']:
        raise ValueError('reference image changed')
    source_receipt = image.get('source_receipt')
    source = inside(job, source_receipt['path']) if source_receipt else None
    if source is not None and sha256(source) != source_receipt['sha256']:
        raise ValueError('reference source receipt changed')
    params = {k: v for k, v in arguments.items() if k != 'model_ref'}
    render_ref = object_hash({'model_ref': reference, 'mesh_sha256': sha256(sources['display_mesh']),
                             'parameters': params, 'rendering_module': lock['modules']['rendering']})
    output = inside(job, 'work/modeling-preview/' + reference + '/' + render_ref)
    response = output / 'delivery.json'
    artifacts = inside(job, 'artifacts')
    name = 'model-preview-' + render_ref[:24]
    video, archive = artifacts / (name + '.mp4'), artifacts / (name + '.zip')
    summary = '参考图生成的三维模型旋转展示完成；附 GLB、OBJ、网格与来源资料。未进行物理模拟。'
    def public_result():
        return {'ok': True, 'result_kind': 'model-preview', 'model_ref': reference,
                'video_path': str(video), 'data_path': str(archive), 'summary': summary,
                'simulation_performed': False, 'full_geometry_retained': True,
                'provider_notice': runtime.get('provider_notice', ''), 'next_action': 'qq_video'}
    if response.exists():
        stored = read_json(response)
        required = {p.relative_to(job).as_posix() for p in
                    (video, archive, output / 'result-manifest.json', output / 'render-manifest.json')}
        if not required <= {p['path'] for p in stored.get('artifact_pins', [])}:
            raise ValueError('saved preview lacks sealed delivery artifacts')
        for item in stored['artifact_pins']:
            if sha256(inside(job, item['path'])) != item['sha256']:
                raise ValueError('saved preview delivery was modified')
        if stored.get('public_result') != public_result():
            raise ValueError('saved preview response changed')
        cached = read_json(output / 'result-manifest.json')
        if cached.get('task_id') != task['task_id'] or cached.get('provenance', {}).get('model_ref') != reference:
            raise ValueError('saved preview manifest belongs to a different model')
        atomic(artifacts / 'result-manifest.json', cached)
        return public_result()
    if output.exists():
        raise ValueError('unsealed preview output retained; explicit recovery is required')
    started = time.monotonic()
    rendered = stage(root, engine, job, lock, 'rendering', {
        'mesh_path': str(sources['display_mesh']), 'output_dir': str(output), 'parameters': params,
        'expected_mesh_sha256': sha256(sources['display_mesh']),
        'budget_seconds': task['limits']['wall_time_seconds'],
        'max_output_bytes': task['limits']['max_output_bytes']}, started, task, action='model_preview')
    proof_path = inside(job, Path(rendered['manifest_path']).relative_to(job).as_posix())
    proof = read_json(proof_path)
    if (proof.get('source_mesh_sha256') != sha256(sources['display_mesh'])
            or proof.get('full_geometry_retained') is not True or proof.get('simulation_performed') is not False):
        raise ValueError('preview renderer did not preserve the original geometry')
    artifacts.mkdir(exist_ok=True)
    original_video = inside(job, Path(proof['video']['path']).relative_to(job).as_posix())
    copy_exact(original_video, video, proof['video']['sha256'])
    members = [(sources[k], 'model/' + sources[k].name, role) for k, role in
               (('display_glb', 'display_glb'), ('display_obj', 'display_obj'),
                ('display_mesh', 'display_mesh_data'), ('receipt', 'modeling_receipt'))]
    members += [(image_path, 'reference/' + image_path.name, 'reference_image'),
                (proof_path, 'render/render-manifest.json', 'render_receipt')]
    if source_receipt:
        members.append((source, 'reference/source-receipt.json', 'source_receipt'))
    licenses = inside(root, lock['modules']['modeling']['path'] + '/modeling_flow/third_party')
    for path in sorted(licenses.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise ValueError('invalid pinned modeling license')
        members.append((path, 'licenses/' + path.name, 'license'))
    make_archive(archive, members, runtime.get('provider_notice', ''), task['delivery']['max_data_bytes'])
    media = proof['media']
    toolbox = read_json(job / 'toolbox-pin.json')
    manifest = {'schema_version': 1, 'result_schema': 'model-preview-result/1',
        'result_kind': 'model-preview', 'status': 'succeeded', 'task_id': task['task_id'],
        'summary': summary,
        'verification': {'passed': True, 'numerical_passed': False, 'simulation_performed': False,
                         'video_decode': {k: media[k] for k in
                         ('all_frames_decoded', 'codec', 'frame_count', 'width', 'height', 'duration_s')}},
        'outputs': [dict(pin(job, video, media_type='video/mp4', role='model_preview_video'), path=video.name)],
        'attachments': [dict(pin(job, archive, media_type='application/zip', role='data',
                                  result_kind='model-preview'), path=archive.name)],
        'provenance': {'toolbox': {k: toolbox[k] for k in ('id', 'version', 'digest')},
            'modeling_module': {k: lock['modules']['modeling'][k] for k in ('id', 'version', 'digest')},
            'runtime_config_sha256': runtime['config_sha256'], 'model_ref': reference,
            'image': pin(job, image_path, id=image['id']),
            **{k: pin(job, sources[k]) for k in ('receipt', 'display_mesh', 'display_glb')},
            'render_receipt': pin(job, proof_path)}}
    manifest_path = output / 'result-manifest.json'
    atomic(manifest_path, manifest)
    atomic(artifacts / 'result-manifest.json', manifest)
    result = public_result()
    sealed = [pin(job, path) for path in (*sources.values(), image_path, proof_path, original_video,
                                         video, archive, manifest_path)]
    if source is not None:
        sealed.append(pin(job, source))
    atomic(response, {'public_result': result, 'artifact_pins': sealed})
    return result
