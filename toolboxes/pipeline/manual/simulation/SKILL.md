---
name: physics-pipeline-simulation
description: Run and inspect a prepared QQ physics pipeline, retrieve solver-backed measurements, and deliver its verified video with data. Use after model preparation or when asking about a saved run.
---

# Run and retrieve solver data

模块内部实现、数据所有权、质量门和恢复合同见 [内部架构](architecture.md)。

The host pins the simulator and all three stage modules as one immutable component set. Calling `physics_simulate` invokes separate modeling, simulation and rendering processes. Internal modules use fixed arguments and `stage-call/1`; they are not agent-facing QQ endpoints.

Use `pipeline_status({})` to distinguish prepared, completed and failed work. For a prepared model, call `physics_simulate` once, passing the exact prepared `scene_json` or `spec_json`, or omit it to reuse the task's model. The host assigns all output paths and budgets. Do not issue parallel simulation or inspection calls while execution is running.

The simulation stage saves canonical solver results with video disabled, preserving the existing simulator's numerical and runtime checks. Rendering reads that saved result. A render failure or presentation-only change does not authorize changing the result, integrating again or shortening physical time.

For completed mechanical runs, use `physics_inspect({})` and `physics_query({"query_id":"the-declared-id"})`; paths are task-owned. For PCB use `pcb_inspect({})` and `pcb_query({"x_m":0.03,"y_m":0.03})`, with coordinates inside the prepared board. Report measured values, units, sampling resolution and material assumptions from these tools.

Read the two gates separately:

- A passed delivery gate permits the requested visual presentation. `data_usable` and `quality_gate.numerical_passed` determine whether numerical conclusions are supported. Visual success with precision warnings is possible; do not describe it as quantitative certification.
- Mechanical measurements come from predeclared solver macro-step observations. Display frames can be decimated and do not provide all particle states, arbitrary post-run measurements or restart checkpoints. An undeclared measurement needs a newly prepared run if the user requests it.
- PCB exports verified cell temperatures and power plus time-series extrema. Finite values and energy balance validate the numerical solve, not the calibration of a real board or a certified error bound.

When the host reports a successful presentation and data bundle, call `qq_video` through the normal host interface. The host queues the video and its `data.zip` together; use the returned delivery status before claiming they were delivered. The archive carries canonical JSON, provenance and available CSV. Check `data_usable` before presenting archived numbers as quantitative evidence.

For a video-only revision, call `pipeline_render_prepare({"rendering_quality":"standard"})` or use `"preview"`, then the usual `physics_simulate` execution entry. A verified simulation is reused. Do not rerun physics to improve colors, camera presentation or encoding. Deep diffusion rendering and high modeling quality are not enabled in this release; surface the explicit unsupported result.

After interruption, inspect `pipeline_status`. Completed stages may be reused only after the host verifies their saved hashes and frozen settings. Stage output is immutable: do not erase, modify or overwrite partial artifacts to force a retry. If the host reports an incomplete stage with no valid recovery record, report its failure and request a new prepared attempt only when required by the changed task or its recovery guidance. A repeated identical failure without new evidence is not progress.

Read `help({"topic":"modeling"})` for preparing new physical inputs and `help({"topic":"rendering"})` for presentation choices. Explicit numerical or conservation questions require strict simulation validation and declared queries before preparation.
