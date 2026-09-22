# Toolbox protocol v1

The transport and the simulation implementation are independent. The host owns admission, queueing,
resource enforcement and delivery. A toolbox owns request interpretation, simulation, rendering and
domain validation. The current contract accepts text and delivers one MP4; attachments and multiple
output files require a future contract extension.

## Package manifest

Place `toolbox.json` at the package root:

```json
{"schema_version":1,"id":"physics","version":"1.0.0","entrypoint":"toolbox_adapter.py",
 "capabilities":["List tasks actually supported"],"limitations":["State precision and scale limits"]}
```

Bundle the relative Python entrypoint and its dependencies. Symlinks and special files are rejected.
The host must trust the package before publishing it; request text is always untrusted data.

## Input

Invoke the entrypoint with a fixed argument vector, never a shell command assembled from the prompt:

```text
python PACKAGE/ENTRYPOINT --phase probe|run --task JOB/task.json
```

The host creates JOB/artifacts and JOB/work and writes:

```json
{"schema_version":1,"task_id":"host-generated-id",
 "request":{"text":"User request","trust":"untrusted_external_input"},
 "limits":{"wall_time_seconds":30,"max_output_bytes":16777216,"network":false},
 "output_dir":"artifacts","work_dir":"work"}
```

Paths are relative to the task JSON's directory. Do not put account credentials or unrelated user
data in the job. The host should permit reads of the package and job, writes only to the job, and no
network. The standalone smoke executor supplies these restrictions on macOS.

## Capability and progress

`probe` must finish within five seconds and atomically write JOB/capability.json:

```json
{"schema_version":1,"supported":true,"estimated_seconds":25}
```

Return supported:false with a reason when the request or budget is unsupported. A supported request
needs a positive finite estimate within the supplied wall-time budget; transport and queue time are
not included. The host must check support before acknowledging that simulation has started.

During `run`, the module can atomically update JOB/progress.json:

```json
{"schema_version":1,"state":"running","fraction":0.5,"updated_at":1789900000}
```

Progress is for host queries. The transport chooses how much to show to the user.

## Result

After domain checks and full video decoding pass, write JOB/artifacts/result-manifest.json and exit 0:

```json
{"schema_version":1,"status":"succeeded","summary":"Verified concise result",
 "verification":{"passed":true,"checks":["domain_quality","video_decode"]},
 "outputs":[{"path":"simulation.mp4","media_type":"video/mp4","sha256":"actual SHA-256"}]}
```

Output paths are relative to artifacts; absolute paths, escapes and symlinks are forbidden. Hosts
must validate the schema, status, verification, size, MP4 header and SHA-256 before delivery. The module
is responsible for domain correctness and decoding; host file checks do not independently prove
physical correctness. Failure may report status:failed or status:unsupported; it must never publish
success with an unverified file. Stdout/stderr are diagnostics, not the output protocol.

## Version identity

`registry.py publish` copies the package to `versions/<digest>` and optionally replaces active.json
atomically. The pointer contains schema_version, digest, id and version. The digest covers sorted
relative filenames, executable bits and SHA-256 of file bytes. It excludes build caches during copying.

The host persists the registry and pointer with each newly accepted task. Later activation must not
change that task's version, even on retry or restart. Keep pinned snapshots until their jobs expire.
Changing the module does not require changing or redeploying the transport.

## Delivery byte budget

The physics module enforces task.limits.max_output_bytes before publishing success.
If a verified video is too large, it renders the saved frames with budgeted JPEG compression,
keeping every frame, FPS and playback duration; 960/720/480 px widths are tried in order.
Physics and canonical result files are never rerun or modified for this operation.
The shared task deadline includes compression. Result verification.delivery records the bytes,
resolution and whether presentation compression was needed. A compression failure returns
stage=presentation, retryable=false; do not change a requested duration or label it unsupported physics.
