# Tool responses, delivery gates and recovery

Standalone connection diagnostics are described in [connections](connections.md), and mixed contacts/rotating bodies in [coupling](coupling.md); the shared result contract and gates below still apply.

Every agent-tool response includes `ok`, `protocol_version`, `status` and `next_action`. Action kinds are `call_tool`, `choose`, `respond_to_user`; these guide control flow but do not execute it. Failures include `errors` with `code`, `path`, `message`, and optional `suggestion`/`retryable`, plus `stage` where applicable. Public inputs are in [tools.json](../../tools.json).

## Response fields

| Tool | Main returned data |
|---|---|
| `physics_capabilities` | Capabilities/limits, canonical examples, workflow, native runtime, physics claims |
| `physics_system` | Named system specification assembled into `scene` and serialized `scene_json`; does not integrate, prepare the returned scene before running |
| `physics_mesh` | Audited `mesh_json`, `audit`, geometry provenance/model scope; `status=mesh_ready`; insert/patch the asset into a scene before prepare |
| `physics_example` | `scene`, serialized `scene_json`, stable `common_patches`, uses/limitations and available customer assumptions |
| `physics_patch` | Changed `scene`, matching `scene_json` and `validation`; operations are atomic; pass `scene_json` directly to prepare |
| `physics_validate` | `valid`, errors/warnings, normalized scene, deterministic effective assumptions |
| `physics_estimate` | `plan`, flat `agent_report`, warnings/assumptions; `ok` alone does not imply budget fit |
| `physics_prepare` | Same plan plus normalized scene/`scene_json`, `ready_to_simulate`; require both it and `ok` |
| `physics_simulate` | Compact run summary: ID/hash, plan/report, assumptions/claims, diagnostics, attempts, timing, quality gate, absolute artifact paths |
| `physics_inspect` | Reconstructed, revalidated compact saved-run summary |
| `physics_query` | Verified declared answers, sampling, run/scene identity, quality gate and artifact paths; one selected series also returns its samples |

Plan fields describe requested/selected backend, quality, original and uniform-spacing particle counts, simulated/display counts, spacing, iterations/substeps/workers, output frames, particle-frame samples, peak memory, adjustments and time estimates. Feasibility and timing definitions are maintained in [capability-boundaries](capability-boundaries.md).

For the coupled route, `plan.coupling_initial_cfl_substeps` reports the initial contact CFL requirement and `plan.coupling_event_headroom` reserves force-field event splits. The shared substep limit of 64 includes this event headroom. Preparation rejects an initial requirement above the cap; later acceleration can still trigger an explicit runtime CFL failure.

With queries, `plan.measurement_plan` includes query count, sample/scalar capacity, work units, sample interval, estimated memory and `fits_limits`; source is `solver_macro_steps`, precision `float64`, population complete. Successful runs persist `measurements.json`; summary `measurements` retains identity/contract/observations hashes, sampling and compact answers. Actual sampling includes `sample_count` and `window_s`. Exact answer fields/states and dwell/event semantics are in [quantitative-queries](quantitative-queries.md).

## Success and diagnostics

Deliver only with `status=completed`, `ok=true`, `quality_gate.passed=true`. `solver_ok` means finite, complete state at requested physical duration; it may be true while final `ok` is false. Successful simulate already returns an inspected summary; inspect is for recovery/rechecking.

All runs must complete the requested duration in a finite state, with diagnostics/counts consistent with scene and plan. Additional checks:

| Applicable model | Delivery gate |
|---|---|
| Fluid, native DFSPH or `native-c11-coupled` | Finite diagnostics in physical ranges; `close_water_particle_fraction <= 0.02`, `water_density_p99_ratio <= 1.15`, `density_iteration_limit_fraction <= 0.60`; coverage `native-full` |
| Python fluid | Basic completion/finite coverage `python-basic`; it does not inherit native checks |
| Closed point masses | `abs(nbody_relative_energy_drift) <= 0.02`; `nbody_momentum_drift <= nbody_momentum_tolerance`; matching `nbody_invariants_conserved=true` |
| Normal video run | Verified run-local MP4, as below |
| Queried run | Measurements bound to this scene/run/declarations/full observations and re-evaluated answers |
| Native mesh | Bound topology/vertices/prescribed motion and deformation/contact checks defined in [mesh-modeling](mesh-modeling.md) |
| Native coupled | Bound all p/g/r/q/m counts and metadata; finite completed shared-clock state, unit quaternion and current mixed contact/deformation gates; [scope](coupling.md) |

Fixed bodies or targeted external fields set N-body invariants inapplicable/unconserved, with null drifts and `fixed_body_breaks_closed_system_momentum` or `external_force_field` exclusion reason. A stability query additionally checks **peak** whole-window drift; terminal diagnostics do not replace it.

Read actual `diagnostics.backend` and any `native_fallback`. Liquid diagnostics:

| Field | Meaning |
|---|---|
| `steps`, `substeps`, `minimum_substep_s`, `cfl_limited_steps` | Macro steps, CFL/event segments, adaptation |
| `threads_used`, neighbour/iteration counts, `peak_*` density/divergence residuals | Cost and discrete convergence diagnostics |
| `water_density_p50_ratio`, `water_density_p95_ratio`, `water_density_p99_ratio` | Terminal discrete density quantiles, including explicit-plane support |
| `minimum_water_separation_ratio`, `water_separation_p01_ratio` | Minimum/p01 nearest same-water distance divided by spacing; p01 is less dominated by one pair |
| `close_water_particle_fraction` | Fraction with nearest water neighbour below `0.75 * spacing` |
| `density_iteration_limit_fraction`, `divergence_iteration_limit_fraction` | Limit-hit substeps divided by substeps; divergence has no current gate threshold |
| `planar_boundary_support_fraction` | Final water fraction supported by explicit planes; excludes other contacts |
| `represented_water_volume_m3` | Nominal `water particle count * spacing³`; not reconstructed occupancy/conservation |

`particle_count` is simulated population, `render_particle_count` the display subset. These gates diagnose implementation stability, not pressure/loads, visual attractiveness or experimental accuracy. High iteration-hit fractions merit controlled comparisons; compare matching scene, spacing and backend.

## Artifact and runtime identity

Normal run artifacts are normalized scene, plan, result, summary, completion, optional measurements and `simulation.mp4`. New results carry `result_version:1`. Inspection also accepts pre-v1 results with their original planning and fidelity contract. If such a result contains fluid, its otherwise-complete normalized scene must predate the `preset` field; inspection restores only the implicit `water` interpretation and verifies the original scene hash and legacy disclosures. Removing `result_version` from a v1 liquid result does not downgrade it. Keep attempts and assumptions. The required video path is absolute, resolves inside that run, is a regular non-symlink file named `simulation.mp4`, matches recorded byte count, exceeds 1,000 bytes and has `ftyp` in its first 32 bytes. The probe validates track/sample tables, every compressed sample and every decoded frame, codec/dimensions/duration, and `sample_count == len(trajectory.frames)`.

H.264 uses AVAssetReader pixel decoding; if sandbox policy blocks that path, Motion JPEG qualifies only through ImageIO validation of every frame. Any video failure records `verified_video_artifact` in `failed_checks`; a plausible file path or first frame is insufficient.

Video `duration_s` is encoded playback duration, `physical_duration_s` is the simulated interval, and `time_scale_to_physical` is their ratio. `output_frames` includes initial and terminal samples; output FPS does not set physical integration precision.

`result.json.total_runtime_s` stops after the final gate but before the large result write. Result and summary are then persisted, followed by bounded run-local `completion.json` with matching run ID/scene hash, `measurement_boundary=through_summary_persistence` and `wall_runtime_s`. Inspect validates this non-symlink record and uses its runtime. Direct simulate measures through the subsequent completion write and labels `through_completion_persistence`. These are measured cooperative-deadline times, not OS preemption guarantees.

`quality_gate.runtime_checks.wall_time_within_budget` compares the measured runtime with the scene budget in both simulate and inspect. An observed overrun returns `wall_time_budget_exceeded`, `ok=false` and a failed gate, including overruns discovered during final checks or persistence. A late failure is saved in summary/completion; the completion record only claims the time measured before its own final write, while the live response includes that write.

## Error recovery

Apply an unambiguous structured correction while each correction makes a concrete model change, and re-prepare after any scene change. Do not retry unsupported requests unchanged, fabricate success, or remove unrelated output files. Preserve errors/plan if failure occurred before result creation.

| Signal | Action |
|---|---|
| `unknown_field`, `finite_number`, `vec3`, `fps_integer` | Fix the named path/type/range using schema; FPS must be integer 1–60, independent of dt |
| Field target/axis/time errors, `combined_force_acceleration` | Use supported existing IDs, nonzero axis, in-duration windows, and bounded summed acceleration |
| `budget_infeasible`, false `fits_budget` | Reduce output cost or complexity; change explicit precision/duration only within authorization |
| `wall_time_budget_exceeded` | The completed pipeline exceeded its budget; do not deliver it as successful. Prepare a less expensive authorized scene or a budget up to 60 s |
| `infeasible_bounds`, `initial_state_outside_bounds` | Enlarge named bounds span or fix sampled entity support, then re-prepare |
| `initial_particle_overlap_limit`, `solver_rejected_scene` | Separate/merge sources or reduce complexity according to message; no neighbour-dropping bypass |
| `unstable_nbody_timestep` | Reduce dt until initial step ratio is at most 0.1 |
| `unsupported_dynamic_box`, `unsupported_native_dynamic_rigid` | These refer to legacy `rigid`; use explicitly authored `rigid_body` when rotation/mixed contact is intended, or the old translating sphere route when requested |
| `slider_model_boundary`, `slider_parameter` | Follow standalone common-rail and initial-state restrictions; do not relabel general boxes |
| `invalid_mesh`, `mesh_*`, `unsupported_mesh_coupling` | Follow [mesh-modeling](mesh-modeling.md); mixed scenes require explicit [coupling](coupling.md); preserve topology/provenance |
| `coupling_*`, `rigid_body_parameter`, `connection_attachment`, `solid_connection_*` | Repair the declared mixed contract and re-prepare; do not erase torque, mass, thickness or reaction to force a legacy route |
| `query_target`, `query_metric_target`, required/plane/axis/origin/hold/value/ID errors | Correct according to [query reference](quantitative-queries.md), not from video guesses |
| `stability_requires_closed_nbody` | Keep actual fixed/external-force model; select another metric instead of deleting its physics |
| `measurement_budget_exceeded` | Reduce queries or an allowed horizon; change dt only within accuracy requirements; FPS is irrelevant |
| `queries_not_recorded`, `query_not_declared` | Choose `available_query_ids` or declare/prepare/run a new scene |
| `invalid_measurement_artifact` | Regenerate verified data; no video or other-run substitution |
| `unsafe_output_directory`, `output_write_failed` | Use a new writable dedicated directory |
| `native_backend_unavailable`, environment stage | Restore C11 environment or explicitly re-plan auto/Python; forced native is non-retryable unchanged |
| `fallback_budget_exhausted`, estimate stage | Auto's Python p90 exceeds remaining budget; simplify and re-prepare |
| `native_fallback` diagnostic | Report Python and reassess fidelity/time; it is not native DFSPH |
| timed out, incomplete, nonfinite | No successful delivery; retain evidence and revise plan/numerics |
| `video_encoding_failed`, `verified_video_artifact` | Fix renderer/environment/path and regenerate actual MP4; no-video is not delivery |
| `result_not_found`, `result_path`, `result_too_large` | Inspect the correct bounded run directory or result.json, not summary.json |
| `quality_gate_failed` | Read `failed_checks`, correct cause and rerun from initial state; do not deliver failed artifacts |

## Visual and strict acceptance

Set `budget.validation` to `visual` for ordinary videos or `strict` for requested numerical analysis.
The standalone toolbox defaults to visual; omitted fields in the raw engine API retain strict behavior for saved-run compatibility.
`quality_gate.passed` describes delivery; `numerical_passed` describes the original numerical checks.
Visual mode moves only listed precision checks (energy drift, fine constraint residuals, volume drift, water density and iteration budget) into `precision_warnings`.
The diagnostic values and original check booleans remain unchanged. Query answers are unavailable when numerical checks fail.
Complete duration, finite states, valid geometry/contact, consistent diagnostics, media integrity and the execution deadline remain mandatory.
Do not retry a passed visual video solely to remove precision warnings; disclose the approximation when explaining results.

Native point-mass gravity now subcycles velocity Verlet using the softened pair free-fall and crossing times.
It preserves initial conditions, physical duration, output frames and the outer observation clock.
The existing substep counters describe that outer clock; internal gravity integration is finer and checks the same wall-clock deadline.
The Python reference uses the same subcycle rule, also with deadline checks. Adaptive stepping improves close encounters but is not a proof of long-term conservation or stability.
