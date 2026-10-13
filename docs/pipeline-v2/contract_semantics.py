"""Design-only cross-field checks for fictional v2 contract fixtures.

This module never opens assets, resolves a runtime handle, executes geometry, or
accepts a job. A future host still needs authority, byte/hash, geometric, sampling,
quality, lifecycle and receipt validators. Fixture resolution is explicitly supplied.
"""
from __future__ import annotations

import math
from collections.abc import Mapping


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError(code)


def dependency(ref: dict, fixtures: Mapping[str, dict] | None, kind: str) -> dict:
    """Resolve a declared fixture id only; this is not runtime authorization/hash QA."""
    require(fixtures is not None and ref['artifact_ref'] in fixtures, 'dependency_unresolved')
    result = fixtures[ref['artifact_ref']]
    require(result['kind'] == kind and result['bundle_id'] == ref['artifact_ref'], 'dependency_type_mismatch')
    return result


def asset_pin(asset: dict) -> dict:
    return {key: asset[key] for key in ('artifact_id', 'sha256', 'topology_sha256')}


def check_binding(binding: dict, source: dict, target: dict) -> None:
    require(binding['source'] == asset_pin(source) and binding['target'] == asset_pin(target),
            'geometry_binding_pin_mismatch')
    require(source['units'] == target['units'], 'geometry_binding_unit_mismatch')
    if binding['kind'] == 'identity':
        require(source['sha256'] == target['sha256'] and
                source['topology_sha256'] == target['topology_sha256'] and
                source['coordinates'] == target['coordinates'], 'geometry_identity_mismatch')
    if binding['kind'] == 'rigid_transform':
        matrix = binding['transform']
        require(matrix[3] == [0, 0, 0, 1], 'invalid_rigid_transform')
        rotation = [row[:3] for row in matrix[:3]]
        for i in range(3):
            for j in range(3):
                dot = sum(rotation[k][i] * rotation[k][j] for k in range(3))
                require(math.isclose(dot, float(i == j), abs_tol=1e-9), 'invalid_rigid_transform')
        a, b, c = rotation
        determinant = (a[0] * (b[1]*c[2]-b[2]*c[1]) -
                       a[1] * (b[0]*c[2]-b[2]*c[0]) + a[2] * (b[0]*c[1]-b[1]*c[0]))
        require(math.isclose(determinant, 1.0, abs_tol=1e-9), 'invalid_rigid_transform')
    report = binding['error_report']
    if report['status'] == 'passed' and report['tolerance'] is not None:
        require(report['value'] <= report['tolerance'], 'geometry_error_exceeds_tolerance')


def check_state(plan: dict) -> None:
    begin, end = plan['time_window_s']
    require(all(math.isfinite(x) for x in (begin, end)) and begin >= 0, 'invalid_state_window')
    if plan['clock'] == 'static':
        require(begin == end == 0, 'invalid_static_window')
    else:
        require(end > begin, 'invalid_state_window')
    names = [field['name'] for field in plan['fields']]
    require(len(names) == len(set(names)), 'duplicate_state_field')
    # Actual time arrays, entity populations, chunks and field precision do not
    # exist in these fixtures and must be checked by a future domain validator.


def validate_semantics(value: dict, fixtures: Mapping[str, dict] | None = None,
                       accepted_plan: dict | None = None) -> None:
    """Check cross-field design invariants after Draft 2020-12 validation."""
    schema = value.get('schema_version')
    if schema == 'agent-result/2' and value['status'] == 'plan_ready':
        plan = value['data']['plan']
        stages = set(plan['permitted_stages'])
        require(stages <= set(plan['lock']['modules']), 'missing_stage_pin')
        require(not (plan['purpose'] == 'model_preview' and plan['kind'] == 'simulation'),
                'preview_simulation_plan')
        effective = plan['effective_arguments']
        purpose = (effective['request']['purpose'] if plan['kind'] == 'pipeline' else
                   effective['spec']['purpose'] if plan['kind'] == 'modeling' else
                   effective.get('purpose', 'physical_simulation'))
        require(purpose == plan['purpose'], 'plan_purpose_mismatch')
        if plan['kind'] == 'pipeline':
            require(stages <= set(effective['request']['permitted_stages']), 'permitted_stages_expanded')
        if plan['state_plan'] is not None:
            check_state(plan['state_plan'])
        return
    if schema == 'stage-task/2':
        if accepted_plan is not None:
            require(value['plan_id'] == accepted_plan['plan_id'] and
                    value['stage'] in accepted_plan['permitted_stages'], 'task_plan_mismatch')
            require(value['pin'] == accepted_plan['lock']['modules'][value['stage']], 'task_pin_mismatch')
            require(value['dependencies'] == accepted_plan['lock']['dependencies'], 'task_dependency_mismatch')
        return
    if schema == 'data-package/1':
        model = dependency(value['model_ref'], fixtures, 'ModelBundle/1')
        require(model['metadata']['purpose'] == value['purpose'], 'package_purpose_mismatch')
        if value['purpose'] == 'physical_simulation':
            simulation = dependency(value['simulation_ref'], fixtures, 'SimulationBundle/1')
            require(simulation['metadata']['model_ref'] == value['model_ref'], 'model_dependency_mismatch')
        return
    if schema != 'bundle-manifest/1':
        return
    files = value['files']
    require(len({item['artifact_id'] for item in files}) == len(files), 'duplicate_artifact_id')
    require(len({item['path'] for item in files}) == len(files), 'duplicate_artifact_path')
    metadata = value['metadata']
    if accepted_plan is not None and metadata.get('engine_dependency') is not None:
        require(metadata['engine_dependency'] == accepted_plan['lock']['dependencies'].get('engine'),
                'engine_plan_mismatch')
    kind = value['kind']
    if kind == 'ModelBundle/1':
        source = metadata['raw_asset'] if metadata['purpose'] == 'model_preview' else metadata['simulation_asset']
        target = metadata['render_asset']
        for asset in (source, target):
            matches = [item for item in files if item['artifact_id'] == asset['artifact_id']]
            require(len(matches) == 1 and matches[0]['sha256'] == asset['sha256'], 'geometry_asset_file_mismatch')
            require(asset['units'] == metadata['units']['length'], 'geometry_asset_unit_mismatch')
            require(asset['coordinates'] == metadata['coordinates'], 'geometry_asset_coordinates_mismatch')
        check_binding(metadata['binding'], source, target)
    elif kind == 'SimulationBundle/1':
        require(metadata['model_ref'] in value['inputs'], 'model_input_missing')
        model = dependency(metadata['model_ref'], fixtures, 'ModelBundle/1')
        require(model['metadata']['purpose'] == 'physical_simulation', 'preview_not_simulatable')
        require(model['metadata']['domain'] == metadata['domain'], 'model_domain_mismatch')
        engine = model['metadata']['engine_dependency']
        if engine is not None:
            require(engine == metadata['engine_dependency'], 'engine_dependency_mismatch')
        require(metadata['state_export'] == metadata['state_plan']['profile'], 'state_profile_mismatch')
        require(metadata['physical_interval_s'] == metadata['state_plan']['time_window_s'], 'state_interval_mismatch')
        check_state(metadata['state_plan'])
    elif kind == 'RenderBundle/1':
        require(metadata['model_ref'] in value['inputs'], 'model_input_missing')
        model = dependency(metadata['model_ref'], fixtures, 'ModelBundle/1')
        require(model['metadata']['purpose'] == metadata['purpose'], 'render_purpose_mismatch')
        require(metadata['binding'] == model['metadata']['binding'], 'render_binding_mismatch')
        require(any(item['role'] == 'video' for item in files), 'render_video_missing')
        require(all(metadata[key] == metadata['presentation'][key]
                    for key in ('width_px', 'height_px', 'fps', 'playback_duration_s', 'fidelity')),
                'render_presentation_mismatch')
        if metadata['purpose'] == 'physical_simulation':
            require(metadata['simulation_ref'] in value['inputs'], 'simulation_input_missing')
            simulation = dependency(metadata['simulation_ref'], fixtures, 'SimulationBundle/1')
            require(simulation['metadata']['model_ref'] == metadata['model_ref'], 'model_dependency_mismatch')
            require(simulation['metadata']['engine_dependency'] == metadata['engine_dependency'],
                    'engine_dependency_mismatch')
            require(simulation['metadata']['physical_interval_s'] == metadata['physical_interval_s'],
                    'render_interval_mismatch')
        else:
            require(not any(ref['schema'] == 'SimulationBundle/1' for ref in value['inputs']),
                    'preview_simulation_input')
