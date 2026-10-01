# Host integration for the composed physics pipeline

This repository publishes the physics engine, independently pinned modeling,
simulation and rendering modules, and their transport-neutral adapter. A QQ or
other messaging bridge is installed separately. Account credentials, group
permissions, incoming-message history, delivery queues and runtime installations
belong to that host and are not part of this repository or a toolbox package.

The adapter preserves the v1 task envelope described in [PROTOCOL.md](../PROTOCOL.md)
and adds typed agent calls, validated image descriptors, an optional local runtime
pin and a required data ZIP attachment. The older text/video example in the base
protocol does not describe these optional fields. Hosts must explicitly support
the fields below before advertising image modeling or complete data delivery.

## Standalone installation and checks

From the repository root, an operator can build a composed package and install
image inference without any messaging client:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 toolboxes/build_pipeline.py \
  --engine /absolute/verified/physics-toolbox-snapshot \
  --output /absolute/new/pipeline-package

python3 toolboxes/install_modeling_flow.py \
  --output /absolute/new/modeling-runtime \
  --python /absolute/apple-silicon/python3.12 \
  --provider-name "YOUR_ACTUAL_SERVICE_OPERATOR"

/absolute/new/modeling-runtime/.venv/bin/python \
  -m toolboxes.modeling_flow health \
  --config /absolute/new/modeling-runtime/config.json
```

Use actual dedicated absolute paths and a real provider name. The installer
requires an Apple Silicon Python >=3.12, downloads only fixed source/dependencies
and pinned weights during installation, preserves licenses, and writes
`host-config.fragment.json`. U2Net CPU foreground processing is included by
default; `--foreground-model none` keeps supplied-alpha modeling without the
foreground graph. Runtime inference stays offline. A health pass checks
installation and hardware availability, and reports `inference_verified:false`.
Perform a real image inference before treating that installation as accepted.
The [backend README](../modeling_flow/README.md) documents direct inference,
typed request fields and model limits.

The public engine/toolbox tests run without a QQ account. The
`pipeline_smoke.py` host-acceptance harness also calls a separately installed
bridge's result validator. Select that private SDK with `--host-source PATH` or
`QQ_SIMULATOR_HOST_SOURCE`; no compatible SDK means an explicit setup error, not
a simulated host acceptance. Its host-dependent acceptance is not a standalone
client supplied by this repository. Real platform delivery requires the chosen
host and actual platform receipts.

## Task envelope and fixed invocation

The host creates a fresh task directory with `inputs/`, `work/` and `artifacts/`.
It writes `task.json`, pins one verified composed package, and invokes its fixed
entrypoint with an argument vector:

```text
HOST_PYTHON PACKAGE/adapter.py --phase api|probe|run --task JOB/task.json
```

For an agent call, the host writes `work/toolbox-call.json` with
`{"schema_version":1,"operation":"pipeline_capabilities","arguments":{}}`.
The API phase writes `work/toolbox-response.json`; read its `result` object.
Probe writes `capability.json`, and run writes the final artifact manifest.
Serialize calls within one task. Do not assemble a shell command from message
text, operation names or arguments.

An image-enabled task has this shape. Replace illustrative hashes with the
actual SHA-256 values of host-owned normalized bytes and configuration:

```json
{
  "schema_version": 1,
  "task_id": "host-generated-task-id",
  "request": {
    "text": "User request",
    "trust": "untrusted_external_input",
    "plan_required": true,
    "input_images": [{
      "id": "input-image-1",
      "role": "direct_image",
      "path": "inputs/input-image-1.png",
      "mime": "image/png",
      "size_bytes": 12345,
      "sha256": "REPLACE_WITH_ACTUAL_IMAGE_SHA256",
      "width": 512,
      "height": 512
    }]
  },
  "limits": {
    "wall_time_seconds": 300,
    "max_output_bytes": 16777216,
    "network": false
  },
  "delivery": {
    "data_attachments": true,
    "max_data_bytes": 16777216
  },
  "output_dir": "artifacts",
  "work_dir": "work",
  "modeling_runtime": {
    "schema_version": 1,
    "root": "/absolute/dedicated/modeling-runtime",
    "python_executable": "/absolute/dedicated/modeling-runtime/.venv/bin/python",
    "read_roots": [
      "/absolute/dedicated/modeling-runtime",
      "/absolute/dedicated/base-python"
    ],
    "config_path": "work/modeling-runtime/config.json",
    "config_sha256": "REPLACE_WITH_ACTUAL_CONFIG_SNAPSHOT_SHA256",
    "provider_notice": "Service provided by YOUR_ACTUAL_SERVICE_OPERATOR. AI generated; no Tencent affiliation, sponsorship or endorsement."
  }
}
```

All image and configuration snapshot paths are task-relative and remain inside
this task. The optional runtime's absolute paths come only from trusted operator
configuration. The installer fragment uses `config:"config.json"` relative to
its runtime root; the host copies those bytes into the task's
`work/modeling-runtime/config.json` and supplies the snapshot hash shown above.
`modeling_runtime:null` means unavailable and is a valid fixed task choice.

## Admission, isolation and ownership

The host owns authorization, image decoding/normalization, MIME/dimension/size
checks, metadata stripping, task-private file placement and descriptor hashes.
Only descriptors admitted for the current task may enter `input_images`. The
adapter independently confines paths and rechecks hashes; it does not replace
the host's authorization or media-admission layer. Bound the total attachment
count and size at admission. The backend additionally caps each still image at
32 MiB / 16 megapixels and rejects unsupported or animated input.

Agent-visible image identifiers are opaque task handles. Neither a prompt nor a
tool argument may select an interpreter, host path, model revision, network
endpoint, shell command or download. Quoted/forwarded content remains untrusted
data; the host must authorize access to it before creating a descriptor.
References, prior experiments and follow-up models must come from an explicitly
authorized compatible context. Do not attach an unrelated cached scene to a new
image request. Ongoing tasks retain their original input, component and runtime
pins after a hot swap; never redirect them by rebuilding `task.json` from current
global settings.

Persist the first verified package/registry pin and runtime choice, including a
disabled runtime. Snapshot the runtime configuration, reject later changes, and
retain referenced versions. The worker separately verifies source Git and model
file pins. Permit reads only of the task, trusted package/system runtime and
explicit dedicated runtime/base-Python roots; permit business writes only in the
task. Deny network during inference. Apple Metal device access can be allowed
without granting network or arbitrary filesystem writes. Enforce deadlines,
process-group termination, output limits and admission capacity in the host.

The structured host/adapter remains Python >=3.9; heavy inference runs in a
dedicated Apple Silicon Python >=3.12 environment. Do not import ML libraries or
weights into the messaging process. A standalone stage module cannot activate as
a complete task toolbox: compose it with the original engine and other stages,
verify the bundle, then atomically select the composed digest for future tasks.

## Document pages, embedded images and public references

Document reading, public-web acquisition and returning pictures are optional
host capabilities. They do not add network access, document parsers or platform
send APIs to the modeling, simulation or rendering modules. Enable each host
capability explicitly and check the installed reader and transport before
advertising it. An older host without these capabilities can still use its
existing validated image input and MP4/data delivery contracts.

A host can admit an image derived from an authorized current-task document or
public reference page into the same image operation:

1. Admit the current event's source document or selected public reference into
   the task's private files. Keep the source identity, original document name or
   reference page URL, source-byte SHA-256 and acquisition/reading receipt. For
   a reference image, record both the admitting page and the fetched image,
   including their final URLs after validated redirects. A search result or
   HTML candidate is only a candidate; it does not prove image bytes were read.
2. Read a bounded document range or fetch the selected public image through
   host-owned facilities. Render PDF pages or decode Office embedded images as
   appropriate; do not claim an embedded image represents a complete document
   page. Normalize the selected still image, strip unnecessary metadata,
   decode its complete pixels, and check MIME, dimensions and byte limits.
   Bound source downloads, archive expansion, page/image counts, subprocess
   resources and total normalized-image bytes independently.
   Supported-image servers can mislabel one supported format as another. Require
   both the declared MIME and sniffed container to be supported; use the fixed
   decoder for the actual container, and retain both formats in the acquisition
   receipt. Never admit HTML, SVG or executable bytes merely because a server
   labels them as an image. Inspect actual pixels for subject identity; a matched
   page title and a successful download do not establish the right reference.
3. Assign a host-generated current-task image ID and an immutable receipt
   linking the normalized image to its exact admitted source. Store only
   regular files at fixed host-owned locations inside the task. Recheck source
   admission, source and receipt hashes, normalized bytes and pixel dimensions
   before exposing the image to a model or importing it for modeling. Reject
   changed sources, symlinks, special files, duplicate/conflicting IDs and path
   escapes. An ID from another event or a global cache is not an admission.
4. Add the selected image's minimal descriptor to
   `task.request.input_images`, then call `modeling_from_image(image_id=...)`
   with the existing explicit scale and material assumptions. This is the
   existing adapter field; there is no new top-level `task.input_images` schema.
   Document-page, embedded-image and reference-image IDs are host-defined
   handles. The adapter requires exactly one matching descriptor and rechecks
   its task-relative path and SHA-256. It does not fetch the source or interpret
   a URL, document filename or arbitrary local path as an image ID.

A minimal admitted descriptor has the same fields as the direct-image example:

```json
{
  "id": "host-reference-image-1",
  "role": "public_reference_image",
  "path": "inputs/host-reference-image-1.png",
  "mime": "image/png",
  "size_bytes": 12345,
  "sha256": "REPLACE_WITH_ACTUAL_NORMALIZED_IMAGE_SHA256",
  "width": 512,
  "height": 512
}
```

The host can use a different fixed task-relative image directory. Retain the
full source receipt in the host's private provenance records and bind it to
this descriptor's ID/hash; those host records are not a new stage input format.
Source text, quoted or forwarded content, document properties and page/image
captions remain untrusted data. They cannot change event authorization, select a
network endpoint or executable, grant a send permission, or become instructions
for the stage. A document-reader failure must be reported as a reading failure,
not evidence that the language model lacks image input or that physics failed.

Hosts performing public-web acquisition must reject private/local destinations,
credentials in URLs and unsafe redirects, and apply explicit request, deadline
and byte limits. Use an operator-configured reader with fixed invocation and
private scratch files; do not execute document macros, archive members or
source-provided commands. Checkpoint/source downloads remain an installation
operation, separate from reference acquisition. Once admitted, the image enters
the same pinned offline inference and scene-readiness workflow as a direct
image. Reading a document or finding a reference does not recover real scale,
material properties, unseen surfaces or an executable physical model.

## Typed image operation and scene readiness

Call `pipeline_capabilities` first. Its `image_modeling.configured` describes the
task's admitted runtime, not successful inference or calibrated geometry. The
image call accepts only current-task IDs, explicit physical assumptions and
bounded sampling settings:

```json
{
  "schema_version": 1,
  "operation": "modeling_from_image",
  "arguments": {
    "image_id": "input-image-1",
    "physical_extent_m": 0.3,
    "material": {"name": "declared homogeneous polymer", "density_kg_m3": 1000},
    "seed": 42,
    "num_inference_steps": 30,
    "guidance_scale": 5,
    "octree_resolution": 128,
    "num_chunks": 2000
  }
}
```

The host route interprets `physical_extent_m` as maximum generated mesh extent in
metres. It preserves alpha or uses the operator-configured pinned CPU U2Net graph
for opaque images. Both masks and unseen surfaces are unverified estimates.
Scale and density are supplied assumptions, not values recovered from pixels.

A successful operation returns `mesh_ref`, `model_ref`, `display_model_ref`,
`receipt_ref`, topology evidence and a density-based solid mass estimate. The
engine also audits the reduced mesh, including its intersection checks. The
public operation still returns `ready_to_simulate:false`: insert `mesh_ref` into
an authored scene with `physics_patch`, choose dynamics/material/mass/boundaries
and predeclare queries, then require a successful full `physics_prepare` before
`physics_simulate`. A mesh or video quality pass does not certify real-object
mechanics, exact CAD, hollow walls or measured material properties.

`physics_simulate` returns the host execution handoff; the host then probes and
runs the pinned adapter. Messaging helpers such as `qq_video` belong to the
separately installed bridge. No toolbox stage sends messages to the platform.

## Complete data delivery and provider disclosure

The composed package requires both MP4 and a complete data ZIP. A host must
advertise `delivery.data_attachments:true` only when its actual transport can
send a file, and supply `max_data_bytes`. Otherwise required-data preparation or
execution fails before publishing a success. A transport-specific file-send
permission is separate from a video-send permission.

On success `artifacts/result-manifest.json` uses v1 `outputs` for one
`video/mp4` and `attachments` for one `application/zip` with role `data`, byte size
and SHA-256. The host must require success/verification, confine relative paths,
reject symlinks/special files and recheck bytes/hash/media headers before making
files sendable. Module validation is evidence; host file validation alone does
not independently prove numerical or physical accuracy.

The ZIP preserves original result/model bytes, available measurements and CSV,
quality/data dictionaries, source-stage evidence and hashes. For image assets
actually referenced by this scene it also includes detailed display GLB/OBJ,
bounded simulation OBJ, mesh JSON, generation receipts and complete third-party
licenses. Images, model checkpoints, Python environments, credentials, unrelated
tasks and communication history are not model-export members. Runtime receipts
can describe local installation paths; treat exported receipts as task-scoped
provenance rather than public installation configuration.

Small packages retain stored ZIP bytes. Larger complete packages may use lossless
DEFLATE, preserving every member's original bytes and hash. Apply independent
compressed and expanded caps (128 MiB maximum expanded data), bounded member
counts, safe relative names, duplicate/path-escape rejection, inert extension
allowlists (`.json`, `.csv`, `.txt`, `.glb`, `.obj`), regular-file modes and streamed
CRC validation. Do not extract or execute archive members during admission.
GLB/OBJ are data files; the host does not import or execute them to verify
delivery. Failure to fit complete data is explicit, never silent field dropping.

Display the actual service operator, model identity, AI-generated status,
applicable licenses and Tencent non-affiliation/non-endorsement in capability
help and generation notices. The installer writes a provider notice and complete
license copies; do not represent public model weights as unrestricted MIT
weights. Language-model service credentials remain host-owned and never enter a
task or toolbox package.

Queue video and data only after every required artifact passes and record
separate real transport receipts. Unknown receipts are neither success nor
permission to resend. Delivery, scientific validation and source publication
are distinct evidence. A render/export failure retains its successful simulation
checkpoint; the same task and component pins may rerender through
`pipeline_render_prepare` without rerunning the solver. Retain incomplete stages
for explicit recovery instead of overwriting or blindly replaying them.

## Optional reference-picture delivery

An optional host `send_image` permission is separate from video and data-file
permissions. Accept current-task image IDs through a typed host API, resolve
those IDs using their admitted source receipts, and revalidate the source,
normalized pixels, hashes, confinement and limits. The API must not accept a
remote URL or arbitrary host file path as a picture to send. The transport
receives verified image bytes; a local bridge can encode those bytes instead of
asking the platform to fetch a URL or open a host path.

For a picture-only request, enforce the host's single-reply rule and reserve the
same reply capacity used by its other direct responses. For a planned
simulation, keep its acknowledgement and reference-picture bundle held and
non-dispatchable while reading, modeling, solving, rendering and exporting.
Only after all required output artifacts and all selected pictures pass their
checks should one durable transaction release the acknowledgement, pictures,
optional result text, MP4 and required data ZIP in that order. A preparation failure, failed pre-release
validation or failed commit cannot expose an early picture or a partial
success. Cancel held pictures when the simulation fails or expires, and publish
only the appropriate authorized failure response. When an older package has no
optional data attachment, the notice must describe only its actual outputs.

Bound the image count and both per-image and total bytes before release. A host
may group multiple pictures into one image action, with one receipt for that
bundle, while keeping its video and ZIP receipts separate. State the selected
contract clearly: a grouped receipt does not prove separate per-picture sends.
Generate reference citations from verified metadata in the host, not from
source-page instructions. Show the reference page URL or a clearly identified
origin with the full URL retained in provenance; identify document images by the
safe original document name and page number where applicable. Do not expose
local installation or task paths in captions.

Recheck queued pictures immediately before dispatch, and keep durable delivery
states for each image bundle, video and data action. An unknown image receipt
must never trigger an automatic resend. Its unknown state does not become a
successful picture receipt when a later MP4 or ZIP succeeds. If the transport
explicitly rejects the picture, retain that failure even when subsequent
verified video/data delivery succeeds. Preserve incomplete/unknown picture jobs
and source receipts for explicit recovery; cancelled held pictures and fully
completed deliveries can follow normal retention after preserving small
reproduction and review evidence. Keep picture, video and data statuses visible
in local review.

These are host delivery responsibilities. No package, stage worker, inference
runtime or document supplied by a user acquires platform-message authority.
Neither a reference-picture receipt nor a model-export receipt is numerical
validation, and source acquisition is not proof of final platform delivery.

## Saved-model preparation for isolated host checks

Composed package 0.2.1 adds `agent_api.context_preparation` metadata without
changing its engine or stage digests. Each exact saved-model domain selects an
`operation` and one JSON-string `argument`; the operation must also appear in
`agent_api.operations`. The default/physics entries declare
`physics_prepare(scene_json)` and the PCB entry declares `pcb_prepare(spec_json)`
when available from the selected engine. Unknown domains are not routed to a
default silently.

A host checking a saved schema-1 wrapper sends its `scene` through the declared
preparation operation, requires exact `ok:true` and `ready_to_simulate:true`, then
probes and executes the pinned package. Copying a saved scene file alone does not
create a composed plan or frozen lock. Missing contracts or incomplete preparation
mean the model needs preparation; they are not evidence that a solver failed.
Execution and artifact validation must still pass before recording a successful
local check. This procedure has no platform-delivery authority.

## Independently reused modeling versions

The 0.2.2 composer can reuse a pinned legacy modeling 0.2.0 stage without the new
PyMeshLab reducer. Its original four license exports remain valid. Modeling
0.2.1, future/custom versions and any stage containing the reducer must also
export the full PyMeshLab GPL-3.0 text; a missing license fails the export.
Do not edit published package bytes to add the dependency or a license.
Install the exact pinned worker dependencies separately, publish a fresh module,
and preserve each accepted task's component and runtime pins.

## Typed raw-preserving model preview (0.3.1)

`modeling_preview_from_image` accepts the admitted current-task `image_id` and
bounded sampling parameters. An optional explicit `physical_extent_m` supplies
an external dimension; absent dimensions mean `model_unit`. Material/density
are forbidden. The distinct `modeling-flow-preview-request/1` disables decimation
and produces shape assets plus receipts, no simulation mesh or mass.
`configuration_file_sha256` binds the exact task configuration snapshot; the
canonical configuration hash remains diagnostic.

For the compatible cleanup combination, optional `display_cleanup` is
`conservative` (default) or `none`. `bounded-floaters/1` removes only entire tiny
disconnected components satisfying every immutable gate. It requires a dominant
surface, bounds component extent, faces, surface area and absolute volume,
checks a certified triangle-to-triangle surface gap and caps aggregate losses.
Candidates must be closed, consistently oriented, positive-volume and free of
degenerate faces. Unreferenced vertices, ambiguous numeric geometry or exhausted
fixed triangle-check/node-visit budgets preserve the complete raw mesh as display.
There is no smoothing, filling holes, simplification, recentering or rescaling.
Small detached features may be intentional; this policy cannot identify semantic
parts. `none` preserves every generated component in display as well as raw.

The worker first saves `raw_mesh.json`, `raw.glb` and `raw.obj` at the declared
normalized or explicit scale. It then writes separate `display_mesh.json`,
`display.glb`, `display.obj` and `cleanup-receipt.json`. The cleanup receipt uses
`modeling-display-cleanup/1`, records mode, immutable policy values/hash, component
statistics, sorted removed face indices, exact raw/display geometry hashes and
summary. Only whole components are removable. Every retained vertex coordinate,
face order and winding remains an exact subset of raw; compacted vertex indexing
is deterministic. Raw preservation is distinct from unchanged display.

`modeling_preview_render` takes its 64-hex `model_ref` and optional bounded
`width`, `height`, `fps`, `duration_seconds`, `pitch_degrees`, `start_yaw_degrees`.
Defaults are 640x640, 15 fps, 6 seconds. Both metadata/lock must declare
`model_preview:true`; cleanup modeling also declares
`display_cleanup:"bounded-floaters/1"`, while rendering declares
`preview_geometry_scope:"render_input"`. Missing/mismatched declarations never
acquire a capability from a newer composer. New modeling with an older renderer
exposes no preview operations and rejects direct calls before inference. Old
modeling with a new renderer retains the v1 uncleaned route. Inspect
`pipeline_capabilities.model_preview` and its cleanup options before use.

New raw-preserving results use the v1 task envelope with
`result_schema:"model-preview-result/2"`, `result_kind:"model-preview"`,
`status:"succeeded"`, exact `task_id`, one MP4 (`role:model_preview_video`) and
one ZIP (`role:data`, `result_kind:model-preview`). Pins have confined relative
`path`, actual `sha256`, `size_bytes`, and appropriate `media_type`.
`verification` requires `passed:true`, `numerical_passed:false`,
`simulation_performed:false`, `raw_geometry_preserved:true`, and an accurate
`display_geometry_modified` value, plus complete `video_decode` metadata.
Outer verification identifies `geometry_scope:"display_mesh"`; the renderer's
`geometry_scope:"render_input"`, `input_geometry_retained:true` and
`full_geometry_retained:true` mean every display input triangle was rendered,
not that no generated fragment was removed before rendering. Keep typed v1
compatibility for legacy uncleaned results; never pass an unknown typed version
through a physical-result fallback.

`provenance` binds exact toolbox/modeling module id/version/digest, runtime
configuration, model_ref, the current host-admitted image and source receipt,
modeling receipt, display mesh/GLB/OBJ, raw mesh/GLB/OBJ, cleanup receipt and
rendering receipt. Rehash exact sources and compare bytes, sizes and paths.
Do not trust role labels, a removal boolean or a producer-supplied hash alone.

The host independently recomputes the fixed policy using a trusted local verifier
with no heavy ML imports: reconstruct connected components from raw faces,
check whole-component eligibility, triangle surface-distance bounds and aggregate
budgets, derive the expected retained vertex/face mapping and compare the exact
display geometry and cleanup report. It must reject arbitrary face deletion,
modified retained coordinates/winding, insufficient gap, changed thresholds or
an incomplete/raw-missing proof. Module tree pins bind immutable implementation;
a user cannot loosen policy through task parameters. A host may maintain an
audited standard-library verifier; importing a producer's mutable script and
trusting its own result does not establish independent verification.

Independent v2 artifact checks also parse OBJ and embedded GLB geometry without
loading model packages or executing assets. OBJ coordinates and positive triangle
indices must match geometry JSON exactly. GLB positions must match the exact
float32 quantization, with bounded embedded accessor offsets/strides and unsigned
index types. Reject an empty or different mesh, extra primitives, transforms,
unreachable/repeated scene nodes, external resources, textures, animations,
skins, morphs and extension decoders. Valid file hashes cannot excuse geometry
that differs from the raw or display JSON it claims to represent.

The renderer proof binds exported display JSON, actual vertex/triangle counts,
canonical input/drawer index hashes, visible surface pixels per frame and no
physics. The host independently fully decodes the delivered video using fixed
local FFmpeg/FFprobe and checks codec, dimensions, duration and frame count;
`ftyp` or the producer's declared decode pass is insufficient. Camera fitting,
neutral shading and rotation provide presentation, not measurements or texture.

The ZIP has `archive-manifest.json`, `schema_version:"model-preview-data/2"`,
`result_kind:"model-preview"`, `simulation_performed:false`,
`numerical_usable:false`, and `{path,role,sha256,size_bytes}` for every member.
Keep required display GLB/OBJ/JSON, raw GLB/OBJ/JSON, modeling/cleanup/render
receipts, reference image and source receipt, provider notice and every full
license from the pinned modeling module. Match all model/source pins to outer
provenance. Stream full CRC/size/hash checks without extracting or executing.
Never omit raw, components or fields merely to satisfy archive delivery size.
Legacy v1 archives keep their uncleaned contract and member requirements.

Only the typed preview format additionally permits inert `.md`, `.png`, `.jpg`,
`.jpeg`, `.webp` source files alongside `.json`, `.txt`, `.glb`, `.obj`. Physical
ZIP rules remain unchanged. Reject symlinks, executable permissions, unsafe or
duplicate paths, encryption and external GLB resources. Keep independent caps
for compressed/expanded bytes, members and media. Release held references,
video and archive together only after every check, recheck before sending and
track independent receipts. Missing or unknown receipts are never replayed;
local capture API acceptance does not prove platform delivery.

Per-model/render references keep separately sealed manifests. Cache reuse
rechecks all raw/display sources, cleanup/source receipts, artifact pins and the
fixed public response before atomically republishing the selected task manifest.
A preview is neither a physical experiment nor simulation readiness. Existing
physical preparation, topology, solver and numerical-quality gates remain.
