# Toolbox protocol v1

The transport and the numerical implementation are independent. The host owns admission, queueing,
trusted attachment handles, resource enforcement and delivery. A composed toolbox owns model
preparation, simulation, rendering and domain validation. Current v1 supports one MP4 in `outputs`
plus a required ZIP in `attachments` for the pipeline, and a separate typed model-preview route.
Admitted images, document-derived images and public reference images can be selected through
current-event host IDs; arbitrary URLs or local paths are not agent inputs to modeling.

`docs/pipeline-v2` is a proposed asynchronous multi-artifact interface, not the deployed protocol.
The current `model-preview-result/1`, `/2`, `/3` and corresponding data schemas are versioned
preview contracts inside v1; their numbers do not indicate adoption of pipeline-v2.

## Package manifest

Place `toolbox.json` at the package root:

```json
{"schema_version":1,"id":"physics","version":"1.0.0","entrypoint":"toolbox_adapter.py",
 "capabilities":["List tasks actually supported"],"limitations":["State precision and scale limits"]}
```

Bundle the relative Python entrypoint and its dependencies. Symlinks and special files are rejected.
The host must trust the package before publishing it; request text is always untrusted data.

For the composed pipeline, `build_pipeline.py` generates package metadata and `bundle-lock.json`
from source and selected immutable components. Runtime Skills are sourced from
`pipeline/manual/<role>/` and copied into that stage module's `manual/`. Modeling and simulation
packages contain only their own stage implementation plus shared helpers; modeling additionally
contains `modeling_flow`, and rendering contains `pipeline_presentation`. The fixed stage worker
is copied as each module's entrypoint. Manuals participate in component content digests. Edit
sources and build a new combination rather than editing generated packages or pinned snapshots.

Stage modules are stored as `package_kind=stage-module` components. They are not independently
activatable QQ task toolboxes; the host activates a verified composition.

## Input

Invoke the entrypoint with a fixed argument vector, never a shell command assembled from the prompt:

```text
python PACKAGE/ENTRYPOINT --phase api|probe|run --task JOB/task.json
```

The host creates JOB/artifacts and JOB/work and writes:

```json
{"schema_version":1,"task_id":"host-generated-id",
 "request":{"text":"User request","trust":"untrusted_external_input"},
 "limits":{"wall_time_seconds":30,"max_output_bytes":16777216,"network":false},
 "output_dir":"artifacts","work_dir":"work"}
```

For host-authorized runs without a wall-clock deadline, `wall_time_seconds` is
`null`. The physics adapter carries that mode through preparation, simulation,
rendering, and validation. Resource and output-byte limits still apply.

Paths are relative to the task JSON's directory. Do not put account credentials or unrelated user
data in the job. The host should permit reads of the package and job, writes only to the job, and no
network. The standalone smoke executor supplies these restrictions on macOS.

The example is the base task envelope. A capable host also supplies admitted media descriptors,
delivery capabilities and an optional task-pinned modeling runtime. The agent receives event IDs;
only trusted adapters resolve them to verified task paths. The `api` phase consumes the host's
`work/toolbox-call.json` and writes `work/toolbox-response.json`. Internal stages instead use a
fixed-argv worker with `stage-call/1`; these are separate from agent-facing operation names.

Optional neural runtime code, interpreter, source/checkpoint inventory and foreground model are
operator-selected and task-pinned separately from module content. Tasks cannot install weights,
change those paths, select a new interpreter or enable network access.

## Capability and progress

`probe` must finish within five seconds and atomically write JOB/capability.json:

```json
{"schema_version":1,"supported":true,"estimated_seconds":25}
```

Return supported:false with a reason when the request or budget is unsupported. A supported request
needs a positive finite estimate within any supplied finite wall-time budget; transport and queue time are
not included. The host must check support before acknowledging that simulation has started.

Capability discovery has distinct scopes:

- `pipeline_capabilities` describes the verified composition, optional module features and current
  task runtime. Module declarations must match the bundle lock; a version string alone grants no feature.
- The internal structured modeling stage's `capabilities()` covers `modeling.prepare/build` only.
  Image inference/preview operations are separate modeling-module entrypoints advertised by the composition.
- Domain capabilities describe the pinned physics/PCB implementation. Full `physics_prepare` or
  `pcb_prepare` decides scene readiness; image `geometry_verified` and preview readiness do not.

`image_modeling.configured` means installation configuration was admitted. It does not establish
actual inference, reference fidelity, geometry eligibility, scene readiness or hardware feasibility.
`health` and a build are separate from real inference and end-to-end delivery acceptance.

During `run`, the module can atomically update JOB/progress.json:

```json
{"schema_version":1,"state":"running","fraction":0.5,"updated_at":1789900000}
```

Progress is for host queries. The transport chooses how much to show to the user.

## Result

After the appropriate purpose checks, complete ZIP verification and full video decoding pass,
write JOB/artifacts/result-manifest.json and exit 0. A physical pipeline result has this shape:

```json
{"schema_version":1,"status":"succeeded","summary":"Verified concise result",
 "verification":{"passed":true,"numerical_passed":true,
                 "checks":["simulation_quality","video_decode","data_manifest"]},
 "outputs":[{"path":"simulation.mp4","media_type":"video/mp4","sha256":"actual SHA-256"}],
 "attachments":[{"path":"data.zip","media_type":"application/zip","role":"data",
                 "size_bytes":1234,"sha256":"actual SHA-256"}]}
```

Final output and attachment paths are relative to artifacts; absolute paths, escapes and symlinks
are forbidden. Hosts must validate schema, purpose, status, verification, required outputs, size,
container integrity and SHA-256 before delivery, and revalidate before sending. The module is
responsible for domain correctness and decoding; host file checks do not independently prove
physical correctness. Failure may report status:failed or status:unsupported; it must never publish
success with an unverified file. Stdout/stderr are diagnostics, not the output protocol.

Physical delivery and numerical gates are separate. A passed visual result may retain precision
warnings while `numerical_passed`/`data_usable` are false; diagnostic data cannot support quantitative
conclusions. Solver measurements must have been declared before preparation. Recorded display
frames may be sampled/rounded and are neither full-state observations nor restart checkpoints.

## Purpose and geometry

| Route | Current inputs and outputs | Required boundary |
|---|---|---|
| Model presentation | admitted `image_id` → `model_ref` → 360-degree MP4 and model ZIP | model units by default, no simulation or measurements |
| Physical simulation | explicit parameters or audited `mesh_ref` → complete prepared scene → solver result → MP4 and data ZIP | explicit units/material assumptions, scene and numerical gates |
| Particle liquid | prepared particle primitives and liquid controls → recorded DFSPH samples → visual surface | display reconstruction does not change particle physics or measure volume |
| Rerender | verified saved result/model/checkpoint → new presentation | same task and original pins, no solver rerun or new measurements |

The current preview route is `modeling_preview_from_image` → `modeling_preview_render` →
`qq_video`; compatible compositions may also advertise `modeling_preview_cleanup` for a sealed
same-task model. It skips simulation and emits a typed `result_kind=model-preview` manifest with
`verification.simulation_performed=false` and `verification.numerical_passed=false`; its bound
render receipt separately declares `measurement_source=false`.
No fake `pipeline-simulation/1` result is created. Preview result/data `/2` bind preserved raw
geometry to an exact unchanged-coordinate display subset; `/3` independently recomputes bounded
surface processing, removals, vertex mapping and displacement. Legacy uncleaned `/1` remains
subject to its original compatible component declarations.

These raw-to-display proofs describe preview processing. Physical scientific video currently uses
the simulation mesh's recorded vertex frames and prepared triangle topology; detailed display assets
are preserved provenance/ZIP members. Uniform generated-mesh scaling, decimation rescaling and
camera fit each have their own receipts, but there is no general dynamic simulation-to-display
deformation binding. A liquid renderer's continuous screen-space surface is a visual reconstruction
of recorded particles, not a physical triangle mesh or an image-generated model.

`image_id`, `model_ref` and `mesh_ref` have distinct task/event scopes. A reference is not a path,
and two hexadecimal IDs are not interchangeable. Existing artifact descriptors bind path, role,
media type, bytes and SHA-256; legacy mesh references retain their existing JSON/key-order hash
semantics. The proposed unified v2 asset IDs and geometry bindings are not current APIs.

## Operation and asset admission descriptors

`pipeline/operation_contracts.json` describes the existing v1 operation names and
their host-facing argument shapes. `pipeline/operation_contracts.py` supplies
stdlib validation and task-scoped asset/dependency checks; it registers no new
operation and does not automatically change the composed adapter. Integration
must package both files and explicitly call the validators. Discovery should use
`operation_descriptors` filtered by the verified pinned manifest, so the catalogue
does not advertise an unavailable installation or a proposed v2 API. Returned
input/output schemas are self-contained. The shared output shape checks do not
replace each operation's existing result/scene/typed artifact validator.

The internal `AssetDescriptor` view retains existing IDs and byte hashes. It adds
purpose, task scope, role, units, frame, producer and dependencies without changing
legacy reference algorithms or rewriting canonical artifacts. Only the host may
derive descriptors from already verified task records; a caller-supplied hash or
descriptor is not file authority. Expected hashes, units and topology must come
from that trusted record, not from the descriptor being checked.

Model-preview rendering accepts a display model without a simulation reference;
physical rendering requires a saved simulation whose dependency names the exact
model/schema/hash in the same task. Geometry-binding checks match asset roles,
hashes, units, frames and topology. They do not establish watertightness, execute
raw/display cleanup proofs or validate a future deformation transfer. Existing
independent geometric/typed validators remain required. `particle_visual_surface`
describes sampled state to visual video with no fabricated triangle topology.

Recovery descriptors never execute a retry. A presentation failure may suggest
`pipeline_render_prepare` only when a preserved simulation is reported, and the
caller must still verify its original checkpoint/pins. Incomplete attempts stay
retained; unknown platform actions require receipt reconciliation without resend.

## Version identity

`registry.py publish` copies the package to `versions/<digest>` and optionally replaces active.json
atomically. The pointer contains schema_version, digest, id and version. The digest covers sorted
relative filenames, executable bits and SHA-256 of file bytes. It excludes build caches during copying.

The host persists the registry and pointer with each newly accepted task. Later activation must not
change that task's version, even on retry or restart. Keep pinned snapshots until their jobs expire.
Changing the module does not require changing or redeploying the transport.

The composed `bundle-lock.json` additionally binds engine and modeling/simulation/rendering
id, version, path, content digest and stage format. Current physical stages use
`prepared-scene/1` → `pipeline-model/1` → `pipeline-simulation/1` → `pipeline-presentation/1`.
Prepare freezes normalized model, settings and combination; changed physical inputs or limits
require a new preparation. Separate rendering/data receipts retain their own schemas and source hashes.

## Failure, recovery and platform receipts

Inspect `pipeline_status` after interruption. Reuse completed stages only after original pins,
settings and saved artifact hashes pass validation. Incomplete nonempty directories retain evidence;
do not erase or overwrite them, switch component pins or blindly retry an unchanged failed scene.
A concrete model correction can be prepared as a new compute attempt.

A saved successful simulation survives presentation/export failure. Within the same task and
original combination, `pipeline_render_prepare` → `physics_simulate` freezes render-only work and
reuses that result without another integration. Cross-task import and renderer-version migration
are not provided by this recovery API. Preview cleanup/rendering has its own source-bound caches;
it cannot promote display geometry to simulation readiness.

Module success means verified artifacts are ready for the host. The host's held transaction releases
required MP4/ZIP and selected images only after its checks. Platform actions have separate `sent`,
`failed` or `unknown` receipts. Claim delivery only from actual receipts; `unknown` is not success
and must not be automatically resent. Partial delivery must be reported per artifact, without
initiating a new computation or send to disguise uncertainty.

## Delivery byte budget

`task.limits.max_output_bytes` bounds video; the host also declares support and a separate byte limit
for required data attachments. The composed pipeline refuses execution when required ZIP delivery
is unavailable. Complete data exceeding the final limit may use lossless compression with original
member bytes/hashes preserved; it fails explicitly if still too large, without dropping fields.

Presentation encoding works from saved states, preserving the complete saved time interval and
declaring playback/interpolation separately from physical time. The composed scientific renderer
uses H.264 quality candidates and full decode checks; the standalone physics adapter has its own
bounded presentation-compression route. Neither reruns physics, changes canonical result bytes or
shortens requested physical duration to fit video size. Presentation failure is not unsupported
physics, and successful numerical data remains available for the verified recovery path.
