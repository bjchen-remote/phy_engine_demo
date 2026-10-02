#!/usr/bin/env python3
"""Validate planning contracts only; no solver, network, registry or delivery calls.

Requires jsonschema. Examples are fictional envelope fixtures, not real artifacts.
"""
import copy,json,re
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
root=Path(__file__).resolve().parent
schema=json.loads((root/'contracts.schema.json').read_text())
Draft202012Validator.check_schema(schema)
v=Draft202012Validator(schema,format_checker=FormatChecker())
paths=sorted((root/'examples').glob('*.json'))+sorted((root/'toolboxes').glob('*.json'))
for p in paths:
    errors=sorted(v.iter_errors(json.loads(p.read_text())),key=lambda e:str(e.path))
    assert not errors, (p,errors[0].message)
print(f'{len(paths)} positive JSON fixtures/contracts passed Draft 2020-12.')
api=json.loads((root/'api-tools.json').read_text())
assert len(api['tools'])==19 and len({t['name'] for t in api['tools']})==19
for t in api['tools']:
    for k in ['input_schema','output_schema']:
        name=t[k]['$ref'].split('#/$defs/')[1]
        assert name in schema['$defs']
for p in (root/'toolboxes').glob('*.json'):
    c=json.loads(p.read_text())
    assert set(c['operations'])=={t['name'] for t in api['tools'] if t['owner']==c['role']}
    assert (p.parent/c['skill_source']).resolve().is_file()
print('19 API signatures and three toolbox operation sets resolve.')
for p in (root/'examples').glob('*response.json'):
    a=json.loads(p.read_text())['next_action']
    if a['kind'] in ['call_tool','poll']:
        name='args_'+a['tool']; av=Draft202012Validator({'$schema':schema['$schema'],'$ref':'#/$defs/'+name,'$defs':schema['$defs']})
        av.validate(a['arguments'])
print('Executable next_action examples match their API input schemas.')
base=json.loads((root/'examples/pipeline-request.json').read_text())
neg=[]
a=copy.deepcopy(base); a['quality']['simulation']='deep'; neg.append(('mixed quality',a))
a=copy.deepcopy(base); a['model']['shell']='rm'; neg.append(('unknown model key',a))
a=copy.deepcopy(base); a['render']['fps']=0; neg.append(('invalid FPS',a))
a=copy.deepcopy(base); a['permitted_stages']=[]; neg.append(('empty stages',a))
a=copy.deepcopy(base); a['simulation']['state_export']='pretend_full'; neg.append(('unknown profile',a))
a=json.loads((root/'examples/simulation-bundle.json').read_text()); a['files'][0]['path']='../outside'; neg.append(('path escape',a))
a=json.loads((root/'examples/simulation-bundle.json').read_text()); a['files'][0]['sha256']='fake'; neg.append(('invalid digest',a))
a=json.loads((root/'examples/insufficient-cache-response.json').read_text()); a['errors']=[]; neg.append(('failure without error',a))
a=json.loads((root/'examples/plan-response.json').read_text()); a['next_action']={'kind':'poll','tool':'rendering_render','arguments':{},'poll_after_ms':1}; neg.append(('bad poll action',a))
a=json.loads((root/'examples/insufficient-cache-response.json').read_text()); a['next_action']['tool']='simulation_run'; neg.append(('respond with executable tool',a))
for name,a in neg: assert not v.is_valid(a),name
print(f'{len(neg)} invalid mutations rejected (contract checks, not runtime tests).')
missing=[]
for p in root.rglob('*.md'):
    for target in re.findall(r'\]\(([^)]+)\)',p.read_text()):
        if '://' in target or target.startswith('#'): continue
        target=target.split('#')[0]
        if target and not (p.parent/target).exists(): missing.append((str(p.relative_to(root)),target))
print('Unresolved markdown links:',missing)
assert not missing

# The standalone modeling plan must not require a solver, renderer or fabricated state.
plan_result=json.loads((root/'examples/plan-response.json').read_text())
model_only=copy.deepcopy(plan_result)
p=model_only['data']['plan']; p['kind']='modeling'
p['lock']['modules']={'modeling':p['lock']['modules']['modeling']}
p['quality_requested']={'modeling':'standard'}; p['quality_resolved']={'modeling':'standard'}
p['state_plan']=None; p['permitted_stages']=['modeling']
p['effective_arguments']={'spec':base['model'],'quality':'standard'}
p['delivery']={'required_roles':['model'],'include_model':True,'data_formats':['json'],'allow_partial':False}
model_only['stage']='modeling'; model_only['next_action']['tool']='modeling_build'
v.validate(model_only)
print('Standalone modeling plan validates without solver/renderer pins or state export.')
extra=[]
a=copy.deepcopy(model_only); a['data']['plan']['state_plan']=plan_result['data']['plan']['state_plan']; extra.append(('fabricated modeling state',a))
a=json.loads((root/'examples/steady-request.json').read_text()); a['simulation']['physical_duration_s']=10; extra.append(('fictional steady duration',a))
a=json.loads((root/'examples/simulation-bundle.json').read_text()); a['quality']['actual']['rendering']='deep'; extra.append(('premature rendering quality',a))
for name,a in extra: assert not v.is_valid(a),name
qv=Draft202012Validator({'$schema':schema['$schema'],'$ref':'#/$defs/args_simulation_query','$defs':schema['$defs']})
wrong={'simulation_ref':{'artifact_ref':'example-model','schema':'ModelBundle/1','sha256':'a'*64},'query_id':'period'}
assert not qv.is_valid(wrong)
print('4 additional independent-stage, steady-state, quality, and reference-type rejection checks passed.')

def refs(node):
    if isinstance(node,dict):
        if '$ref' in node and node['$ref'].startswith('#/$defs/'):
            assert node['$ref'].split('/')[-1] in schema['$defs'],node['$ref']
        for val in node.values(): refs(val)
    elif isinstance(node,list):
        for val in node: refs(val)
refs(schema)
print('All internal schema references resolve.')
