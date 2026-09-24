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


def _host_tool_definitions(root: Path) -> list[dict]:
    """Describe the task-confined wrapper, not the unrestricted engine API."""
    definitions = json.loads((root/'tools.json').read_text())
    by_name = {item['name']: item for item in definitions}

    liquid = by_name['physics_liquid']
    liquid['description'] += (
        ' In this toolbox, preserve an example fluid whose preset already matches'
        ' the request and whose properties are explicit. Applying this tool\'s generic'
        ' patch would overwrite those controls.'
    )
    example = by_name['physics_example']
    example['description'] += (
        ' For an unscaled visible ground splash choose droplet_ground_splash;'
        ' for a specified millimetre drop on dry ground choose droplet_ground;'
        ' choose droplet_ground_micro_wet only when a pre-wetted film is allowed;'
        ' for cone impact choose droplet_cone. Preserve explicit dimensions and wetness.'
    )
    for name in ('physics_prepare', 'physics_estimate'):
        definition = by_name[name]
        definition['input_schema']['properties'].pop('budget_seconds', None)
        definition['description'] += ' The toolbox host supplies the execution budget.'
    simulate = by_name['physics_simulate']
    simulate['description'] = (
        'After successful physics_prepare, authorize the host to run its saved scene.'
        ' Call with empty arguments or the exact prepared scene_json. This operation'
        ' returns ready_to_run, not an MP4; call the host release operation next.'
        ' The host assigns output directory and budget.'
    )
    simulate['input_schema']['properties'] = {
        'scene_json': {
            'type': 'string',
            'description': 'Optional exact scene_json returned by physics_prepare.',
        },
    }
    simulate['input_schema']['required'] = []
    inspect = by_name['physics_inspect']
    inspect['description'] = (
        'Inspect this task\'s saved physics result after a run; the host supplies its path.'
    )
    inspect['input_schema']['properties'] = {}
    inspect['input_schema']['required'] = []
    query = by_name['physics_query']
    query['description'] = (
        'Read this task\'s verified, predeclared numerical answers after its run.'
        ' Supply only an optional query_id; the host supplies the saved result path.'
    )
    query['input_schema']['properties'].pop('result_path', None)
    query['input_schema']['required'] = []
    definitions.extend([
        {
            'name': 'context',
            'description': 'Load the previous verified scene for a follow-up in this task, when available.',
            'input_schema': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False},
        },
        {
            'name': 'help',
            'description': 'Read a named toolbox topic. Use topic=tools for these host operation signatures.',
            'input_schema': {
                'type': 'object',
                'properties': {'topic': {'type': 'string'}},
                'required': [],
                'additionalProperties': False,
            },
        },
    ])
    return definitions


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
        if topic == 'tools':
            definitions = _host_tool_definitions(root)
            result = {'ok': True, 'topics': sorted(files),
                      'text': json.dumps(definitions, ensure_ascii=False, indent=2),
                      'tool_definitions': definitions}
        else:
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
