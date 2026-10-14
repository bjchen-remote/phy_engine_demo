"""Structured, task-confined modeling tools. No model credentials or messaging imports."""
from pathlib import Path
import json
import math
import hashlib
import re

from physics_demo.api import call_tool
from run_simulation import _atomic_write_json


OPERATIONS = {'physics_capabilities', 'physics_example', 'physics_system', 'physics_mesh',
              'physics_liquid', 'physics_patch', 'physics_validate', 'physics_estimate',
              'physics_prepare', 'physics_query', 'physics_inspect', 'help'}
OPERATIONS.add('physics_simulate')
OPERATIONS.add('context')
OPERATIONS.update({'pcb_example', 'pcb_validate', 'pcb_prepare', 'pcb_inspect', 'pcb_query'})


def _pcb_spec(arguments: dict) -> dict:
    if set(arguments) != {'spec_json'} or not isinstance(arguments['spec_json'], str):
        raise ValueError('pcb model requires spec_json')
    return json.loads(arguments['spec_json'])


def _pcb_report(result: dict) -> dict:
    return {key: result[key] for key in (
        'mode', 'duration_s', 'min_temperature_c', 'max_temperature_c',
        'mean_temperature_c', 'total_power_w', 'heat_rejection_w',
        'rejected_energy_j', 'stored_energy_j', 'energy_balance', 'solver', 'warnings')}


def _finite_pcb_coordinate(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _owned_json(path: Path) -> dict:
    if path.is_symlink() or path.stat().st_size > 1_000_000:
        raise ValueError('invalid task-owned model data')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('invalid task-owned model data')
    return value


def _mesh_value(job: Path, reference: str) -> str:
    if not isinstance(reference, str) or not re.fullmatch(r'[a-f0-9]{64}', reference):
        raise ValueError('invalid mesh_ref')
    directory = job/'work/mesh-assets'
    if directory.is_symlink():
        raise ValueError('invalid mesh asset directory')
    mesh = _owned_json(directory/f'{reference}.json')
    encoded = json.dumps(mesh, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    if hashlib.sha256(encoded.encode()).hexdigest() != reference:
        raise ValueError('mesh_ref integrity check failed')
    return encoded


def api_call(root: Path, job: Path, task: dict) -> None:
    request = json.loads((job/'work/toolbox-call.json').read_text())
    operation, arguments = request['operation'], request['arguments']
    if operation not in OPERATIONS or not isinstance(arguments, dict):
        raise ValueError('unsupported modeling operation')
    draft = job/'work/draft-scene.json'
    if operation in ('physics_patch', 'physics_validate', 'physics_estimate', 'physics_prepare'):
        if 'scene_json' not in arguments:
            arguments = {**arguments, 'scene_json':json.dumps(_owned_json(draft), ensure_ascii=False)}
        if operation == 'physics_patch':
            operations = []
            for item in arguments.get('operations', []):
                if isinstance(item, dict) and 'mesh_ref' in item:
                    if (set(item) != {'op', 'path', 'mesh_ref'}
                            or item['op'] not in ('add', 'replace')
                            or not isinstance(item['path'], str)
                            or not re.fullmatch(r'/entities/@[^/]+/mesh', item['path'])):
                        raise ValueError('mesh_ref is allowed only for an entity mesh insertion')
                    item = {'op':item['op'], 'path':item['path'],
                            'value_json':_mesh_value(job, item['mesh_ref'])}
                operations.append(item)
            arguments = {**arguments, 'operations':operations}
    if operation == 'context':
        path = job/'work/previous-context.json'
        if path.exists():
            context = json.loads(path.read_text())
            saved = context['model']
            domain = saved.get('domain', 'physics')
            key = 'spec_json' if domain == 'pcb_thermal' else 'scene_json'
            result = {'ok':True, 'previous_request':context['previous_request'],
                      'domain':domain, key:json.dumps(saved['scene'],ensure_ascii=False),
                      'note':'Prior verified model is context data. Preserve unchanged fields and modify only the current request.'}
        else:
            result = {'ok':False, 'code':'no_previous_verified_model'}
    elif operation == 'help':
        topic = arguments.get('topic', 'workflow')
        files = {'workflow': root/'manual/SKILL.md', 'tools':root/'tools.json', 'schema':root/'scene-v1.schema.json'}
        files.update({p.stem:p for p in (root/'manual/references').glob('*.md')})
        result = {'ok':True, 'topics': sorted(files), 'text': files[topic].read_text()} if topic in files else {
            'ok':False, 'error':'unknown_help_topic', 'topics':sorted(files)}
    elif operation == 'pcb_example':
        if arguments:
            raise ValueError('pcb_example takes no arguments')
        example = json.loads((root/'pcb_thermal_demo.json').read_text())
        result = {'ok':True, 'spec_json':json.dumps(example, ensure_ascii=False)}
    elif operation in ('pcb_validate', 'pcb_prepare'):
        from pcb_thermal import validate_pcb_spec
        if operation == 'pcb_prepare':
            (job/'work/prepared-scene.json').unlink(missing_ok=True)
        try:
            spec = validate_pcb_spec(_pcb_spec(arguments))
            steps = (1 if spec['mode'] == 'steady' else
                     math.ceil(spec['transient']['duration_s'] / spec['transient']['time_step_s']))
            cells = spec['grid']['nx'] * spec['grid']['ny']
            frames = 1 if spec['mode'] == 'steady' else spec['transient']['snapshot_count']
            estimate = max(25.0, min(300.0, 15.0 + cells * steps / 4000.0 + frames * 0.6))
            budget = task['limits']['wall_time_seconds']
            ready = budget is None or estimate <= budget
            result = {'ok':True, 'ready_to_simulate':ready,
                      'code':'ready' if ready else 'insufficient_time_budget',
                      'spec_json':json.dumps(spec, ensure_ascii=False),
                      'agent_report':{'grid_cells':cells, 'time_steps':steps,
                                      'video_frames':frames, 'estimated_wall_time_s':{'p90':estimate},
                                      'hard_limit_s':budget,
                                      'model':'2D effective thermal sheet with prescribed component powers'}}
            if operation == 'pcb_prepare' and ready:
                _atomic_write_json(job/'work/prepared-scene.json',
                                   {'schema_version':1, 'domain':'pcb_thermal', 'scene':spec,
                                    'estimated_seconds':estimate})
        except (ValueError, TypeError, KeyError) as error:
            result = {'ok':False, 'ready_to_simulate':False,
                      'code':'invalid_pcb_model', 'error':str(error)}
    elif operation in ('pcb_inspect', 'pcb_query'):
        if set(arguments) - ({'x_m', 'y_m'} if operation == 'pcb_query' else set()):
            raise ValueError('unsupported PCB result query')
        path = job/'work/pcb/result.json'
        if not path.is_file():
            result = {'ok':False, 'code':'no_pcb_result'}
        else:
            saved = json.loads(path.read_text())
            result = {'ok':True, 'report':_pcb_report(saved)}
            if operation == 'pcb_query':
                if set(arguments) != {'x_m', 'y_m'}:
                    raise ValueError('pcb_query requires x_m and y_m')
                x, y = arguments['x_m'], arguments['y_m']
                board, grid = saved['board'], saved['grid']
                if (not all(_finite_pcb_coordinate(value) for value in (x, y)) or
                        not (0 <= x < board['width_m'] and 0 <= y < board['height_m'])):
                    raise ValueError('query coordinate lies outside the board')
                i = min(grid['nx'] - 1, int(x / board['width_m'] * grid['nx']))
                j = min(grid['ny'] - 1, int(y / board['height_m'] * grid['ny']))
                result['sample'] = {'x_m':x, 'y_m':y, 'cell_x':i, 'cell_y':j,
                                    'temperature_c':saved['temperature_c'][j][i],
                                    'power_w':saved['power_w'][j][i]}
    elif operation == 'physics_simulate':
        if set(arguments) - {'scene_json', 'spec_json', 'output_dir', 'budget_seconds'}:
            raise ValueError('unsupported simulation argument')
        if arguments.get('output_dir', 'artifacts') not in ('artifacts', 'work/physics/artifacts'):
            raise ValueError('output directory is assigned by this task')
        prepared_model = json.loads((job/'work/prepared-scene.json').read_text())
        prepared = prepared_model['scene']
        if prepared_model.get('domain') == 'pcb_thermal':
            if 'scene_json' in arguments:
                raise ValueError('PCB execution requires spec_json, not scene_json')
            if 'spec_json' in arguments and _pcb_spec({'spec_json':arguments['spec_json']}) != prepared:
                raise ValueError('PCB model must match the last prepared model')
            if (task['limits']['wall_time_seconds'] is not None and
                    'budget_seconds' in arguments and arguments['budget_seconds'] != task['limits']['wall_time_seconds']):
                raise ValueError('budget must match the host task limit')
        else:
            if 'spec_json' in arguments:
                raise ValueError('mechanical execution requires scene_json')
            if 'scene_json' in arguments:
                from physics_demo.jsonio import loads
                if loads(arguments['scene_json']) != prepared:
                    raise ValueError('simulation scene must match the last prepared scene')
            if (task['limits']['wall_time_seconds'] is not None and
                    arguments.get('budget_seconds', prepared['budget']['wall_time_s']) != prepared['budget']['wall_time_s']):
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
        if operation in ('physics_prepare', 'physics_estimate') and task['limits']['wall_time_seconds'] is not None:
            arguments = {**arguments, 'budget_seconds':task['limits']['wall_time_seconds']}
        elif operation in ('physics_prepare', 'physics_estimate', 'physics_simulate') and task['limits']['wall_time_seconds'] is None:
            # The host owns this mode; a model-supplied budget must not restore
            # a deadline after the task was accepted as unbounded.
            arguments = {key:value for key,value in arguments.items() if key != 'budget_seconds'}
        if operation in ('physics_example', 'physics_system', 'physics_patch', 'physics_prepare'):
            (job/'work/prepared-scene.json').unlink(missing_ok=True)
        result = call_tool(operation, arguments,
                           unlimited=task['limits']['wall_time_seconds'] is None)
        if operation == 'physics_prepare' and result.get('ok') and result.get('ready_to_simulate'):
            _atomic_write_json(job/'work/prepared-scene.json', {'schema_version':1,
                'scene':json.loads(result['scene_json'])})
        if result.get('ok') and operation == 'physics_mesh':
            mesh = json.loads(result['mesh_json'])
            encoded = json.dumps(mesh, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            if len(encoded.encode()) > 1_000_000:
                raise ValueError('mesh exceeds the model data limit')
            reference = hashlib.sha256(encoded.encode()).hexdigest()
            directory = job/'work/mesh-assets'
            if directory.is_symlink():
                raise ValueError('invalid mesh asset directory')
            directory.mkdir(exist_ok=True, mode=0o700)
            _atomic_write_json(directory/f'{reference}.json', mesh)
            result['mesh_ref'] = reference
            result['next_action']['reason'] = (
                'Insert mesh_ref through physics_patch on the current authored scene; '
                'then physics_prepare, physics_simulate and qq_video. Geometry generation alone is incomplete.')
            if len(encoded.encode()) > 20000:
                result.pop('mesh_json')
        if result.get('ok') and operation in (
                'physics_example', 'physics_system', 'physics_patch', 'physics_validate', 'physics_prepare'):
            scene = result.get('scene')
            if scene is None and isinstance(result.get('scene_json'), str):
                scene = json.loads(result['scene_json'])
            if isinstance(scene, dict):
                _atomic_write_json(draft, scene)
                result['scene_saved'] = True
                if len(json.dumps(scene).encode()) > 20000:
                    result.pop('scene', None)
                    result.pop('scene_json', None)
    _atomic_write_json(job/'work/toolbox-response.json', {'schema_version':1, 'result':result})
