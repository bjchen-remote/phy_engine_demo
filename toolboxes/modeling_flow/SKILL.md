---
name: modeling-flow-local
description: Internal offline Mac-local flow matching worker for single-image shape modeling, separate from simulation and rendering.
---

# Image modeling worker

Use the QQ host's `modeling_preview_from_image` for shape reconstruction and
`modeling_preview_render` for a model turntable. Use `modeling_from_image` when
the task explicitly requests a physical simulation. Both need the verified
operator-installed runtime. Refer to the trusted
attachment ID, not an invented filesystem path. This package is the worker that
the modeling stage invokes, not a general shell tool or a simulation/rendering
skill. Keep the existing structured modeling route for explicitly specified
geometry.

If a sealed preview `model_ref` already exists in the current task and the pinned
capabilities advertise `model_preview.cleanup_operation:"modeling_preview_cleanup"`,
use `modeling_preview_cleanup({model_ref,display_cleanup:"surface"})` for requested
surface cleanup instead of rerunning image inference. That CPU geometry/export
operation returns a new task-owned `model_ref`; render that exact new reference.
It retains the original raw/source artifacts, does not load neural weights and
does not render or send by itself. It cannot import a model from another task,
promote old module pins, or accept paths. The cleanup operation defaults to
surface when omitted; the initial image-preview operation still defaults to conservative.

Require an explicit physical extent in metres and declared material density
before preparing a physics asset. A visual shape uses the separate
`modeling-flow-preview-request/1`: only an image is required, material is forbidden,
decimation is disabled, and missing dimensions mean `model_unit`. Export the
raw GLB/OBJ/JSON, display assets, cleanup receipt, reference and licenses.
The compatible preview defaults to `display_cleanup: conservative`; its fixed
`bounded-floaters/1` policy may remove only eligible whole tiny disconnected
components from the display. `none` keeps all generated components in the video.
For an explicit request to reduce scattered debris, rough ridges or surface noise,
check the pinned modeling module's `surface_cleanup: bounded-surface/1` capability
and use `display_cleanup: surface`. This optional 0.3.2 mode removes only bounded
whole detached micro-components, including eligible open/flat debris, and smooths
eligible vertices under fixed displacement and triangle-orientation limits.
It preserves boundary, nonmanifold and sharp/thin feature neighborhoods; noisy
sharp protrusions may therefore remain. It does not recognize the back side,
complete hidden surfaces, delete connected patches or fill holes. Keep the
conservative default unless surface processing was requested.
Keep the raw normalized mesh intact and explain that geometric thresholds do
not guarantee semantic identity. Surface output uses `model-preview-result/3`
and `model-preview-data/3`: the host independently recomputes the fixed algorithm,
vertex mapping, removals and displacements from raw geometry, then verifies the
same display mesh used by the video and ZIP. Hashes alone are insufficient.
Read the cleanup receipt's `applied`, `analysis_complete`, `skip_reason` and
displacement/protection statistics. A budget no-op preserves raw geometry and
must be described as unprocessed; zero moved vertices is not proof that the
requested roughness was corrected. None of these display modes creates a simulation mesh, mass
or numerical result. Explain
that a physical extent and density are assumptions if supplied later; never
describe them as values recovered from the image. Prefer an isolated foreground
image. This worker preserves provided alpha and applies EXIF orientation.
Opaque photographs use an offline estimated U2-Net foreground mask only when
the operator enabled a verified local segmentation graph; otherwise the whole
image is used. Explain the actual preprocessing from the returned receipt.

Explain that unobserved surfaces come from a pretrained generative prior.
Hunyuan3D-2mini estimates one plausible shape; one photograph does not identify
the true back side, wall thickness, cavities, exact dimensions or material. Do
not substitute silhouette extrusion, language-invented geometry or primitives
when this operation fails. Return missing runtime, checkpoint, GPU, inference,
decimator or topology errors explicitly.

Use the detailed `display.glb` for visual inspection and the independently
audited, bounded `simulation.obj` / `simulation_mesh.json` for physics.
Simulation reduction may remove only explicitly measured numerical micro-components
under the fixed all-threshold policy, with each removal disclosed in the receipt.
Detailed display geometry stays intact; significant parts, shells and cavities are
retained. Topology-preserving QEM is still independently audited, including the
pipeline's original engine intersection gate. Never use largest-component selection
to discard meaningful parts or describe cleanup as exact reconstruction.
The worker's `ready_to_simulate` establishes bounded mesh eligibility, not verified
physical accuracy or a prepared scene. The QQ modeling adapter additionally runs
the engine mesh gate and returns `mesh_ref`; insert it into the authored scene,
set explicit mass/physical parameters and pass `physics_prepare` before running
a simulation. Preserve source hashes, seed,
checkpoint identity, receipt and assumptions across downstream handoffs. Keep
simulation and rendering as separate stage operations. Never hide a decimation,
scale change, backend/dtype/device choice or topology rejection.

Runtime installation and updates are operator actions. Agent-generated requests
must not choose paths, executable commands, remote services, model revisions or
download instructions. Runtime inference stays offline; the trusted host selects
the pinned source, checkpoint inventory and local interpreter.

Read [README.md](README.md) for module boundaries, backend options, limits,
receipt semantics and developer CLI contracts.
