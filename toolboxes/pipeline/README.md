# QQ modeling / simulation / rendering pipeline

`physics-pipeline` 0.2.1 composes three independently versioned 0.2.0 stage packages and an explicitly selected physics engine snapshot. QQ keeps its **v1** envelope, existing structured `physics_*` / `pcb_*` tools, `physics_simulate` and `qq_video`. An optional Mac-local Hunyuan3D-2mini flow matching worker adds image-to-shape generation through `modeling_from_image`; it does not train a new model or replace the solver.

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

## Agent workflow and readiness

The host must support v1 data ZIP attachments and explicitly enable OneBot `send_file`; the required-data package otherwise fails before modeling/execution. See [host integration and data delivery](HOST-INTEGRATION.md). A first host code/config upgrade is distinct from subsequent module hot swaps.

1. Read `help({"topic":"pipeline"})` and `pipeline_capabilities({})`. Set independent modeling/simulation/rendering qualities with `pipeline_configure` before final prepare.
2. For structured requests, use the existing mechanical or PCB model tools. For image reconstruction, require an admitted current-event `image_id`, explicit maximum physical extent in metres and material name/density, then call `modeling_from_image`. Sampling parameters are bounded, not shell or path arguments.
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
