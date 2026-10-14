#!/usr/bin/env python3
"""Validate design contracts only; no solver, network, registry or delivery calls.

Requires jsonschema. Fixtures have fictional IDs/hashes and no actual assets.
Cross-field checks exercise the design, not runtime authority or geometry proofs.
"""
import argparse
import copy
import json
import re
from pathlib import Path

from contract_semantics import validate_semantics

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--semantic-only', action='store_true',
    help='Use only stdlib fixture/semantic/reference checks; explicitly skip Draft 2020-12.')
options = parser.parse_args()
schema = json.loads((root / 'contracts.schema.json').read_text())
validator = None
if options.semantic_only:
    print('SKIPPED: Draft 2020-12 schema validation and schema-negative checks (--semantic-only).')
else:
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ModuleNotFoundError as error:
        parser.exit(2, 'jsonschema is required for formal validation; use --semantic-only for explicitly limited stdlib checks.\n')
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
paths = sorted((root / 'examples').glob('*.json')) + sorted((root / 'toolboxes').glob('*.json'))
fixtures = {path.name: json.loads(path.read_text()) for path in paths}
bundles = {item['bundle_id']: item for item in fixtures.values() if item.get('schema_version') == 'bundle-manifest/1'}
for path in paths:
    item = fixtures[path.name]
    if validator is not None:
        errors = sorted(validator.iter_errors(item), key=lambda error: str(error.path))
        assert not errors, (path, errors[0].message)
    validate_semantics(item, bundles, fixtures['plan-response.json']['data']['plan'])
print(f'{len(paths)} positive JSON fixtures/contracts parsed and passed design semantic checks.' +
      (' Draft 2020-12 passed.' if validator is not None else ' Schema validity NOT checked.'))


def args_validator(operation):
    return Draft202012Validator({'$schema': schema['$schema'],
        '$ref': '#/$defs/args_' + operation, '$defs': schema['$defs']}, format_checker=FormatChecker())


api = json.loads((root / 'api-tools.json').read_text())
assert len(api['tools']) == 19 and len({tool['name'] for tool in api['tools']}) == 19
for tool in api['tools']:
    for key in ('input_schema', 'output_schema'):
        assert tool[key]['$ref'].split('#/$defs/')[1] in schema['$defs']
for path in (root / 'toolboxes').glob('*.json'):
    contract = json.loads(path.read_text())
    assert set(contract['operations']) == {tool['name'] for tool in api['tools'] if tool['owner'] == contract['role']}
    assert (path.parent / contract['skill_source']).resolve().is_file()
for path in (root / 'examples').glob('*response.json'):
    action = fixtures[path.name]['next_action']
    if action['kind'] in ('call_tool', 'poll'):
        if validator is not None:
            args_validator(action['tool']).validate(action['arguments'])
print('19 unchanged API names, three toolbox operation sets and API schema references resolve.' +
      (' next_action arguments validate.' if validator is not None else ' next_action argument schema checks skipped.'))

base = fixtures['pipeline-request.json']
plan_result = fixtures['plan-response.json']
preview = fixtures['model-preview-request.json']
preview_model = fixtures['model-preview-model-bundle.json']
physical_render = fixtures['physical-render-bundle.json']

# Standalone modeling still needs no solver/renderer module or fabricated state.
model_only = copy.deepcopy(plan_result)
plan = model_only['data']['plan']
plan['kind'] = 'modeling'
plan['lock']['modules'] = {'modeling': plan['lock']['modules']['modeling']}
plan['lock']['dependencies'] = {}
plan['quality_requested'] = {'modeling': 'standard'}
plan['quality_resolved'] = {'modeling': 'standard'}
plan['state_plan'] = None
plan['permitted_stages'] = ['modeling']
plan['effective_arguments'] = {'spec': base['model'], 'quality': 'standard'}
plan['delivery'] = {'required_roles': ['model'], 'include_model': True, 'data_formats': ['json'], 'allow_partial': False}
model_only['stage'] = 'modeling'
model_only['next_action']['tool'] = 'modeling_build'
if validator is not None:
    validator.validate(model_only)
validate_semantics(model_only, bundles)

# Both render purposes retain rendering_prepare; preview supplies no simulation_ref.
render_args = {'purpose': 'model_preview', 'model_ref': fixtures['model-preview-render-bundle.json']['metadata']['model_ref'],
               'spec': preview['render'], 'quality': 'standard'}
if validator is not None:
    args_validator('rendering_prepare').validate(render_args)
physical_args = {'purpose': 'physical_simulation', 'model_ref': physical_render['metadata']['model_ref'],
                 'simulation_ref': physical_render['metadata']['simulation_ref'], 'spec': base['render'], 'quality': 'standard'}
if validator is not None:
    args_validator('rendering_prepare').validate(physical_args)
print('Standalone modeling semantic checks passed.' +
      (' Both purposes of existing rendering_prepare validate.' if validator is not None else ' Rendering argument schema checks skipped.'))

negative = []

def mutation(name, fixture, change):
    value = copy.deepcopy(fixture)
    change(value)
    negative.append((name, value))

mutation('mixed quality', base, lambda x: x['quality'].__setitem__('simulation', 'deep'))
mutation('unknown model key', base, lambda x: x['model'].__setitem__('shell', 'rm'))
mutation('invalid FPS', base, lambda x: x['render'].__setitem__('fps', 0))
mutation('empty stages', base, lambda x: x.__setitem__('permitted_stages', []))
mutation('unknown profile', base, lambda x: x['simulation'].__setitem__('state_export', 'pretend_full'))
mutation('path escape', fixtures['simulation-bundle.json'], lambda x: x['files'][0].__setitem__('path', '../outside'))
mutation('invalid digest', fixtures['simulation-bundle.json'], lambda x: x['files'][0].__setitem__('sha256', 'fake'))
mutation('failure without error', fixtures['insufficient-cache-response.json'], lambda x: x.__setitem__('errors', []))
mutation('bad poll action', plan_result, lambda x: x.__setitem__('next_action', {'kind': 'poll', 'tool': 'rendering_render', 'arguments': {}, 'poll_after_ms': 1}))
mutation('respond with executable tool', fixtures['insufficient-cache-response.json'], lambda x: x['next_action'].__setitem__('tool', 'simulation_run'))
mutation('fabricated modeling state', model_only, lambda x: x['data']['plan'].__setitem__('state_plan', plan_result['data']['plan']['state_plan']))
mutation('fictional steady duration', fixtures['steady-request.json'], lambda x: x['simulation'].__setitem__('physical_duration_s', 10))
mutation('premature rendering quality', fixtures['simulation-bundle.json'], lambda x: x['quality']['actual'].__setitem__('rendering', 'deep'))
mutation('preview fabricated simulation', preview, lambda x: x.__setitem__('simulation', base['simulation']))
mutation('preview simulation stage', preview, lambda x: x['permitted_stages'].append('simulation'))
mutation('preview simulation quality', preview, lambda x: x['quality'].__setitem__('simulation', 'visual'))
mutation('preview model-purpose mismatch', preview, lambda x: x['model'].__setitem__('purpose', 'physical_simulation'))
mutation('preview physical fidelity', preview, lambda x: x['render'].__setitem__('fidelity', 'trajectory_preserving'))
mutation('physical missing simulation', base, lambda x: x.pop('simulation'))
mutation('preview fabricated state plan', fixtures['model-preview-plan-response.json'], lambda x: x['data']['plan'].__setitem__('state_plan', plan_result['data']['plan']['state_plan']))
mutation('missing engine dependency', fixtures['pipeline-lock.json'], lambda x: x['dependencies'].pop('engine'))
mutation('invalid engine digest', fixtures['pipeline-lock.json'], lambda x: x['dependencies']['engine'].__setitem__('digest', 'fake'))
mutation('stage missing engine dependency', fixtures['stage-task.json'], lambda x: x['dependencies'].pop('engine'))
mutation('untyped model metadata', preview_model, lambda x: x.__setitem__('metadata', {'purpose': 'model_preview'}))
mutation('unknown binding kind', preview_model, lambda x: x['metadata']['binding'].__setitem__('kind', 'trust_worker'))
mutation('binding missing topology pin', preview_model, lambda x: x['metadata']['binding']['source'].pop('topology_sha256'))
mutation('preview simulation asset', preview_model, lambda x: x['metadata'].__setitem__('simulation_asset', fixtures['physical-model-bundle.json']['metadata']['simulation_asset']))
mutation('preview claims numerical pass', fixtures['model-preview-render-bundle.json'], lambda x: x['validation'].__setitem__('numerical', 'passed'))
mutation('preview render simulation ref', fixtures['model-preview-render-bundle.json'], lambda x: x['metadata'].__setitem__('simulation_ref', physical_render['metadata']['simulation_ref']))
mutation('physical render missing simulation', physical_render, lambda x: x['metadata'].pop('simulation_ref'))
mutation('preview data simulation ref', fixtures['model-preview-data-package.json'], lambda x: x.__setitem__('simulation_ref', physical_render['metadata']['simulation_ref']))
mutation('sampled population lacks selection', fixtures['simulation-bundle.json'], lambda x: x['metadata']['state_plan'].__setitem__('population', 'sampled'))
mutation('full-state sampled population', fixtures['simulation-bundle.json'], lambda x: x['metadata']['state_plan'].update(profile='full_state', population='sampled'))
if validator is not None:
    for name, value in negative:
        assert not validator.is_valid(value), name
    wrong_query = {'simulation_ref': {'artifact_ref': 'example-model', 'schema': 'ModelBundle/1', 'sha256': 'a' * 64}, 'query_id': 'period'}
    assert not args_validator('simulation_query').is_valid(wrong_query)
    wrong_args = copy.deepcopy(render_args)
    wrong_args['simulation_ref'] = physical_args['simulation_ref']
    assert not args_validator('rendering_prepare').is_valid(wrong_args)
    wrong_args = copy.deepcopy(physical_args)
    wrong_args.pop('simulation_ref')
    assert not args_validator('rendering_prepare').is_valid(wrong_args)
    print(f'{len(negative) + 3} schema-negative mutations rejected.')

# These remain structurally valid: the separate semantic check must reject them.
semantic_negative = []

def semantic_mutation(name, fixture, change):
    value = copy.deepcopy(fixture)
    change(value)
    semantic_negative.append((name, value))

semantic_mutation('duplicate artifact id', preview_model, lambda x: x['files'][1].__setitem__('artifact_id', x['files'][0]['artifact_id']))
semantic_mutation('duplicate artifact path', preview_model, lambda x: x['files'][1].__setitem__('path', x['files'][0]['path']))
semantic_mutation('binding hash mismatch', preview_model, lambda x: x['metadata']['binding']['target'].__setitem__('sha256', '0' * 64))
semantic_mutation('binding file hash mismatch', preview_model, lambda x: x['files'][0].__setitem__('sha256', '0' * 64))
semantic_mutation('identity has unequal geometry', preview_model, lambda x: (x['metadata']['render_asset'].__setitem__('sha256', '0' * 64), x['metadata']['binding']['target'].__setitem__('sha256', '0' * 64), x['files'][1].__setitem__('sha256', '0' * 64)))
semantic_mutation('geometry error exceeds tolerance', preview_model, lambda x: x['metadata']['binding']['error_report'].update(value=1, tolerance=0))
semantic_mutation('state time reversed', fixtures['simulation-bundle.json'], lambda x: x['metadata']['state_plan'].__setitem__('time_window_s', [8, 0]))
semantic_mutation('state profile mismatch', fixtures['simulation-bundle.json'], lambda x: x['metadata'].__setitem__('state_export', 'full_state'))
semantic_mutation('duplicate state field', fixtures['simulation-bundle.json'], lambda x: x['metadata']['state_plan']['fields'].append(copy.deepcopy(x['metadata']['state_plan']['fields'][0])))
semantic_mutation('render wrong model hash', physical_render, lambda x: (x['metadata']['model_ref'].__setitem__('sha256', '0' * 64), x['inputs'][0].__setitem__('sha256', '0' * 64)))
semantic_mutation('render engine mismatch', physical_render, lambda x: x['metadata']['engine_dependency'].__setitem__('version', 'other-engine'))
semantic_mutation('render binding mismatch', physical_render, lambda x: x['metadata']['binding']['target'].__setitem__('topology_sha256', '0' * 64))
semantic_mutation('preview hidden simulation input', fixtures['model-preview-render-bundle.json'], lambda x: x['inputs'].append(physical_args['simulation_ref']))
semantic_mutation('plan purpose mismatch', model_only, lambda x: x['data']['plan'].__setitem__('purpose', 'model_preview'))
semantic_mutation('missing required stage pin', plan_result, lambda x: x['data']['plan']['lock']['modules'].pop('rendering'))
semantic_mutation('stage engine differs from accepted plan', fixtures['stage-task.json'], lambda x: x['dependencies']['engine'].__setitem__('digest', '0' * 64))
semantic_mutation('model engine differs from accepted plan', fixtures['physical-model-bundle.json'], lambda x: x['metadata']['engine_dependency'].__setitem__('version', 'other-engine'))
for name, value in semantic_negative:
    if validator is not None:
        validator.validate(value)
    try:
        validate_semantics(value, bundles, plan_result['data']['plan'])
    except ValueError:
        pass
    else:
        raise AssertionError(name)
print(f'{len(semantic_negative)} cross-field/dependency mutations rejected by design semantic checks.' +
      (' Each is schema-valid.' if validator is not None else ' Their structural validity was NOT checked.'))

missing = []
uninspected_evidence = []
for path in root.rglob('*.md'):
    for target in re.findall(r'\]\(([^)]+)\)', path.read_text()):
        if '://' in target or target.startswith('#'):
            continue
        target = target.split('#')[0]
        if target.startswith('../../runs/'):
            uninspected_evidence.append((str(path.relative_to(root)), target))
            continue  # Historical private/generated evidence is outside source-only validation.
        if target and not (path.parent / target).exists():
            missing.append((str(path.relative_to(root)), target))
assert not missing, missing
reference_count = 0

def refs(node):
    global reference_count
    if isinstance(node, dict):
        if '$ref' in node and node['$ref'].startswith('#/$defs/'):
            assert node['$ref'].split('/')[-1] in schema['$defs'], node['$ref']
            reference_count += 1
        for value in node.values():
            refs(value)
    elif isinstance(node, list):
        for value in node:
            refs(value)

refs(schema)
print(f'{reference_count} internal schema references and all source Markdown links resolve.')
print(f'{len(uninspected_evidence)} historical runs/ evidence links deliberately not inspected.')
print('Design checks only: no asset bytes/hashes, geometry reconstruction, solver, provider or delivery verified.')
