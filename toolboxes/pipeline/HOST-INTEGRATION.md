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
