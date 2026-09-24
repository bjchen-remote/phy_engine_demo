"""Physics implementation of toolbox v1; knows nothing about QQ or its sender."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import time

import run_simulation as engine


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
    engine._atomic_write_json(work/'result.json', result)
    remaining = task['limits']['wall_time_seconds'] - (time.monotonic() - started)
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
                        'checks':[key for key, passed in checks.items() if passed] + ['video_decode'],
                        'domain':'pcb_thermal', 'energy_balance':result['energy_balance'],
                        'solver':result['solver'], 'media':media},
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
            checked = prepare(scene, task['limits']['wall_time_seconds'])
            if not checked.get('ready_to_simulate'):
                raise engine.UnsupportedRequest('prepared scene failed revalidation')
            name = 'agent_authored'
        else:
            name, _ = engine.route(text, seed=seed)
        if domain != 'pcb_thermal':
            estimate = (checked["agent_report"]["estimated_wall_time_s"]["p90"]
                        if scene is not None else (25 if name.startswith("water_") else 15))
        supported = estimate <= task["limits"]["wall_time_seconds"]
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
    result = (engine.simulate_scene(scene, legacy/'artifacts') if scene is not None else
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
            task['limits']['wall_time_seconds'] - (time.monotonic() - started))
    except VideoEncodingError:
        write(artifacts/'result-manifest.json', status='failed', failure={
            'code':'video_delivery_budget', 'stage':'presentation', 'retryable':False,
            'message':'物理模拟已完成，但视频未能在发送大小和剩余时间限制内完成压缩。',
            'suggestion':'Report a video delivery limit with validation_failed; do not rerun physics or shorten the requested duration.'})
        return
    with video.open("rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    write(artifacts / "result-manifest.json", status="succeeded",
          outputs=[{"path": video.name, "media_type": "video/mp4", "sha256": digest}],
          verification={"passed": True, "checks": ["physics_quality_gate", "video_decode"],
                        "validation_mode":summary['quality_gate']['validation_mode'],
                        "numerical_passed":summary['quality_gate']['numerical_passed'],
                        "precision_warnings":summary['quality_gate']['precision_warnings'],
                        "delivery":delivery},
          summary=f"模拟完成：{name}，物理时长 {summary.get('simulated_time_s')} 秒。")
    write(job / "progress.json", state="completed", fraction=1, updated_at=time.time())


if __name__ == "__main__":
    main()
