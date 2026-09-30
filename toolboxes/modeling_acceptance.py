#!/usr/bin/env python3
"""Exercise local image -> mesh -> prepare -> solver -> MP4/ZIP, without QQ.

This uses the real host sandbox and a private test registry. It never opens a
WebSocket, reads real messages, changes live configuration or queues delivery.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'toolboxes'))

from dev_host import HostSourceUnavailable, activate_host_source, resolve_host_source
from build_pipeline import build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--python-base', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--provider-name', required=True)
    parser.add_argument('--steps', type=int, default=30)
    parser.add_argument('--resolution', type=int, default=128)
    parser.add_argument('--host-source', type=Path,
        help='Private QQ project root or src; defaults to QQ_SIMULATOR_HOST_SOURCE or a sibling checkout')
    parser.add_argument('--host-config', type=Path,
        help='Host config for the isolated acceptance job; defaults to selected project/config/agent.json')
    args = parser.parse_args()
    try:
        host = resolve_host_source(args.host_source)
        activate_host_source(host)
    except HostSourceUnavailable as error:
        parser.error(str(error))
    from qq_simulator_agent.config import load_config
    from qq_simulator_agent.input_media import normalize_image, persist_event_image
    from qq_simulator_agent.release_runner import _execute_release_command, build_environment
    from qq_simulator_agent.toolbox import (atomic_json, call_api, command, prepare_task,
                                           publish, validate_result)
    host_config = args.host_config or host.parent / 'config/agent.json'
    if not host_config.is_file():
        parser.error('Host configuration is missing; supply --host-config PATH')
    output = args.output.resolve()
    if output.exists():
        raise ValueError('acceptance output must be a new dedicated directory')
    output.mkdir(parents=True)
    build(ROOT / 'toolboxes/physics', output / 'package')
    config = load_config(host_config)
    config['toolbox']['registry'] = str(output / 'registry')
    config['capabilities']['allowed'].append('send_file')
    config['limits'].update(max_data_bytes=16 * 1024 * 1024, max_video_bytes=16 * 1024 * 1024)
    config['release']['timeout_seconds'] = None
    config['modeling_runtime'] = {'root': str(args.runtime.resolve()),
        'python_executable': str(args.runtime.resolve() / '.venv/bin/python'), 'config': 'config.json',
        'read_roots': [str(args.python_base.resolve())], 'provider_name': args.provider_name}
    pointer = publish(output / 'package', Path(config['toolbox']['registry']), activate=True)
    job = output / 'job'
    for name in ('work', 'artifacts'):
        (job / name).mkdir(parents=True, exist_ok=True)
    image = persist_event_image(job, *normalize_image(args.image.read_bytes()), role='direct_image')
    atomic_json(job / 'request.json', {'schema_version': 1, 'event_id': '0' * 24,
        'message': {'text': 'Offline image modeling acceptance, not a QQ event.', 'input_attachments': [image]}})
    started = time.monotonic()
    def api(operation, arguments):
        result = call_api(config, job, operation, arguments)
        atomic_json(output / (operation + '-response.json'), result)
        if not result.get('ok'):
            raise RuntimeError(operation + ' failed: ' + str(result.get('code', result)))
        print(operation + ' passed', flush=True)
        return result
    generated = api('modeling_from_image', {'image_id': image['id'], 'physical_extent_m': 0.3,
        'material': {'name': 'homogeneous prototype plastic assumption', 'density_kg_m3': 1000},
        'seed': 42, 'num_inference_steps': args.steps, 'octree_resolution': args.resolution, 'num_chunks': 2000})
    api('physics_example', {'name': 'mesh_soft_drop'})
    api('physics_patch', {'operations': [
        {'op': 'replace', 'path': '/entities/@soft/mesh', 'mesh_ref': generated['mesh_ref']},
        {'op': 'add', 'path': '/entities/@soft/mass', 'value_json': json.dumps(generated['mass_estimate_kg'])},
        {'op': 'replace', 'path': '/world/duration', 'value_json': '0.25'}]})
    api('physics_prepare', {})
    api('physics_simulate', {})
    prepare_task(config, job)
    argv, _ = command(config, job, 'run')
    completed = _execute_release_command(argv, job / 'work', build_environment(job), None, config['release'], job)
    if completed.returncode:
        raise RuntimeError('pipeline execution failed; isolated log is retained')
    result = validate_result(job, 16 * 1024 * 1024, 16 * 1024 * 1024)
    report = {'schema_version': 'modeling-acceptance/1', 'passed': True,
        'live_qq_delivery': False, 'live_registry_changed': False, 'network': False,
        'pipeline': pointer, 'elapsed_seconds': time.monotonic() - started,
        'generated': generated, 'video_sha256': __import__('hashlib').sha256(Path(result['video']).read_bytes()).hexdigest(),
        'data_sha256': result['data_attachment']['sha256']}
    atomic_json(output / 'acceptance.json', report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
