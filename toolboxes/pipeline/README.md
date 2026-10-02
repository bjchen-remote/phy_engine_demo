# QQ modeling / simulation / rendering pipeline

`physics-pipeline` 0.3.2 composes independently versioned modeling 0.3.2, rendering 0.3.1 and frozen simulation 0.2.0 packages with an explicitly selected physics engine snapshot. QQ keeps its **v1** envelope, existing structured `physics_*` / `pcb_*` tools, `physics_simulate` and `qq_video`. An optional Mac-local Hunyuan3D-2mini flow matching worker adds image-to-shape generation through `modeling_preview_from_image` for shape presentations and `modeling_from_image` for physical scenes; it does not train a new model or replace the solver.

The messaging bridge is installed separately; this repository publishes the engine and composable modules. The public [host integration contract](HOST-INTEGRATION.md) describes image admission, runtime pins and complete data delivery.

The [internal architecture](ARCHITECTURE.md) records component pins, prepare locks, stage checkpoints and recovery. Stage implementation maps are maintained beside the [modeling](manual/modeling/architecture.md), [simulation](manual/simulation/architecture.md) and [rendering](manual/rendering/architecture.md) Skills. The asynchronous multi-artifact contracts in `docs/pipeline-v2` remain a future interface.

## Build against the intended engine

Always pass an explicit immutable `--engine` snapshot when preserving a deployed solver. The maintenance baseline is physics **1.4.7**, content digest:

```text
f22cb4f59bc3cce75862c7a549656a94ce8c70c17b4762efd228d30399f3f41f
```

This identifies the existing engine snapshot, not a pipeline activation or QQ image-modeling acceptance receipt. Resolve it from the operator's actual registry and verify `toolbox.json` plus its content digest before building. For an operator with the corresponding verified registry snapshot (the host-dependent smoke command requires the separately installed bridge):

```sh
PYTHONDONTWRITEBYTECODE=1 python3 toolboxes/build_pipeline.py \
  --engine "$HOME/Library/Application Support/QQSimulatorToolboxes/versions/f22cb4f59bc3cce75862c7a549656a94ce8c70c17b4762efd228d30399f3f41f" \
  --output /private/tmp/pipeline-bundle

PYTHONDONTWRITEBYTECODE=1 python3 toolboxes/pipeline_smoke.py \
  --host-source /absolute/separately-installed/bridge-sdk/src \
  --engine "$HOME/Library/Application Support/QQSimulatorToolboxes/versions/f22cb4f59bc3cce75862c7a549656a94ce8c70c17b4762efd228d30399f3f41f" \
  --output /private/tmp/pipeline-acceptance
```

Output directories must be new. Omitting `--engine` selects the current source-tree packaged engine, which may differ from the live snapshot. A new engine requires its own acceptance; changing the modeling stage alone does not authorize a solver upgrade.

The smoke harness requires a separately installed compatible bridge result validator; it is host acceptance, not a standalone client included here. With that host present it uses its own temporary registry, synthetic mechanical/PCB tasks and the macOS deny-network sandbox, checks legacy queries and ZIP verification, and rerenders without rerunning the solver. It does not contact QQ or alter the live registry. `--backend python` separately exercises the reference solver route; native is the default mechanical backend. Actual local image inference, an opaque-photo foreground mask and QQ platform delivery are separate acceptance steps, recorded in [current architecture/evidence](../../docs/current-architecture.md).

The structured pipeline and QQ host retain Python **3.9+**, the pinned macOS arm64 engine, prebuilt native renderers and host ffmpeg/ffprobe. Heavy ML libraries are not imported into the host. The optional image worker uses its own Apple Silicon Python **3.12+** environment and pinned MLX/source/checkpoint installation. A build or `health` response alone does not establish real inference or peak-memory feasibility.

## Replace one independently pinned stage

```sh
PYTHONDONTWRITEBYTECODE=1 python3 toolboxes/build_pipeline.py \
  --module-only modeling --output /private/tmp/modeling-module

PYTHONDONTWRITEBYTECODE=1 python3 toolboxes/build_pipeline.py \
  --engine /absolute/immutable/physics-1.4.7-snapshot \
  --modeling-module /private/tmp/modeling-module \
  --output /private/tmp/pipeline-next
```

Equivalent `--simulation-module` and `--rendering-module` options select those roles; unspecified stages are built from current source. `bundle-lock.json` binds engine and each stage id/version/path/content digest. Modeling packages include only modeling implementation, shared stage helpers, their manual and image adapter; simulation packages include only simulation implementation, shared helpers and their manual. An edit to `pipeline_stages/modeling.py` cannot change the simulation digest by copying that file into both roles. Deliberate shared worker/helper/metadata changes still affect the corresponding packages.

Stage modules are registry-storable building blocks and cannot activate directly as QQ task toolboxes. Activate a verified **composed package**. New events pin the new combination; queued/running events retain their original combination. Keep old referenced versions; automatic version GC is not implemented. Runtime code/weight pins are separate from stage content pins so large checkpoints do not enter hot-swap bundles or Git.

After the required host/config upgrade and acceptance, publish/activate using the existing registry CLI and the actual configured registry. `build_pipeline.py --registry PATH --activate` also supports composed packages. Activation changes `active.json` atomically without restarting QQ; rollback selects the previous composed digest. The source documentation does not claim live activation or successful platform delivery without the corresponding recorded receipt.

## Optional Mac-local image modeling

The preferred Apple Silicon backend is Hunyuan3D-2mini through the pinned MLX port; the operator may instead explicitly configure the official PyTorch/MPS or CPU route. Backend/device/dtype are fixed configuration choices, with no automatic fallback. Shape inference stays offline, uses no texture generator or commercial service, and loads code/weights only in a dedicated worker. Installation instructions, exact source/checkpoint pins, dependencies and licenses are in [modeling_flow](../modeling_flow/README.md).

Host `modeling_runtime` configuration selects a dedicated root, interpreter, relative runtime config, bounded read roots and real `provider_name`. The host freezes the first runtime selection and configuration copy per task, including the disabled state. Source commits, checkpoint inventories and local foreground models are verified separately; QQ messages cannot choose paths, revisions, interpreters, commands or downloads. The optional Metal read/device capability preserves deny-network and task-only writes. `/help` discloses the provider, Hunyuan3D-2mini, AI-generated content, license and Tencent non-affiliation.

For transparent object references, supplied alpha is preserved. For opaque photos, the operator may install CPU ONNX Runtime and a pinned `segmentation/u2net.onnx`, then explicitly enable `background_removal: u2net` plus `foreground_model:{path,sha256}`. Without that setting, the full opaque image is used and recorded as not segmented. U2Net is a local estimated salient-object mask; it is not a measured contour or proof of which object the user meant. Missing/invalid model, dependency or mask fails explicitly, and inference never downloads a segmentation graph.

`pipeline_capabilities({})` dynamically reports `image_modeling.configured`, provider notice, operation name, scale/material requirements and generative fidelity from this task's runtime pin. Configured means admitted installation configuration, not proven inference, image fidelity or complete scene readiness.

## Raw-preserving model turntable delivery

When a user asks only for a reconstructed shape and a 360-degree model video,
call `modeling_preview_from_image({"image_id":"CURRENT_TASK_ID"})`, then
`modeling_preview_render({"model_ref":"RETURNED_MODEL_REF"})`. A compatible
external host independently verifies the typed result before its `qq_video`
handoff queues the reference image, MP4 and complete model ZIP with separate
receipts. The public package does not include that messaging client.

No physical size, density or material is invented. Missing dimensions mean
`model_unit`; an explicitly supplied `physical_extent_m` is an external size
assumption. The normalized raw mesh preserves every original component. In the
compatible 0.3.2 combination, display_cleanup defaults to conservative, using
the fixed bounded-floaters/1 policy to remove only eligible whole tiny detached
components from the display. Choose display_cleanup:none for an unfiltered
turntable. Retained coordinates and face winding remain exact; there is no
smoothing, filling holes, recentering, rescaling, solver or decimation. It renders neutral untextured
surfaces through the pinned native drawer, rotates the camera, verifies visible
surfaces and decodes every H.264 frame. The video and exported display bind the
same mesh. The ZIP retains separate raw and display GLB/OBJ/JSON, cleanup and
model/render receipts, reference/source receipt, provider notice and all pinned
third-party licenses. It cannot claim numerical usability or measurement.

Preview operations are advertised only when both pinned modeling and rendering
modules declare support. New cleanup-capable modeling requires a renderer with
preview_geometry_scope:render_input; mixing it with a legacy renderer disables
preview before inference. Legacy modeling with a new renderer keeps its v1
uncleaned preview. Cleanup does not identify semantic parts or repair physics.
For maintenance that preserves the deployed solver and simulation bytes,
explicitly pass `--engine` and `--simulation-module /absolute/frozen/simulation`.
Unspecified stages are rebuilt from current source; shared worker changes can
change their content digest even with the same module version string.

## Optional bounded surface processing (0.3.2)

For requested surface-noise or detached-fragment cleanup, require
`modules.modeling.surface_cleanup:"bounded-surface/1"` and
`model_preview.surface_filtering.available:true` before explicitly choosing
`display_cleanup:"surface"`. Initial image generation retains its conservative
default. The fixed surface policy removes only eligible complete micro-components
and applies six bounded Taubin smoothing pairs to eligible vertices. It protects
boundaries, sharp/skinny features and abnormal topology, retains face order and
winding, and checks orientation, area and displacement before each proposal.
It does not fill holes, invent missing anatomy, or repair a physical model.

When this task already has a sealed generated model and capability discovery
advertises `model_preview.cleanup_operation:"modeling_preview_cleanup"`, call
`modeling_preview_cleanup({"model_ref":"CURRENT_TASK_MODEL_REF", "display_cleanup":"surface"})`.
The omitted cleanup mode defaults to surface. The operation verifies the sealed
source, request, image, runtime and current module pins, then uses CPU geometry
processing and export without loading neural weights. It keeps the original
files and derives a new model reference; it does not render or send anything.
Use the returned reference in `modeling_preview_render` and inspect real front
and back frames before host handoff. Same-task and same-pin references are
required; cross-task imports, arbitrary paths and pin promotion are unsupported.
Incomplete work is retained, and cache reuse rechecks exact artifact bytes.

Surface uses `model-preview-result/3`, `model-preview-data/3` and
`modeling-surface-cleanup/1`. A compatible external host independently reproduces
the exact raw-to-display geometry and report, including component removal,
vertex mapping, coordinate movement and retained topology. It requires explicit
`raw_geometry_preserved:true`, accurate display/vertex modification disclosures,
and `semantic_fidelity_verified:false`. A checksum alone is insufficient;
cleanup-capable results cannot downgrade to v1 to bypass validation.
Conservative/none retain their unchanged-coordinate v2 proof, and legacy
uncleaned modules retain their own v1 contract. All raw and display sources stay
in the complete provenance/license ZIP, and rendering remains scoped to
`render_input`.

On one previously generated mesh, paired local inspection found modest smoothing
of small bumps while large back ridges remained prominent. This is limited
postprocessing evidence rather than a reconstruction-quality benchmark. See
[surface validation](../../docs/modeling-surface-cleanup-validation.md) and the
[typed host contract](HOST-INTEGRATION.md). Source publication, new inference,
host activation and real platform delivery are separate acceptance layers.

## Agent workflow and readiness

The host must support v1 data ZIP attachments and explicitly enable OneBot `send_file`; the required-data package otherwise fails before modeling/execution. See [host integration and data delivery](HOST-INTEGRATION.md). A first host code/config upgrade is distinct from subsequent module hot swaps.

1. Read `help({"topic":"pipeline"})` and `pipeline_capabilities({})`. Set independent modeling/simulation/rendering qualities with `pipeline_configure` before final prepare.
2. For structured requests, use the existing mechanical or PCB model tools. For image-guided physical simulation, require an admitted current-event `image_id`, explicit maximum physical extent in metres and material name/density, then call `modeling_from_image`. Sampling parameters are bounded, not shell or path arguments.
3. A passing image operation returns a task-owned `mesh_ref`, receipt/display references and audit; its `ready_to_simulate` remains **false** because it only verified geometry. Insert `mesh_ref` into an authored scene through `physics_patch`, choose motion/boundaries/initial conditions and mass assumptions, and declare numerical queries. Single photos cannot infer absolute scale, density, hidden geometry or calibrated mechanics.
4. Call `physics_prepare` or `pcb_prepare` for the complete scene and require both `ok` and `ready_to_simulate`. This freezes normalized model, settings and the component combination. A worker's mesh eligibility does not bypass full scene/budget validation.
5. Call `physics_simulate`, then `qq_video` for host handoff. The host queues verified MP4 and ZIP together and records separate receipts. Only actual receipts prove delivery.
6. For presentation failure with a saved successful simulation, inspect `pipeline_status`, call `pipeline_render_prepare`, then `physics_simulate`. The original solver result is reused after hash checks.

Runtime Skills ship with their stages: [modeling](manual/modeling/SKILL.md), [simulation](manual/simulation/SKILL.md), [rendering](manual/rendering/SKILL.md).

| Dimension | Available implementation | Explicit limitations |
|---|---|---|
| Modeling | standard structured models plus optional pinned local MLX/PyTorch single-image geometry | No tolerance-certified CAD, inferred material/scale or silent primitive substitute |
| Foreground | supplied alpha or configured local pinned U2Net CPU on opaque photos | Inferred saliency, not measured segmentation or automatic object identification |
| Simulation | inherit/visual/strict; original engine and predeclared queries | No new physical laws, arbitrary full-state queries or restart from display samples |
| Rendering | preview/standard scientific video, recorded-state interpolation and H.264 | deep/PBR/diffusion not advertised as installed |

Unsupported quality/backend/geometry fails concretely, with no silent downgrade. The detailed generated display GLB and bounded simulation mesh are separate; independent decimation candidates must pass topology/budget checks and the original engine's geometry audit. Scale transforms, simplification attempts, assumptions, hashes and source/checkpoint identities remain in receipts and exported provenance for assets used by the scene.

## Data, recovery and limits

Simulation saves `result.json` with video disabled. Presentation exports exact result bytes, model, available measurements/CSV, quality metadata, dictionary and file hashes. Used image assets additionally preserve display GLB/OBJ, simulation OBJ, modeling receipts/mesh JSON and complete third-party licenses. Macro-step observations and sampled display frames are distinct; `quantitative_usable=false` is diagnostic data. Strict numerical checks do not establish the physical calibration of a reconstructed object.

Stage checkpoints bind metadata and file hashes. Changed preparations archive the previous compute attempt; incomplete nonempty stages are retained rather than overwritten or blindly repeated. Successful simulation survives export/render failure. Rerendering remains scoped to the same task and original component pins; cross-task import and renderer-version migration are not implemented.

QQ currently accepts one MP4 plus one ZIP, default 16 MiB each. Oversized complete data fails rather than silently dropping fields. Unknown platform receipts are never resent. OneBot may run without fixed timeouts; other transports retain their configured task deadline. The executor has output/queue limits but no hard aggregate memory or temporary-disk quota. An extra artistic video needs the future multi-artifact protocol and explicit provider capability/authorization before cloud generation can be enabled.

Large complete ZIPs may use lossless DEFLATE when the stored form exceeds the delivery limit. Every original member and its hash is retained; small archives keep their previous bytes. Host validation streams CRC under the independent expanded-size cap without extracting or executing model files. The adapter also removes indentation from legacy task-owned model files that would otherwise exceed the engine's existing 1 MB admission limit, preserving every value and insertion order where legacy mesh references require it. The engine snapshot is unchanged.

## Conservative display fragment cleanup

`bounded-floaters/1` is a reversible display policy, separate from the numerical micro-component policy used for simulation. It requires a dominant surface and checks each disconnected candidate’s extent, triangle count, surface area, absolute volume and certified gap to that surface, plus aggregate removal budgets. It removes only complete eligible components, preserves every retained coordinate and face winding, and performs no smoothing, hole filling, decimation, recentering or rescaling. Thresholds are fixed in the pinned modeling code; QQ arguments cannot loosen them. The receipt records policy values/hash, component statistics and exact removed face indices.

Small detached features may be intentional. The policy does not identify eyes, accessories or anatomy, and geometry passing it does not establish semantic fidelity. Raw assets remain available for comparison and recovery; `display_cleanup:"none"` disables this processing. A multi-part object without a qualifying dominant surface is retained. Remaining fragments that fail any threshold also remain.

Capability discovery requires the modeling cleanup declaration and a rendering module declaring `preview_geometry_scope:"render_input"`. New modeling with a legacy renderer does not expose or start the preview operations; legacy modeling with a new renderer remains compatible as an uncleaned v1 preview. The host independently checks original raw pins, exact retained faces/vertices and whole-component eligibility before accepting the v2 archive or video. No cleanup result is promoted to physics readiness.

Candidates must also be closed, consistently oriented, positive-volume and free of degenerate faces. Unreferenced vertices, numerical ambiguity or exhaustion of fixed triangle-check/node-visit budgets keep the complete raw mesh as display.
