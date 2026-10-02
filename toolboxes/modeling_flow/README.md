# Local image-to-shape backend

This package adds offline single-image shape generation to the modeling skill.
It calls pretrained Hunyuan3D-2mini flow matching inference; it does not train a
new generative model or infer exact geometry, dimensions or materials from a
photograph. Language models choose the typed operation and explain assumptions.
The local numerical model generates the geometry.

The preferred Apple Silicon route is the independent MLX port from
[Hunyuan3D-Swift](https://github.com/ZimengXiong/Hunyuan3D-Swift). Its Python shape
pipeline loads the original Tencent `config.yaml` and
`model.fp16.safetensors` without weight conversion. The optional PyTorch route
calls the official
[Hunyuan3DDiTFlowMatchingPipeline](https://github.com/Tencent-Hunyuan/Hunyuan3D-2/blob/main/hy3dgen/shapegen/pipelines.py)
on MPS. CPU inference is available only through an explicit operator PyTorch
configuration. There is no automatic backend, dtype, geometry or device fallback.

## Architecture and ownership

```text
QQ trusted attachment + purpose (model preview or physical simulation)
  -> host writes bounded request / selects pinned local runtime
  -> modeling_flow CLI (dedicated worker process)
     contracts.py: JSON schemas, paths, limits, content hashes
     runner.py: verified Git/checkpoint -> offline backend -> immutable assets
     meshes.py: triangle import -> uniform declared metre/normalized model-unit scale -> audit
     reduction.py: bounded numerical-component cleanup -> topology-preserving QEM
                   -> independent second audit; original display stays intact
  -> original display.glb + display.obj + display_mesh.json + receipt.json
  -> preview: original mesh -> turntable + original-model/reference/license ZIP
  -> physics: explicit dimensions/material -> simulation.obj + simulation_mesh.json -> scene audit
  -> host validates artifact hashes and physics stage validates its scene
```

This backend is an internal modeling worker rather than a separate QQ transport
or a fourth independent physics pipeline. Modeling, simulation and rendering
keep their existing stage boundaries. Simulation receives only the bounded
simulation asset. The QQ adapter runs the existing engine mesh audit (including
its intersection checks), creates a canonical `mesh_ref` and requires the entire
authored scene to pass `physics_prepare` before simulation. A worker mesh pass
does not mean the scene is ready. Rendering may receive the detailed display
asset. QQ installs
and selects runtimes through operator configuration; agent requests cannot
choose an interpreter, source checkout, model path, executable, shell command or
network endpoint. The CLI supports local developer use with explicit paths, but
those paths are not agent-controlled QQ operation parameters.

Importing `modeling_flow` is dependency-free. Heavy libraries and weights are
loaded only in an explicitly invoked worker. Upstream and weights live outside
the packaged QQ skill; checkpoints are too large to include in the Git repository
or hot-plug bundle. Pin the runtime's source commit and checkpoint inventory in
the local config. Runtime source changes, missing dependencies, unavailable GPU,
missing tensors and digest mismatches produce explicit failures.

## Operator installation and offline operation

Acquire upstream code, dependencies and the two weight files during an explicit
installation step. Review the Tencent checkpoint license before installing or
redistributing weights. Publicly available weights are not equivalent to an
unrestricted license. This repository contains our adapter, no Tencent model
weights or copied model implementation.

The example pins the MLX source to
`292331f4d26ddb80b9dcea6bcb5629ff82f12b82` and the official
[Hunyuan3D-2mini checkpoint](https://huggingface.co/tencent/Hunyuan3D-2mini/tree/f90a0f7df7d5e6f71109cf333f6a95a0ae3194a6/hunyuan3d-dit-v2-mini)
to `f90a0f7df7d5e6f71109cf333f6a95a0ae3194a6`. Fill in real absolute paths and
SHA-256 values in [config.example.json](config.example.json). The source path for
MLX is the repository's `python/shape` directory, with `hy3dmlx` below it. The
source path for PyTorch is the official repository root, with `hy3dgen` below it.
Its audited source commit is `f8db63096c8282cb27354314d896feba5ba6ff8a`.

The MLX port declares Python >=3.12 and MLX >=0.31.2. Shape inference needs MLX,
NumPy, OpenCV, Pillow, PyYAML, safetensors, scikit-image and trimesh. Install
`pymeshlab==2023.12.post3` for topology-preserving simulation reduction. Its
[official filters](https://pymeshlab.readthedocs.io/en/latest/filter_list.html#meshing-decimation-quadric-edge-collapse)
describe the QEM topology/normal/boundary constraints and original-vertex placement.
The package is GPL-3.0; the installer and exported modeling data retain its full
license, and dependency versions remain in each receipt. PyTorch additionally needs its
upstream shape dependencies, including torchvision, diffusers, transformers,
einops and pymeshlab. Dependency versions are recorded in each receipt; their
presence alone does not establish a tested inference environment.

On a 16 GB Mac, explicitly choose MLX `dtype: float16` first. The checkpoint is
about 3.82 GB in fp16, and the image conditioner is substantial. Float32,
sampling activations and loading copies can increase memory far beyond the
checkpoint file size. No speed, quality or memory guarantee is inferred from a
successful import. `health` verifies installation, pins and GPU availability,
and reports `inference_verified: false`; a real image inference establishes the
hardware smoke test.

```sh
python -m toolboxes.modeling_flow health --config /absolute/local-config.json
python -m toolboxes.modeling_flow generate --config /absolute/local-config.json \
  --request /absolute/request.json --output /absolute/new-run
```

The same package works as `python -m modeling_flow` when `toolboxes` is on the
trusted `PYTHONPATH`. The CLI emits one JSON object to stdout and diagnostics to
stderr. Exit status is 0 on success and 2 on a modeled failure. The host must
enforce the task timeout and terminate the dedicated process; the adapter cannot
reliably preempt a running Metal kernel from inside Python.

For a reproducible operator install use the repository's
`toolboxes/install_modeling_flow.py` with required `--output` (a dedicated new
absolute directory), `--python` (an explicit Apple Silicon Python >=3.12) and
`--provider-name` (the actual local service operator). It clones the fixed MLX
source, installs the exact minimal dependency closure in
[requirements-mlx.lock.txt](requirements-mlx.lock.txt), streams only the fixed
checkpoint files with exact size/SHA-256 verification, preserves full license
copies and writes `host-config.fragment.json`. Review that fragment and merge its
`modeling_runtime` into the QQ host's local config. Installation failure retains
its directory, log, state and partial download; `--resume` requires the same
identity and `--check-only` runs health offline. Neither command performs a real
image inference. The default `--foreground-model u2net` also installs the pinned
175997641-byte foreground graph, the exact CPU ONNX Runtime dependency closure
in [requirements-foreground.lock.txt](requirements-foreground.lock.txt), and full
U2-Net Apache-2.0 / rembg MIT license copies. Select `--foreground-model none`
for supplied-alpha modeling only. Full license texts and `NOTICE.txt` are retained
under the runtime so the host can include them in portable output bundles. QQ
never invokes this installer.

At runtime Hugging Face and Transformers are offline, telemetry is disabled,
and MPS CPU fallback is disabled. Source/checkpoint paths are local and pinned;
there is no download, pip installation, prompt shell execution, texture model,
FlashVDM custom kernel, quantization or compiled denoiser in this adapter. MLX
sets the default computation device to Metal. CPU image decoding and marching
cubes are explicit preprocessing/postprocessing, recorded in the receipt.

## Input and assets

The physical `modeling-flow-request/1` requires a still PNG/JPEG/WebP attachment,
a physical extent in metres and declared material name/density. The separate
`modeling-flow-preview-request/1` requires only the image. It accepts an optional
explicit extent, forbids material and decimation, and otherwise normalizes the
maximum extent to one `model_unit`; no physical dimensions are inferred. Its
receipt records `purpose: model_preview`, no simulation, and null material/mass.
The QQ route is `modeling_preview_from_image` → `modeling_preview_render` →
`qq_video`. In the 0.3.2 source preview, `display_cleanup` still defaults to `conservative`;
`none` keeps all components in the display. Both retain untouched normalized
raw GLB/OBJ/JSON plus display assets and a fixed-policy cleanup receipt. Only
whole tiny disconnected components meeting every `bounded-floaters/1` bound
can be removed; retained coordinates/winding are exact, without smoothing,
filling holes, decimation, recentering or rescaling. This is reversible display
processing, with no semantic fidelity guarantee. Simulation topology
gates apply only to the physical route. `configuration_file_sha256` binds the
receipt to the exact runtime snapshot; `configuration_sha256` records the
validated configuration with defaults. Prefer an isolated object with a meaningful
alpha foreground. The default `background_removal: none` uses the whole opaque
image and the receipt says `foreground_mask: full_image` and
`background_removal: not_performed`. An operator may explicitly enable
`background_removal: u2net` after installing a pinned standalone
`weights/segmentation/u2net.onnx` graph and CPU ONNX Runtime. Configure
`foreground_model` with relative path `segmentation/u2net.onnx` and its SHA-256.
[segmentation.py](segmentation.py) applies a local estimated salient-object mask
to opaque photographs before shape conditioning; supplied alpha is preserved.
The mask does not identify object boundaries or fine edges with measured
accuracy. Missing/incompatible graphs, dependencies and invalid masks fail
explicitly. No segmentation graph is downloaded at inference time. The image
is decoded once with a 32 MiB / 16 megapixel budget and normalized EXIF
orientation. A fully transparent foreground is rejected.

The optional `display_cleanup: surface` requires the modeling module's explicit
`surface_cleanup: bounded-surface/1` capability. Its fixed geometric policy can
remove eligible whole detached open/flat micro-components and smooth eligible
vertices under displacement, triangle-orientation and area limits. Boundary,
nonmanifold, sharp and thin-feature neighborhoods are protected; some pointed
noise can therefore remain. It does not identify the back side, recover hidden
surfaces, delete retained connected patches, fill holes or generate images.
Read the actual policy, removal/movement/protection statistics and maximum/RMS
displacement in the cleanup receipt. An operation-budget no-op keeps the source
and must be described as unprocessed. Raw normalized GLB/OBJ/JSON remain intact.
Surface's `model-preview-result/3` and `model-preview-data/3` require independent
exact host recomputation of mapping, removals and bounded coordinates; a v2
unchanged-coordinate submesh proof or matching hashes cannot authorize smoothing.

When the current task already holds a sealed `model_ref` and the pinned
capabilities advertise `modeling_preview_cleanup`, that operation can derive a
new display/model reference from raw using CPU geometry/export without loading
neural weights. Use its returned reference for rendering and delivery; it does
not render or send by itself. Source files and pins remain unchanged. Arbitrary
paths, cross-task references and automatic pin promotion are unsupported. This
separate cleanup operation defaults to surface; initial image preview remains conservative.

`scale_axis` is `max` by default or `x`, `y`, `z` in the **generated mesh's**
coordinate frame. Uniform scaling makes that extent equal the user-supplied
number; this is not a camera calibration. Origin becomes the generated bounding
box center. The shape's orientation is preserved. Never claim the model's axes
are automatically aligned to the photograph or gravity.

`display.glb`, `display.obj` and inert `display_mesh.json` preserve the generated shape at its declared
physical scale. They are shape-only assets. `simulation.obj` and
`simulation_mesh.json` contain at most 4096 vertices / 8192 triangles. Quadric
decimation is allowed by the typed request and disclosed with its uncertified
geometry error. It never silently removes disconnected components, fills holes,
substitutes a primitive or extrudes an image silhouette to satisfy the request.
Global face reversal is allowed only for a closed, consistently oriented,
single-component mesh with negative signed volume, and is recorded.
Simulation-only cleanup may remove a numerical micro-component only when one
surface accounts for at least 99% of total area, the fragment stays inside that
surface's bounds, and all extent/area/absolute-volume thresholds pass. Individual
limits are 0.001 / 1e-6 / 1e-9 of the corresponding dominant/total measures;
aggregate area/volume losses stay below 1e-5 / 1e-8. Thin large parts, cavities,
separate objects and outside fragments remain and can cause a genuine rejection.
The receipt lists every removed fragment and threshold; display geometry is preserved.
Disabling decimation also disables this cleanup.

Reduction tries face budgets 6000, 8188 and 4000 independently from the admitted
simulation source, using PyMeshLab topology/normal/boundary preservation,
original-vertex placement and no automatic repair. Each candidate must preserve
the source Euler characteristic as well as closure and winding, accepting the
first independently passing candidate. Each
candidate is uniformly rescaled to retain the explicitly requested physical
extent; attempted topology audits and additional transforms are in the receipt.
If none passes, the simulation mesh is rejected. The pipeline then applies its
unchanged engine intersection/work-budget gate; a decimator flag is not proof of
intersection freedom. A candidate failing that final gate remains a failure.

Physics eligibility requires bounded finite coordinates, distinct triangle
indices, no duplicate/degenerate faces or unused vertices, one connected
component, zero boundary/nonmanifold/inconsistently oriented edges and nonzero
volume. Checks run after reduction too. This does **not** certify absence of
self-intersections, image fidelity, CAD tolerances, wall thickness or material
properties. Simulations of reconstructed objects are approximations with these
assumptions; quantitative physical accuracy requires separate measurements.

The bounded OBJ importer accepts triangle `v`/`f` statements with positive or
relative negative indices. It never opens `mtllib` files. Polygons, curves,
external includes and homogeneous coordinates are rejected rather than silently
converted. Existing physics scenes retain their own engine validation.

## Receipt and failures

`receipt.json` pins source Git commit, official checkpoint revision and file
hashes, image hash, request/configuration hashes, seed, sampling settings,
backend/device/dtype, package versions, scale/material assumptions, audits,
reduction and output hashes. A seed belongs to its backend and environment;
cross-backend bitwise equality is not promised. The receipt lists output assets;
the returned result separately hashes the receipt to avoid a circular digest.

Inference failure returns no claimed geometry. A successful shape with an
ineligible simulation mesh preserves the display assets and receipt, returns
`ok: false`, `ready_to_simulate: false`, an actionable rejection code and the
display paths. QQ may offer a visual preview but must not automatically simulate
that rejected mesh. The physics mesh exists only after its audit passes. Each
run uses a new or empty output directory and creates immutable artifacts.

Tests exercise contracts, unsafe/degenerate mesh rejection, scaling, provenance,
display/physics separation and explicit failure paths without downloading
weights or importing a GPU framework. A fake inference fixture verifies adapter
behavior; it does not count as a real flow matching quality or hardware test.
