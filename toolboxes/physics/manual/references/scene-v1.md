# Scene v1 authoring reference

Use SI units, a right-handed Y-up coordinate system, and [scene-v1.schema.json](../../scene-v1.schema.json). Runtime validation adds semantic, coupling and planning checks beyond JSON Schema. Start from a complete example rather than assuming the skeleton below is runnable.

```json
{
  "version": 1, "name": "drop-on-sphere",
  "world": {
    "gravity": [0,-9.81,0], "duration": 1.5, "dt": 0.0111111111,
    "output_fps": 24, "bounds": {"min":[-2,0,-2],"max":[2,3,2]}
  },
  "budget": {"wall_time_s":55,"quality":"balanced","backend":"auto"},
  "entities": [], "colliders": [], "force_fields": [], "queries": [],
  "interactions": {}
}
```

`output_fps` accepts integer-valued numbers 1–120 (`24.0` is accepted; `24.5` is not). It controls presentation timestamps, always including initial and terminal states. It never changes `world.dt` or observation sampling. Camera and lighting are currently renderer defaults, not scene parameters.

## Entities and defaults

| Type | Fields | Meaning |
|---|---|---|
| `point_mass` | `id`, positive `mass`, `position`, `velocity`; optional `fixed=false`, coupled-only `collision_radius=0` | N-body/connection point, or mixed contact sphere; always independent of world gravity |
| `fluid` | `id`, `shape`, `spacing`, `velocity`, optional `preset`, `properties` | One liquid phase; preset defaults to water and supplies model coefficients/appearance |
| `granular` | Same geometry as fluid; `properties` | Sand; friction defaults 0.55, cohesion 0.18 |
| `rigid` | `id`, `position`, `velocity`, `mass`, sphere/box `shape` | Zero mass is static; dynamic sphere is Python-only; positive-mass box rejected |
| `slider` | `id`, top-level `position`, `velocity`, `size`, `mass`, `acceleration`, `friction`, `restitution` | Separate common-X-rail scene; full restrictions/defaults in [quantitative-queries](quantitative-queries.md) |
| `mesh` | `id`, audited `mesh`, `motion`, position/velocity, compliance, pins, color | Native triangle surface; standalone or explicitly mixed; [mesh-modeling](mesh-modeling.md) |
| `rigid_body` | `id`, sphere/box/cylinder `shape`, COM position/velocity, mass, wxyz orientation, world angular_velocity, fixed/pivot | Native rotating uniform body; selects [coupling](coupling.md); cylinder axis local Y |

Particle shapes are `{"type":"sphere","center":[x,y,z],"radius":r}` or `{"type":"box","center":[x,y,z],"size":[sx,sy,sz]}`. Fluid `preset` is `water`, `honey`, `glue`, `lava`, or `molten_lead`; call `physics_liquid` for newly authored or changed liquid material and read [liquid-presets](liquid-presets.md) instead of guessing coefficients. Preserve explicit properties already present in a matching example. Omitted properties come from the preset; explicit properties override them and must be disclosed. Multiple fluid regions share one phase. A water ball has no membrane. Material controls are visual parameters, not measured soil or liquid constants.

Normalized scene exposes all effective defaults; its assumptions are deterministically derived from the full normalized values, so prepare→simulate does not lose assumptions. Interactions are `mutual_gravity`, `gravity_G`, `softening`, `water_sand_drag` (default 0.16), and `wetting_rate` (default 1.8). Particle self-gravity also accepts `particle_gravity_density` and `gravity_theta`; see [particle-gravity](particle-gravity.md) for mass, accuracy and route constraints.

Particle spacing is 0.0001–0.5 m. Sub-millimetre spacing is intended for physically small scenes such as the canonical 6 mm drop, not for refining metre-scale volumes: halving spacing in 3D produces about eight times the particles and usually at least sixteen times the work after transport/capillary refinement. Native fluid/granular volumes use one spacing, initially the finest requested and then commonly coarsened if needed. Check `effective_spacing`, `requested_particles` and `uniform_spacing_particles_before_cap`; input entity spacings do not remain separate resolutions.

Prepare checks actual post-coarsening lattice support plus particle radius against bounds, not only the shape AABB. A raw shape extending slightly outside may still have accepted discrete support; use `initial_bounds_feasible` and `initial_bounds_violations`. Bounds span feasibility is separate. More than eight particle volumes overlapping at a volume centre are rejected before neighbour allocation.

Connections are scene-level links, not entity types. Standalone point-mass fields and `connection_settings` are defined in [connections](connections.md). `coupling:{}` or a `rigid_body` selects the mixed route: links can instead supply `endpoints` for point centres, mesh vertices or rigid local points and optional straight-capsule `solid`. Read [coupling](coupling.md) for the single endpoint convention, gravity semantics, stiffness controls, limits and angular metrics. The mixed route uses `coupling` settings, not `connection_settings`.

## Colliders

| Type | Geometry |
|---|---|
| `plane` | `normal`, `offset` |
| `sphere` | `center`, `radius` |
| `box` | `center`, `size`; optional `appearance: "solid"` or `"glass"`, default solid |
| `capsule` | Distinct endpoints `a`, `b` and `radius`; rounded ends |

Optional collider `friction` is bounded position-level Coulomb friction. A plane may also set `water_adhesion` in m/s², from 0 to 250. It smoothly attracts water particles within one kernel radius and, together with contact friction, provides a bounded visual wetting/pinning control. It is not a calibrated static or dynamic contact angle, hysteresis law, or solid surface-energy measurement. Box appearance only changes rendering, never geometry, friction, diagnostics or trajectory; glass is translucent styling without refraction/caustics.

Native explicit planes add analytic DFSPH solid-half-space density/pressure support. Plane `water_adhesion` is evaluated by both native and Python particle routes, although their pressure and surface-tension solvers are not equivalent. Sphere/box/capsule/world bounds do not provide either feature. Boxes use swept old-to-candidate segment contact against a radius-expanded AABB; sphere, capsule and bounds use discrete endpoint projection. Do not infer general continuous collision detection.

Native `world.bounds` are six closed invisible particle walls, not camera-only metadata. Use explicit plane/box walls for visible cups/tanks/channels, leave bounds several spacings outside, and keep an explicit floor plane when DFSPH planar wall support is needed.

## Force fields

Fields have `id`, `type`, `targets` and optional `start_time`/`end_time` within duration. Targets name existing fluid/granular/point_mass IDs; rigid targets and executable expressions are rejected. Fields add on the half-open active interval `[start_time,end_time)`, subject to the combined acceleration limit.

| Type | Parameters |
|---|---|
| `uniform` | `acceleration:[ax,ay,az]`, m/s² |
| `radial` | `center`, signed `strength`, `radius`; positive repels, negative attracts, linear falloff |
| `vortex` | `center`, nonzero `axis`, signed tangential `strength`, `radius`, optional nonnegative `inward_strength`; linear falloff |

These are prescribed accelerations, not simulated wind, pumps, explosions or electromagnetic fields. Integration splits at start/end boundaries within a timestep-scaled floating-point tolerance, so a short pulse acts only for its real overlap. `steps` counts macro steps; `substeps` includes force-window segments and CFL subdivisions.

## Queries, backend and patches

Declare `queries` before prepare. Types are `series`, `threshold`, `nbody_stability`; each has a unique ID. Read [quantitative-queries](quantitative-queries.md) for complete metric/slider contracts and examples. No arbitrary post-run expressions are accepted.

Use `budget.backend: "auto"` normally; alternatives are `native` and `python`. Honey, glue and molten-lead presets require native and reject explicit Python rather than silently changing their model. Backend and algorithm differences are in [solver-routing](solver-routing.md); budget/type limits in [capability-boundaries](capability-boundaries.md). Check prepare feasibility after any patch, including heights and sizes.

`physics_patch` supports atomic `add`, `replace`, `remove` operations. Tool values are JSON-serialized strings: `"value_json":"2.0"`, `"value_json":"[0,2,0]"`, or `"value_json":"null"` for remove. CLI patch files use ordinary `value`. An `@id` JSON Pointer token selects an object by ID, e.g. `/entities/@drop/shape/center/1`. If any operation fails, no operation is applied.

`budget.validation`: `visual` prioritizes a complete verified video with precision warnings; `strict` requires numerical checks. The toolbox defaults to visual; raw engine API defaults to strict. Numerical questions should explicitly select strict.
