"""Physics implementation of toolbox v1; knows nothing about QQ or its sender."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

import run_simulation as engine


DATA_REQUEST = re.compile(
    r"周期|频率|振幅|最高|最低|最大|最小|峰值|极值|温度|功率|能量|速度|"
    r"距离|半径|轨迹|误差|数据|数值|多少|多久|什么时候|何时"
)


def _data_requested(text: str) -> bool:
    return DATA_REQUEST.search(text) is not None


def _physics_data_reply(summary: dict, request: str) -> tuple[str, str, list[dict]]:
    """Make a bounded public answer only from quality-gated saved measurements."""
    if not _data_requested(request):
        return 'not_requested', '', []
    gate = summary.get('quality_gate', {})
    measurements = summary.get('measurements') or {}
    if gate.get('numerical_passed') is not True or measurements.get('usable') is not True:
        return 'unavailable', '本次数据未通过数值质量检查，不能提供可靠实测值。', []
    answers = measurements.get('answers') or []
    if not answers:
        return 'unavailable', '本次运行没有预先声明该项数据查询，不能从视频估算数值。', []
    period_requested = any(word in request for word in ('周期', '频率'))
    if period_requested:
        candidates = [a for a in answers if a.get('type') == 'period']
    elif any(word in request for word in ('多久', '什么时候', '何时')):
        candidates = [a for a in answers if a.get('type') == 'threshold']
    else:
        requested_metrics = (
            (('速度', '速率'), ('speed', 'angular_speed')),
            (('能量',), ('spring_energy', 'rotational_energy')),
            (('半径', '扩散'), ('spread_radius',)),
            (('距离', '间距', '间隙'), ('center_distance', 'surface_gap')),
            (('伸长',), ('connection_extension',)),
            (('弹簧力', '受力'), ('spring_force',)),
            (('位置', '轨迹', '位移'), ('centroid', 'max_displacement')),
        )
        metric_types = next((types for words, types in requested_metrics
                             if any(word in request for word in words)), ())
        candidates = [a for a in answers if a.get('type') in ('threshold', 'series')
                      and a.get('definition', {}).get('metric', {}).get('type') in metric_types]
        if not candidates and ('稳定' in request or '三体' in request):
            candidates = [a for a in answers if a.get('type') == 'nbody_stability']
        if not candidates and len(answers) == 1:
            candidates = answers
    if not candidates:
        return 'unavailable', '本次运行没有预先声明所问物理量的数据查询，不能从视频估算数值。', []
    answer = candidates[0]
    status = answer.get('status')
    public = [{key: answer[key] for key in (
        'id', 'type', 'status', 'unit', 'window_s', 'sampling_interval_s',
        'period_s', 'frequency_hz', 'complete_cycles', 'required_cycles',
        'time_s', 'time_bracket_s', 'minimum', 'maximum',
    ) if key in answer}]
    if answer['type'] == 'period':
        if status != 'measured':
            reason = {
                'insufficient_cycles': '观察窗口内同向返回的完整间隔不足',
                'sampling_too_coarse': '求解步采样过粗',
                'irregular_returns': '返回间隔变化较大，不能定义单一稳定周期',
            }.get(status, '周期结果尚不能确认')
            return 'unavailable', f'本次未测得可靠周期：{reason}（完整间隔 {answer.get("complete_cycles", 0)} 个）。', public
        metric = answer.get('definition', {}).get('metric', {})
        side = '正侧向负侧' if answer.get('direction') == 'falling' else '负侧向正侧'
        phrase = (f'求解器实测周期 {answer["period_s"]:.6f} s、频率 {answer["frequency_hz"]:.6f} Hz；'
                  f'定义：{metric.get("entity", "目标")} 的 {metric.get("axis", "标量")} 值'
                  f'连续两次从{side}越过参考值 {answer["reference_value"]:g}，'
                  f'共 {answer["complete_cycles"]} 个完整间隔，求解步 {answer["sampling_interval_s"]:g} s。')
        reference = answer.get('ideal_single_pendulum_reference')
        if isinstance(reference, dict):
            if reference.get('release_side') in ('positive_x', 'negative_x'):
                release_side = '正侧' if reference['release_side'] == 'positive_x' else '负侧'
                phrase += f'对这个理想单摆，一个周期也对应从{release_side}静止释放后首次回到{release_side}转折点。'
            phrase += (f'小角度公式 {reference["small_angle_period_s"]:.6f} s；'
                       f'初角 {math.degrees(reference["release_angle_rad"]):.3g}° 的大振幅解析参考 '
                       f'{reference["finite_amplitude_period_s"]:.6f} s。')
            public[0]['analytic_reference'] = {key: reference[key] for key in (
                'small_angle_period_s', 'finite_amplitude_period_s', 'release_angle_rad',
                'release_side') if key in reference}
        return 'measured', phrase, public
    if answer['type'] == 'threshold':
        if status in ('reached', 'initially_satisfied'):
            bracket = answer.get('time_bracket_s')
            return 'measured', (f'求解器观测到阈值事件：{answer["time_s"]:.6g} s，'
                                f'采样括区 {bracket} s；采样步 {answer["sampling_interval_s"]:g} s。'), public
        return 'unavailable', f'观察窗口 {answer.get("window_s")} s 内未观测到所问阈值事件。', public
    if answer['type'] == 'nbody_stability':
        if status not in ('criteria_satisfied', 'criteria_violated'):
            return 'unavailable', '三体数据的数值完整性不足，不能作有限窗口判定。', public
        return 'measured', (f'有限窗口判据：{status}；最大质心半径 '
                            f'{answer["max_observed_com_radius_m"]:.6g} m，最小两体距离 '
                            f'{answer["min_observed_pair_distance_m"]:.6g} m。'), public
    if status == 'measured':
        field = 'minimum' if any(word in request for word in ('最低', '最小')) else 'maximum'
        extremum = answer[field]
        label = '最小值' if field == 'minimum' else '最大值'
        return 'measured', (f'求解器观测 {answer["id"]} 的{label}：'
                            f'{extremum["value"]:.6g} {answer["unit"]}，'
                            f'发生于 {extremum["time_s"]:.6g} s；'
                            f'采样步 {answer["sampling_interval_s"]:g} s。'), public
    return 'unavailable', '该数据查询尚未得到可确认的数值结果。', public


def _pcb_data_reply(result: dict, request: str) -> tuple[str, str, list[dict]]:
    if not _data_requested(request):
        return 'not_requested', '', []
    if '结温' in request or '走线' in request:
        return 'unavailable', '当前板级热模型没有封装热阻或走线局部几何，不能计算器件结温或走线热点。', []
    values = {key: result[key] for key in (
        'min_temperature_c', 'mean_temperature_c', 'max_temperature_c',
        'total_power_w', 'duration_s', 'mode')}
    values['source'] = 'verified_pcb_solver_grid'
    if any(word in request for word in ('什么时候', '何时', '多久', '首次')):
        match = re.search(r'(?:超过|达到|高于)\s*(-?\d+(?:\.\d+)?)\s*(?:°\s*C|℃|度)', request)
        if result['mode'] != 'transient' or not match:
            return 'unavailable', '时间事件需要瞬态模型和明确的温度阈值，例如“最高板温首次超过 80 °C”。', [values]
        threshold = float(match.group(1))
        series = result['time_series']
        first = next((index for index, sample in enumerate(series)
                      if sample['max_temperature_c'] > threshold), None)
        if first is None:
            return 'unavailable', (f'在 0–{result["duration_s"]:g} s 的热求解步内，'
                                    f'板面最高温未超过 {threshold:g} °C。'), [values]
        if first == 0:
            event_s = 0.0
            bracket = [0.0, 0.0]
        else:
            before, after = series[first - 1], series[first]
            t0, t1 = before['time_s'], after['time_s']
            y0, y1 = before['max_temperature_c'], after['max_temperature_c']
            event_s = t0 + (threshold - y0) * (t1 - t0) / (y1 - y0)
            bracket = [t0, t1]
        values.update({'threshold_c': threshold, 'crossing_estimate_s': event_s,
                       'sample_bracket_s': bracket})
        return 'measured', (f'板面网格最高温首次超过 {threshold:g} °C：'
                            f'约 {event_s:.6g} s，热求解步括区 {bracket} s。'), [values]
    return 'measured', (f'板厚平均网格末态温度：最低 {result["min_temperature_c"]:.6g} °C，'
                        f'平均 {result["mean_temperature_c"]:.6g} °C，'
                        f'最高 {result["max_temperature_c"]:.6g} °C；'
                        f'配置的总耗散功率 {result["total_power_w"]:.6g} W。'), [values]


def write(path: Path, **value) -> None:
    engine._atomic_write_json(path, {"schema_version": 1, **value})


def _run_pcb(spec: dict, job: Path, task: dict, started: float) -> None:
    """Execute the isolated PCB domain without entering the mechanical engine."""
    from pcb_thermal import simulate_pcb_thermal
    from pcb_video import PcbVideoError, render_pcb_video

    artifacts = job / 'artifacts'
    work = job / 'work/pcb'
    work.mkdir(parents=True, exist_ok=True)
    write(job / 'progress.json', state='running', fraction=0,
          updated_at=time.time())
    result = simulate_pcb_thermal(spec)
    input_power = sum(component['power_w'] for component in spec['components'])
    deposited_power = math.fsum(math.fsum(row) for row in result['power_w'])
    duration = result['duration_s']
    balance = result['energy_balance']['residual']
    balance_scale = input_power * (duration if result['mode'] == 'transient' else 1)
    checks = {
        'component_power_conserved':math.isfinite(deposited_power) and
            abs(deposited_power - input_power) <= max(1e-9, 1e-8 * abs(input_power)),
        'energy_balance':math.isfinite(balance) and
            abs(balance) <= max(1e-7, 1e-6 * abs(balance_scale)),
        'finite_temperature':all(math.isfinite(value) and value > -273.15
            for row in result['temperature_c'] for value in row),
    }
    if not all(checks.values()):
        write(artifacts / 'result-manifest.json', status='failed', failure={
            'code':'numerical_validation_failed', 'stage':'pcb_thermal',
            'retryable':False, 'failed_checks':[key for key, passed in checks.items() if not passed]})
        raise RuntimeError('PCB thermal verification failed')
    data_status, data_summary, data_answers = _pcb_data_reply(result, task['request']['text'])
    engine._atomic_write_json(work/'result.json', result)
    budget = task['limits']['wall_time_seconds']
    remaining = None if budget is None else budget - (time.monotonic() - started)
    try:
        video = artifacts / 'simulation.mp4'
        media = render_pcb_video(result, spec, video,
                                 max_bytes=task['limits']['max_output_bytes'],
                                 timeout_seconds=remaining)
    except PcbVideoError as error:
        write(artifacts / 'result-manifest.json', status='failed', failure={
            'code':'video_delivery_failed', 'stage':'presentation',
            'retryable':False, 'message':str(error)[:300]})
        return
    digest = hashlib.sha256(video.read_bytes()).hexdigest()
    write(artifacts/'result-manifest.json', status='succeeded',
          outputs=[{'path':video.name, 'media_type':'video/mp4', 'sha256':digest}],
          verification={'passed':True,
                        'numerical_passed':True,
                        'checks':[key for key, passed in checks.items() if passed] + ['video_decode'],
                        'domain':'pcb_thermal', 'energy_balance':result['energy_balance'],
                        'solver':result['solver'], 'media':media},
          data_status=data_status, data_summary=data_summary, data_answers=data_answers,
          summary=(f"PCB 热仿真完成：总功耗 {result['total_power_w']:.3g} W，"
                   f"最高温度 {result['max_temperature_c']:.3g} °C。"))
    write(job/'progress.json', state='completed', fraction=1,
          updated_at=time.time())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("probe", "run", "api"), required=True)
    parser.add_argument("--task", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    task = json.loads(args.task.read_text())
    if task.get("schema_version") != 1:
        raise ValueError("unsupported task schema")
    job = args.task.resolve().parent
    root = Path(__file__).resolve().parent
    os.environ["PHYSICS_DEMO_NATIVE_LIBRARY"] = str(root / "prebuilt/libphysics_native.dylib")
    os.environ["PHYSICS_DEMO_PREBUILT_VIDEO_DIR"] = str(root / "prebuilt")
    if args.phase == 'api':
        from modeling import api_call
        api_call(root, job, task)
        return
    prepared = job/'work/prepared-scene.json'
    prepared_model = json.loads(prepared.read_text()) if prepared.exists() else None
    scene = prepared_model['scene'] if prepared_model else None
    domain = prepared_model.get('domain', 'physics') if prepared_model else 'physics'
    if scene is None and task['request'].get('plan_required'):
        if args.phase == 'probe':
            write(job/'capability.json', supported=False, estimated_seconds=None, reason='model_not_prepared')
            return
        raise ValueError('model must pass physics_prepare before execution')
    text = task["request"]["text"]
    seed = int.from_bytes(hashlib.sha256(str(task.get('task_id', '')).encode()).digest()[:8], 'big')
    try:
        if scene is not None and domain == 'pcb_thermal':
            from pcb_thermal import validate_pcb_spec
            scene = validate_pcb_spec(scene)
            name = 'pcb_thermal'
            estimate = prepared_model['estimated_seconds']
        elif scene is not None:
            from physics_demo.runner import prepare
            budget = task['limits']['wall_time_seconds']
            checked = prepare(scene, budget, unlimited=budget is None)
            if not checked.get('ready_to_simulate'):
                raise engine.UnsupportedRequest('prepared scene failed revalidation')
            name = 'agent_authored'
        else:
            name, _ = engine.route(text, seed=seed)
        if domain != 'pcb_thermal':
            estimate = (checked["agent_report"]["estimated_wall_time_s"]["p90"]
                        if scene is not None else (25 if name.startswith("water_") else 15))
        budget = task["limits"]["wall_time_seconds"]
        supported = budget is None or estimate <= budget
        reason = "" if supported else "insufficient_time_budget"
    except engine.UnsupportedRequest:
        supported, estimate, reason = False, None, "unsupported_scene"
    if args.phase == "probe":
        write(job / "capability.json", supported=supported, estimated_seconds=estimate,
              reason=reason)
        return
    artifacts = job / "artifacts"
    if not supported:
        write(artifacts / "result-manifest.json", status="unsupported", reason=reason)
        return
    if domain == 'pcb_thermal':
        _run_pcb(scene, job, task, started)
        return
    root = Path(__file__).resolve().parent
    os.environ["PHYSICS_DEMO_NATIVE_LIBRARY"] = str(root / "prebuilt/libphysics_native.dylib")
    os.environ["PHYSICS_DEMO_PREBUILT_VIDEO_DIR"] = str(root / "prebuilt")
    write(job / "progress.json", state="running", fraction=0, updated_at=time.time())
    # Private legacy adapter input is inside work; the bridge never sees this schema.
    legacy = job / "work" / "physics"
    legacy.mkdir(exist_ok=True)
    (legacy / "artifacts").mkdir(exist_ok=True)
    write(legacy / "request.json", message={"text": text}, seed=seed)
    result = (engine.simulate_scene(scene, legacy/'artifacts',
                                   unlimited=budget is None) if scene is not None else
              engine.main(["run_simulation.py", str(legacy / "request.json"), str(legacy / "artifacts")]))
    if result:
        summary_path = legacy/'artifacts/summary.json'
        summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
        checks = summary.get('quality_gate', {}).get('failed_checks', [])
        nbody_error = 'nbody_relative_energy_drift_at_most_0_02' in checks
        write(artifacts/'result-manifest.json', status='failed', failure={
            'code':'numerical_validation_failed' if checks else 'execution_failed',
            'message':'三体能量误差超过 2% 的校验阈值。' if nbody_error else '结果未通过物理或视频校验。',
            'failed_checks':checks, 'retryable':bool(checks),
            'suggestion':('Preserve requested masses, initial positions, velocities, duration and softening. '
                          'Reduce world.dt by 100 times, call physics_prepare again, then rerun from the initial state. '
                          'Do not repeat the unchanged failed scene or claim the system is physically unstable.'
                          if nbody_error else 'Inspect the structured checks, correct the model and prepare again before retrying.')})
        raise RuntimeError("physics verification failed")
    from delivery import publish_video, VideoEncodingError
    video = artifacts / "simulation.mp4"
    summary = json.loads((legacy / "artifacts/summary.json").read_text())
    try:
        delivery = publish_video(legacy/'artifacts', video, summary,
            task['limits']['max_output_bytes'],
            None if budget is None else budget - (time.monotonic() - started))
    except VideoEncodingError:
        write(artifacts/'result-manifest.json', status='failed', failure={
            'code':'video_delivery_budget', 'stage':'presentation', 'retryable':False,
            'message':'物理模拟已完成，但视频未能在发送大小和剩余时间限制内完成压缩。',
            'suggestion':'Report a video delivery limit with validation_failed; do not rerun physics or shorten the requested duration.'})
        return
    with video.open("rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    data_status, data_summary, data_answers = _physics_data_reply(summary, text)
    write(artifacts / "result-manifest.json", status="succeeded",
          outputs=[{"path": video.name, "media_type": "video/mp4", "sha256": digest}],
          verification={"passed": True, "checks": ["physics_quality_gate", "video_decode"],
                        "validation_mode":summary['quality_gate']['validation_mode'],
                        "numerical_passed":summary['quality_gate']['numerical_passed'],
                        "precision_warnings":summary['quality_gate']['precision_warnings'],
                        "delivery":delivery},
          data_status=data_status, data_summary=data_summary, data_answers=data_answers,
          summary=f"模拟完成：{name}，物理时长 {summary.get('simulated_time_s')} 秒。")
    write(job / "progress.json", state="completed", fraction=1, updated_at=time.time())


if __name__ == "__main__":
    main()
