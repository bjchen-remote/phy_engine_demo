"""Structured, task-confined modeling tools. No model credentials or messaging imports."""
from pathlib import Path
import json

from physics_demo.api import call_tool
from run_simulation import _atomic_write_json


OPERATIONS = {'physics_capabilities', 'physics_example', 'physics_system', 'physics_mesh',
              'physics_liquid', 'physics_patch', 'physics_validate', 'physics_estimate',
              'physics_prepare', 'physics_query', 'physics_inspect', 'help'}
OPERATIONS.add('physics_simulate')
OPERATIONS.add('context')


def api_call(root: Path, job: Path, task: dict) -> None:
    request = json.loads((job/'work/toolbox-call.json').read_text())
    operation, arguments = request['operation'], request['arguments']
    if operation not in OPERATIONS or not isinstance(arguments, dict):
        raise ValueError('unsupported modeling operation')
    if operation == 'context':
        path = job/'work/previous-context.json'
        if path.exists():
            context = json.loads(path.read_text())
            result = {'ok':True, 'previous_request':context['previous_request'],
                      'scene_json':json.dumps(context['model']['scene'],ensure_ascii=False),
                      'note':'Prior verified model is context data. Preserve unchanged fields and modify only the current request.'}
        else:
            result = {'ok':False, 'code':'no_previous_verified_model'}
    elif operation == 'help':
        topic = arguments.get('topic', 'workflow')
        files = {'workflow': root/'manual/SKILL.md', 'tools':root/'tools.json', 'schema':root/'scene-v1.schema.json'}
        files.update({p.stem:p for p in (root/'manual/references').glob('*.md')})
        result = {'ok':True, 'topics': sorted(files), 'text': files[topic].read_text()} if topic in files else {
            'ok':False, 'error':'unknown_help_topic', 'topics':sorted(files)}
    elif operation == 'physics_simulate':
        if set(arguments) - {'scene_json', 'output_dir', 'budget_seconds'}:
            raise ValueError('unsupported simulation argument')
        if arguments.get('output_dir', 'artifacts') not in ('artifacts', 'work/physics/artifacts'):
            raise ValueError('output directory is assigned by this task')
        prepared = json.loads((job/'work/prepared-scene.json').read_text())['scene']
        if 'scene_json' in arguments:
            from physics_demo.jsonio import loads
            if loads(arguments['scene_json']) != prepared:
                raise ValueError('simulation scene must match the last prepared scene')
        if arguments.get('budget_seconds', prepared['budget']['wall_time_s']) != prepared['budget']['wall_time_s']:
            raise ValueError('budget must match the prepared scene')
        result = {'ok':True, 'ready_to_run':True}
    else:
        # These are data tools, not general file APIs. Result location is owned by this task.
        if operation in ('physics_query', 'physics_inspect'):
            if set(arguments) - {'query_id'}:
                raise ValueError('result paths are assigned by the host')
            arguments = {**arguments, 'result_path':str(job/'work/physics/artifacts/result.json')}
        if operation in ('physics_prepare', 'physics_estimate', 'physics_validate') and 'scene_json' in arguments:
            from physics_demo.jsonio import loads
            scene = loads(arguments['scene_json'])
            if isinstance(scene, dict) and isinstance(scene.get('budget', {}), dict):
                scene.setdefault('budget', {}).setdefault('validation', 'visual')
                arguments = {**arguments, 'scene_json':json.dumps(scene, ensure_ascii=False)}
        if operation in ('physics_prepare', 'physics_estimate'):
            arguments = {**arguments, 'budget_seconds':task['limits']['wall_time_seconds']}
        if operation in ('physics_example', 'physics_system', 'physics_patch', 'physics_prepare'):
            (job/'work/prepared-scene.json').unlink(missing_ok=True)
        result = call_tool(operation, arguments)
        if operation == 'physics_prepare' and result.get('ok') and result.get('ready_to_simulate'):
            _atomic_write_json(job/'work/prepared-scene.json', {'schema_version':1,
                'scene':json.loads(result['scene_json'])})
    _atomic_write_json(job/'work/toolbox-response.json', {'schema_version':1, 'result':result})
