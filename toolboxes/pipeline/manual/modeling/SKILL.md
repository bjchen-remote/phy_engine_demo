---
name: physics-pipeline-modeling
description: Generate current-event image models for a 360-degree shape preview, or prepare structured mechanical and PCB thermal models with explicit physical assumptions before simulation.
---

# Generate or prepare a model

模块内部实现、图片重建与完整场景准备的边界见 [内部架构](architecture.md)。

This is the runtime modeling Skill for the current v1 composition. The source is
`toolboxes/pipeline/manual/modeling/`; the builder copies this directory into the
modeling module's `manual/`. Edit the source and rebuild a new composition rather
than changing a generated or pinned package. `docs/pipeline-v2` describes a future
interface; its asynchronous jobs and asset contracts are not current tool names.

Discover capability at the correct layer: `pipeline_capabilities` reports the
current composition and task runtime; domain capabilities report the pinned
engine's physics; the internal structured stage's `capabilities()` covers only
structured `modeling.prepare/build`. Image generation is a separate operation
within the modeling module. `image_modeling.configured` admits configuration,
not successful inference, reference fidelity or scene readiness. Require the
actual operation result and the appropriate readiness flag before proceeding.

For “make this model liquid”, first select the current task's sealed `model_ref`
or ask the host to authorize and import a durable model as `imported_asset_ref`.
Never pass a previous task's bare reference. Require an explicit physical maximum
extent, discover `model_liquefaction.available`, and call `modeling_liquefy` with
`physical_extent_m` and `entity_id`. It uses raw geometry and returns a sealed
whole-fluid `fluid_ref`; follow its whole-entity `physics_patch` action, then
`physics_prepare` and `physics_simulate`. The source solid must be replaced.
Water density is fixed at 1000 kg/m³; conservation means the scaled source volume
at t=0. Conversion has no thermal phase model and does not preserve source solid
mass. Open or ineligible geometry and insufficient resolution fail explicitly.
Read an installed operation's exact schema via `help(topic=operation:<name>)`.

Simulation-only reduction uses audited topology-preserving QEM. It may remove only
fixed-threshold numerical micro-components, recorded individually in the receipt;
display geometry is retained. Significant separate parts and cavities remain and
can legitimately fail the single-solid simulation gate. Keep the original engine
intersection audit and full-scene preparation; never select the largest component
or fabricate a primitive to turn a failed reconstruction into success.

Use the host's toolbox-call bridge. Choose the requested purpose first. A 360-degree
model presentation uses `modeling_preview_from_image` → `modeling_preview_render`
→ `qq_video`; it does not need a fabricated physical size or density and does not
run simulation. `physics_simulate` remains the execution entry for the physical
modeling → simulation → rendering pipeline. Internal stage modules are not QQ
tools or commands to invoke directly.

## Image model presentation

1. Read `pipeline_capabilities({})` and require the image runtime and model-preview operations to be available in the current pinned combination. Reading a PDF, searching the web or downloading an image is input preparation; each reference must still be checked visually against the user's object.
2. Call `modeling_preview_from_image` with the admitted current-event `image_id`. Sampling settings are bounded optional parameters. Omit `physical_extent_m` unless the user supplied that physical size; the default output uses `model_unit`. Do not invent material, density, mass, motion or a physical scene for a shape-only request.
3. Require `ok` and `ready_to_preview`, retain the returned task-owned `model_ref`, and inspect the reconstruction assumptions and display audit. When cleanup is advertised, inspect its fixed-policy receipt: the default conservative mode may remove eligible whole tiny disconnected components from display assets, while raw GLB/OBJ/JSON retain every generated component. Use `display_cleanup:"none"` when the user wants an unfiltered model. For a requested reduction of scattered debris, rough ridges or surface noise, require `modules.modeling.surface_cleanup:"bounded-surface/1"` and `model_preview.surface_filtering.available:true`, then explicitly set `display_cleanup:"surface"`. Its fixed policy can remove eligible whole detached open/flat debris and smooth eligible vertices within displacement and triangle-orientation limits. It preserves boundary, nonmanifold, sharp and thin feature neighborhoods, so some pointed noise may remain. Inspect `applied`, `analysis_complete`, `skip_reason`, moved/protected vertex counts and displacement statistics; an operation-budget no-op means no processing was performed. Cleanup has no semantic fidelity guarantee, does not identify the back side, recover hidden surfaces, delete retained connected patches or fill holes, and cannot establish physics readiness. Preview readiness does not assert closed-solid topology, calibrated mechanics or `ready_to_simulate`.
4. Pass that exact `model_ref` to `modeling_preview_render` for the 360-degree camera presentation, then hand its verified MP4 to `qq_video`. The host validates and queues MP4 plus the complete model ZIP, and any selected reference images, under the held delivery transaction. The preview contains the exact display mesh used by the video, preserved raw geometry, cleanup/generation receipts and licenses; it has no simulation or measurement results. Surface mode uses `model-preview-result/3` and `model-preview-data/3`, whose host verifier independently recomputes removals, retained topology, mapping and bounded coordinate changes. Conservative/none use `model-preview-result/2` and `model-preview-data/2` when cleanup is supported; their unchanged-coordinate submesh proof cannot authorize smoothing. These numbered preview contracts run inside the current v1 host protocol and are separate from the proposed pipeline-v2 interface.
5. Describe hidden surfaces, cropped anatomy and uncertain image identity honestly. A photo provides no absolute scale or material measurement. If the user later asks for physical simulation, use the separate physical reconstruction and full-scene preparation below; a successful preview cannot bypass those checks.

When a sealed model already exists in this current task, read
`model_preview.cleanup_operation` before recomputing it. If it advertises
`modeling_preview_cleanup`, call that operation with the exact current-task
`model_ref` and explicit `display_cleanup:"surface"` for requested mesh denoising.
It derives a new model reference from the preserved raw geometry using CPU
geometry/export only; it does not run the image model, change the reference,
render a video or release delivery. Pass its new `model_ref` to
`modeling_preview_render`, inspect actual frames, then complete `qq_video`.
Original files are immutable, and valid repeated calls reuse only verified
same-task/same-pin inputs. Cross-task references, arbitrary file imports and
automatic pin promotion are unsupported. This cleanup operation's omitted mode
defaults to surface; the initial image-preview default remains conservative.

## Physical model preparation

1. Call `pipeline_capabilities({})` and the relevant domain capabilities. Choose `physics` for mechanical scenes or `pcb_thermal` for board temperature. Use `help({"topic":"pipeline"})` for pipeline settings and the existing physics help topics for model fields.
2. If the request refers to an earlier experiment, call `context({})`, preserve its domain and every unmentioned physical parameter, and modify the returned verified model. If context is unavailable, obtain the missing parameters instead of inventing a prior experiment.
3. Set requested options before final preparation with `pipeline_configure`. Supported values are `modeling_quality:"standard"`, `simulation_quality:"inherit"|"visual"|"strict"`, and `rendering_quality:"standard"|"preview"`. Configuration may include only the fields that need changing. High modeling quality and deep rendering are currently unsupported; report the capability result without silently substituting standard quality.
4. Build structured input with the existing operations: mechanical `physics_capabilities`, `physics_example`, `physics_system`, `physics_mesh`, `physics_liquid`, `physics_patch`, `physics_validate`, `physics_estimate`; PCB `pcb_example` and `pcb_validate`. Examples are optional starting points. Geometry, units, material assumptions, initial conditions, boundaries and requested physical duration must match the user's intent.
   For image-to-3D reconstruction, first check `pipeline_capabilities.image_modeling.configured`, then call `modeling_from_image` with the validated event attachment `image_id`, explicit `physical_extent_m` (maximum bounding-box extent in metres), and `material:{name,density_kg_m3}`. Optional `seed`, `num_inference_steps`, `guidance_scale`, `octree_resolution`, `num_chunks` are bounded sampling settings. The local flow matching model produces a separate display GLB and a bounded simulation mesh, reducing only when needed and permitted. Use its `mesh_ref` in `physics_patch`; do not copy vertices into tool arguments. Set entity mass from the declared homogeneous-solid density only if that assumption matches the request, then prepare the full scene. Geometry success is not simulation readiness. If the backend is unavailable or geometry fails its gate, report the returned limitation; do not replace image reconstruction with invented primitives. Single images cannot determine physical scale, density, hidden surfaces or calibrated mechanics. Supplied alpha is preserved; opaque photographs use the whole image unless the operator explicitly installed and pinned the local U2Net CPU foreground model. Read the receipt's actual preprocessing and assumptions rather than assuming background removal or precise object identification.
5. For numerical questions, declare the required mechanical `queries` before preparation and select strict simulation validation. Data export cannot recover an undeclared macro-step measurement. High rendering quality would not improve the physical model or certify measurements.
6. Finish with `physics_prepare({"scene_json":"..."})` or `pcb_prepare({"spec_json":"..."})`. Require both `ok` and `ready_to_simulate`. This freezes the prepared pipeline settings and model for execution. Use the exact returned normalized JSON for any subsequent call.
7. Call `physics_simulate` once with that prepared model, or omit its JSON argument to use the saved preparation. The host assigns paths, starts the independently pinned stages and enforces execution limits. Do not supply shell commands, filesystem paths, provider credentials or internal module calls.

Correct a failed validation only through a concrete change that preserves explicit user requirements; inspect the new preparation estimate. Never claim tolerance-certified CAD, measured reconstruction, calibrated materials or physics outside the reported solver capabilities. PCB uses a 2D effective thermal sheet with prescribed component powers; it does not resolve circuitry, traces, vias or package internals.

Keep geometry roles explicit. `model_ref` identifies a task-owned image-model
attempt; `mesh_ref` identifies the audited simulation mesh inserted into a scene.
Neither is an arbitrary file path or a cross-task import. Uniform physical scaling
centers the generated bounding box and preserves its orientation; it does not
align the model with gravity. Simulation reduction may rescale a candidate again
to preserve the supplied extent, as recorded in its receipt. The physical video
uses the simulation mesh's recorded vertices and topology. Preserved detailed
display assets are exported provenance; current code provides no general dynamic
simulation-mesh-to-display-mesh deformation binding. Raw-to-display cleanup proofs
are separate from that missing binding.

Liquid scenes use the engine's particle primitives and declared liquid controls;
an image-generated closed mesh is not automatically a liquid volume or a DFSPH
particle initialization. Rendering a continuous liquid surface does not change
particle collision geometry or supply a measured surface mesh.

If the task already ran, inspect `pipeline_status({})` before further execution. For presentation-only changes, use `pipeline_render_prepare` as described by `help({"topic":"rendering"})`; retain the saved model and simulation. See `help({"topic":"simulation"})` for result verification, data usability and delivery.

Do not repeat an unchanged failed model, erase an incomplete attempt, switch pins
or treat `unknown` delivery as permission to resend. Hash-verified reuse and
same-task, original-pin render-only recovery are distinct from rerunning failed
physics. Only actual platform receipts establish delivery.
