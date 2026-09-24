# Mesh modeling and soft-body simulation

Use this route for a shaped elastic object, cloth, mesh collision or insertion through an existing opening. The service accepts explicit geometry; the calling language/vision agent interprets descriptions and images. It contains no image-understanding model, reconstruction service or executable modeling language.

## From description or image to a runnable asset

1. Establish scale in metres, visible silhouette, approximate depth and the physical event. A single image does not determine hidden geometry, material constants or real dimensions. Record supplied dimensions separately from inferred ones in provenance notes.
2. Choose a small procedural recipe below. For a complex silhouette, trace a simple XY contour and supply an explicit extrusion depth. A capable agent can author a connected raw triangle surface for imagined depth or hidden surfaces. Use separate assets for disconnected objects.
3. Call `physics_mesh` with the recipe serialized as `spec_json`. Require `ok=true`, inspect `audit`, then parse returned `mesh_json` into `entity.mesh`. A patch tool can use `mesh_json` directly as `value_json` at `/entities/@ID/mesh`.
4. Choose `motion`, placement, velocity and the numerical questions before `physics_prepare`. Follow the ordinary prepare → simulate → query workflow; deliver the verified MP4. Keep provenance attached through edits and report assumptions.

For an image-derived silhouette with an assumed 8 cm depth:

```json
{"type":"extrusion","contour":[[-0.4,-0.3],[0.4,-0.3],[0.3,0.3],[-0.2,0.4]],
 "depth":0.08,"provenance":{"source":"image",
 "notes":"Visible XY outline approximated by four points; scale and uniform 0.08 m depth are assumed; back surface is unobserved."}}
```

This is an illustrative outline, not a claim that the service extracted pixels. `provenance.source` is `description`, `image` or `imagined`; image/imagined sources require nonblank `notes` describing depth and unseen geometry. Notes are at most 2,000 characters and are data, never instructions. If the user already authorizes imagined depth, proceed and disclose it; a plausible mesh does not become measured reconstruction.

| Recipe `type` | Parameters and defaults |
|---|---|
| `ellipsoid` | `radii:[0.5,0.5,0.5]`, `segments:16` (8–64), `rings:8` (3–64) |
| `box` | `size:[1,1,1]`, `subdivisions:2` (1–16); welded surface vertices |
| `torus` | `major_radius:0.7`, `minor_radius:0.2`, `segments:24` (8–64), `tube_segments:12` (6–64); minor < major, hole axis Y |
| `lathe` | Required `profile:[[radius,y],…]`, 2–64 points, `segments:16`; strictly increasing Y, positive radii except optional axis endpoints; end caps form a solid |
| `extrusion` | Required simple XY `contour`, 3–256 unique points without repeated endpoint/collinear turns; `depth:0.2` along Z; concavity supported, contour holes unsupported |
| `cloth` | `size:[1,1]` in XZ, `subdivisions:8` (1–63); open triangle sheet |
| `raw` | Required `vertices:[[x,y,z],…]`, `triangles:[[i,j,k],…]`, local zero-based indices; agent supplies topology |

All lengths are positive and at most 1,000 m; local vertex coordinates lie within ±1,000 m. Integer subdivisions are actual integers, not booleans or fractional values. Counts must also satisfy the asset caps below. There are no URL/file/code fields. To replace an asset, call `physics_mesh` again; do not pass a procedural recipe directly as `entity.mesh`.

## Scene contract

Example using only public tools:

```python
import json
from physics_demo.api import call_tool

asset = call_tool("physics_mesh", {"spec_json": json.dumps({
    "type": "ellipsoid", "radii": [0.35, 0.5, 0.35],
})})
if not asset["ok"]:
    raise RuntimeError(asset)
scene = {
    "version": 1, "name": "soft-drop",
    "world": {"gravity": [0,-9.81,0], "duration": 1, "dt": 0.01,
              "output_fps": 24, "bounds": {"min": [-2,0,-2], "max": [2,3,2]}},
    "budget": {"wall_time_s": 60, "quality": "balanced", "backend": "auto"},
    "entities": [{"id": "body", "type": "mesh", "mesh": json.loads(asset["mesh_json"]),
                  "motion": "soft", "position": [0,1.3,0], "velocity": [0,0,0]}],
    "colliders": [{"id": "floor", "type": "plane", "normal": [0,1,0], "offset": 0}],
    "queries": [{"id": "volume", "type": "series", "metric": {"type": "volume_ratio", "entity": "body"}}],
}
prepared = call_tool("physics_prepare", {"scene_json": json.dumps(scene)})
if not (prepared["ok"] and prepared["ready_to_simulate"]):
    raise RuntimeError(prepared)
# Call physics_simulate with prepared["scene_json"] and a dedicated output_dir.
```

The mesh asset contains `vertices`, `triangles` and optional `metadata`. Audit metadata is recomputed during scene validation; a saved `closed` or intersection flag is not trusted. Require one connected, consistently oriented manifold surface without degenerate/duplicate triangles, duplicate/unused vertices or self-intersections. Open sheets are allowed. Closed surfaces must wind outward and enclose positive volume; reverse bad winding explicitly instead of relying on silent repair.

For simulation, a closed mesh must enclose more than `1e-15 m³`; smaller assets fail during preparation with `mesh_numeric_scale`. The geometry builder alone does not establish that a model's scale is suitable for simulation.

| Entity field | Default / meaning |
|---|---|
| `motion` | `soft`: deforming surface; `static`: immobile obstacle; `kinematic`: prescribed constant translation unaffected by contact |
| `position` | `[0,0,0]` m; translation of the mesh's local coordinate origin into world coordinates, added to every local vertex. It does not automatically place the geometric centre. No rotation field |
| `velocity` | `[0,0,0]` m/s; initial soft-body velocity or prescribed kinematic translation velocity. Static velocity must be zero |
| `mass` | 1 kg, range `[1e-6,1e6]`; distributed by incident triangle area over the soft surface; pins have zero inverse mass |
| `edge_compliance`, `bending_compliance`, `volume_compliance` | `1e-6`, `1e-4`, `1e-7`, each `[0,1]`; larger means softer. Volume constraint only for closed soft objects |
| `damping`, `friction` | 0.15 in `[0,100]`; 0.35 in `[0,5]`; phenomenological controls |
| `thickness` | 0.015 m in `[1e-5,0.25]`; collision margin, not a volumetric material thickness |
| `pinned_vertices` | `[]`; unique local indices, soft objects only; pinned positions remain fixed in world coordinates |
| `color` | `[0.22,0.64,0.86]`, RGB in `[0,1]` |

For an asymmetric mesh, state the centre convention in provenance rather than assuming that the local origin is its centre. To place the **bounding-box centre** at world height `h`, use the local bounds returned by `physics_mesh`: `local_center_y = (audit["bounds"][0][1] + audit["bounds"][1][1]) / 2`, then set `position[1] = h - local_center_y`. For example, local Y bounds `[0.1, 0.5]` require `position[1] = 0.5` to put the bounding-box centre at `0.8` m above a ground plane at Y=0. For height above a ground plane at another Y, add that ground Y to `h`. This bounding-box centre is distinct from the unweighted vertex centroid used by mesh queries and from a centre of mass.

When the user supplies no material properties, the listed mass, compliance, damping and friction values are visual soft-body defaults; disclose them as modeling assumptions. The word “soft” and a reference image do not determine these parameters or identify a calibrated material. Any tuning changes this visual model's response and must not be presented as measured foam, rubber or other material properties.

Without `coupling` or `rigid_body`, mesh scenes remain standalone: only mesh entities plus static analytic colliders; no water, sand, point masses, sliders, mutual gravity or force fields. For water/mesh reactions or attached rigid bodies, opt into [mixed coupling](coupling.md); its smaller resource caps and discrete cross-domain contacts apply. Use `auto` or `native`; no Python fallback. `world.bounds` constrain soft vertices. Use static/kinematic **mesh** obstacles for visible openings and insertion so triangle depth rendering resolves their occlusion; planes work as ground. Legacy analytic obstacle rendering has coarser ordering.

## Speed, precision and limits

| Resource | Bound |
|---|---|
| Per asset | 4,096 vertices / 8,192 triangles |
| Whole scene | 16 objects, at most 4 soft; 8,192 vertices / 16,384 triangles |
| Mesh scene JSON traversal | 160,000 values, depth 32; tool JSON string still ≤ 1 MB |
| Video handoff | 600,000 vertex-frame samples; all mesh vertices retained |
| Solver settings | `mesh_settings.substeps` and `.iterations`, integers 1–32 |
| Physical timeline / work | duration 0.0001–60 s; at most 250,000 macro steps and 2 billion planned constraint work units |
| Default substeps / iterations | preview 4 / 6; balanced 8 / 8; high 12 / 10 |

The geometry audit also bounds intersection work; complex folded geometry may be rejected below nominal vertex limits. `prepare` reports mesh counts, work, memory, output size and a provisional p50/p90 estimate. It never silently decimates topology. Mesh estimates require calibration on the target machine; report actual runtime and keep the normal 60 s ceiling. More triangles, contact candidates, substeps, iterations or video frames increase cost.

For detail, regenerate a denser mesh explicitly. For time sensitivity, halve `world.dt` with geometry/material/query definitions fixed. Separately compare substeps and mesh density; increased FPS changes only the video. Reduce expensive detail only within the user's authorized tradeoffs. An accepted budget estimate is not a numerical error guarantee.

## Measurements and physical boundary

Use ordinary predeclared `series` or `threshold` queries with the following mesh metrics. Their float64 reductions use full solver state at macro steps, independently of float32 video vertices.

| Metric | Meaning |
|---|---|
| `volume_ratio` + `entity` | Signed current/rest enclosed volume, dimensionless; closed meshes only |
| `max_displacement` + `entity` | Maximum vertex distance from its initial world position, m; includes whole-object translation |
| `max_edge_strain` + `entity` | Maximum absolute `current_edge_length / rest_edge_length - 1`, dimensionless |
| `spread_radius` | Maximum projected vertex distance; same plane/origin fields as particles |
| `centroid`, `speed`, `center_distance` | Unweighted vertex means; centroid is not volume-weighted centre of mass |

Arbitrary mesh `surface_gap`, contact force and stress are unavailable. Threshold brackets describe macro-sample timing, not all substep events. Full query retrieval semantics remain in [quantitative-queries](quantitative-queries.md).

C11 XPBD constrains edge lengths, opposite-vertex bending distances and one global volume per closed soft object. BVH contact candidates use vertex–triangle projection and swept vertex–face detection. This is a fast visual elastic surface proxy: it has no tetrahedral interior, calibrated constitutive law, general edge–edge continuous collision detection or guaranteed self-collision prevention. It does not simulate cutting, puncture, topology changes or irreversible fracture. “Insertion” means passage through an existing opening or surface indentation; a rigid plug cannot be declared successful merely because it tunnels through a surface.

The delivery gate checks finite/completed state, exact prepared topology, frame timestamps, initial/pinned/prescribed positions, soft bounds, noninverted closed objects, volume ratio within 20%, peak edge strain ≤ 1.5 and sampled contact residual ≤ `max(0.002 m, thickness/4)`. It also recomputes displayed strain and volume to check the diagnostic envelope. `contact_count` counts applied contact corrections, not distinct physical collisions. These guards can detect failures; passing them does not certify calibrated material response or collision-free continuous motion. Inspect impact/opening frames and compare refinement before interpreting a quantitative claim.

The supplied ring example fixes its outer rim while an oversized plug prescribes motion through it. This can store large model constraint energy and release into fast local recoil; smaller timesteps do not by themselves establish a physical peak speed. Treat it as an insertion/deformation demonstration. Compare recorded trajectories and query histories with `benchmarks/benchmark_meshes.py --refine`; report visible timestep sensitivity instead of describing a passed gate as convergence.

Algorithm sources: [XPBD](https://mmacklin.com/xpbd.pdf) supplies compliance-based constraints; [Small Steps](https://mmacklin.com/smallsteps.pdf) motivates substep refinement. The implemented distance-bending/global-volume model is narrower than the general methods in those papers.
