# Limits, planning and fidelity

Read `physics_capabilities` for current machine-readable limits before constructing a scene. This reference explains the limits; [tool-results](tool-results.md) owns quality gates and error recovery.

## Input and allocation limits

Input is one finite scene-v1 JSON object. Present invalid fields never become defaults: booleans are not numbers, strings are not booleans, and NaN/Infinity, duplicate keys, cycles and non-string keys are rejected. Optional missing fields may receive defaults. Names/IDs and saved files cannot override these rules.

| Resource | Limit |
|---|---|
| Input scene | 1 MB, 20,000 values (mesh scenes 160,000), depth 32 |
| Entities / point masses / colliders / fields | 128 / 64 / 64 / 8; nonempty unique target IDs |
| Physical duration / macro dt | `(0,30]` s / `[0.0001,0.05]` s |
| Output FPS / per-run wall budget | Integer 1–60 / 1–300 s |
| Particle spacing | 0.0001–0.5 m; sub-millimetre values require physically small bounds and a bounded capillary timestep |
| Coordinate magnitude / world axis span / shape extent | 10,000 / 1,000 / 1,000 m |
| Initial speed / combined target field acceleration | 500 m/s / 250 m/s² |
| Video handoff | 600,000 particle-frame samples |
| Queries / scalar observations / observation work | 16 / 250,000 / 50,000,000 work units |
| Saved result / measurement / completion files | 128 MB / 32 MB / 64 KiB |
| Standalone common-X-rail sliders | 1–16; full restrictions in [query reference](quantitative-queries.md) |
| Standalone spring/rod/rope networks | Link/node caps, numerical controls and route limits in [connections](connections.md) |
| Mixed `coupling` scenes | 4096 particles, 2048 mesh vertices, 16 rigid bodies, 128 links; [full contract](coupling.md) |
| Standalone native mesh scenes | Object/topology/frame caps and provisional timing model in [mesh-modeling](mesh-modeling.md) |

Geometry is counted with bounded arithmetic before sampling; each volume contributes at least one particle and coarsening has a fixed iteration ceiling. Validation rejects more than eight particle volumes overlapping at a volume centre. The native grid rejects more than 512 particles in one cell or more than 512 directed neighbour links per particle on average; it fails instead of silently discarding neighbours.

| Quality | Simulation particles | Display particles | Density/divergence iterations | Max CFL substeps |
|---|---:|---:|---:|---:|
| preview | 2,000 | 2,000 | 5 / 2 | 6 |
| balanced | 8,000 | 6,000 | 12 / 4 | 8 |
| high | 24,000 | 10,000 | 16 / 6 | 12 |

These are caps, not guaranteed executed counts. Strong forcing, speed, small dt or collider-heavy scenes can cost more than particle count alone suggests. Reaching adaptive or projection iteration caps is reported; compare diagnostics at controlled spacing/backend before interpreting fidelity.

The table above applies to particles. Connection scenes have their own bounded substeps/iterations and frequency feasibility in [connections](connections.md). Mesh scenes retain their explicit topology; quality controls XPBD substeps/iterations, and any simplification requires rebuilding the asset. Image depth remains an agent assumption. Mesh physical limits and delivery diagnostics are defined in [mesh-modeling](mesh-modeling.md).

## Planning decisions

Use `physics_prepare`; require `ok` and `ready_to_simulate`. Low-level estimate's `ok` only means planning succeeded. `timing_estimate.fits_budget`, `bounds_feasible`, `initial_bounds_feasible`, `nbody_timestep_feasible`, and any measurement `fits_limits` must hold.

- `timing_estimate` separates physical duration from solver/video/cold-compile p50/p90 wall time, total p50/p90, hard limit, calibration hardware/confidence and limiting factor. The cold reserve includes first-run compilation even if a cache exists. Plan with p90; Apple M4 calibration is not a guarantee for other hardware.
- Native particles use one resolution. `requested_particles` counts original per-entity requests; `uniform_spacing_particles_before_cap` counts all volumes at the finest requested spacing. Caps/budget then coarsen every volume together. Report the final `effective_spacing`, counts and `adjustments`.
- Display downsampling changes `render_particle_count`, not `particle_count`. Lower FPS only reduces output/encoding work. It cannot fix solver-dominated runtime or measurement capacity.
- After coarsening, every bounds span must hold `minimum_particle_bounds_span_m`: the larger of `0.92 * effective_spacing` and a supported dynamic sphere's diameter. Actual sampled initial support is checked separately. Enlarge the reported narrow span or revise an allowed resolution, then prepare again.
- Mutual point-mass gravity requires `nbody_initial_step_ratio <= 0.1` relative to the shortest initial softened pair dynamical time. Lower dt to repair it; FPS cannot help.
- `measurement_plan` reports sample/scalar capacity, work, float64 full-population source, interval and memory. Memory reserves 128 bytes per scalar for Python/JSON overhead, not just the 8-byte C buffer.

The planner does not know which values were explicit user requirements. If an adjustment changes required precision, duration, geometry, force, FPS or backend, operate only within already authorized tradeoffs. If no compliant bounded plan fits, report p90 and the limiting factor. No incomplete run or hidden preview retry counts as success.

## Time and filesystem boundaries

Tool `budget_seconds` overrides scene `wall_time_s` for that call, including video. Deadlines are cooperative: native macro/substep boundaries and renderer subprocess timeouts are checked, but an individual kernel, Python conversion or filesystem write is not OS-preempted. Report actual runtime; persistence timing semantics are in [tool-results](tool-results.md).

Use a dedicated output directory containing only known run artifacts. Filesystem/home roots, symlink directories, unrelated entries, symlinks and non-files are rejected. JSON writes use same-directory temporary files, `fsync` and atomic replacement; filesystem permissions remain authoritative. Do not clear unrelated user files to satisfy the check.

## Physical scope

| Model | Supported interpretation | Outside its scope |
|---|---|---|
| Point mass | Softened N-body, standalone links, or opt-in collision-radius point in [coupling](coupling.md) | World gravity, resolved point spin, permanent stability proof |
| Liquid | Native DFSPH visual single-phase free surface; water/honey/glue/molten-lead presets and numerical diagnostics | Calibrated CFD/material data, density contrast, immiscibility, air/bubbles, wetting/splash thresholds, pressure/loads, non-Newtonian rheology, heat/phase change/chemistry |
| Particle self-gravity | Native softened Barnes-Hut/direct gravity on fluid/granular particles; one shared reference density | Particle/point-mass gravity exchange, compressible gas, exact tree angular momentum or orbital-stability certification |
| Sand/water-sand | Contacts, wetting, weakened cohesion and drag; qualitative erosion | Soil constitutive law, pore pressure, sediment rate, strength/scour prediction |
| Legacy `rigid` / collider | Static native geometry; limited Python sphere translation | Rotational legacy-body dynamics; use `rigid_body` explicitly |
| `rigid_body` / mixed route | Uniform sphere/box/cylinder rotation, pivot gravity and attachment/contact torque | Exact general contact manifolds, monolithic pressure FSI, calibrated buoyancy, bearings, fracture |
| Mesh | XPBD surfaces, cloth, standalone contact or opt-in two-way mixed reaction | Calibrated volumetric stress, cutting/puncture, pressure-coupled FSI, general collision-free guarantees |
| Fields | Finite prescribed uniform/radial/vortex accelerations | Resolved wind/pump/explosion/electromagnetism or reaction on field source |
| Slider | Common rail, event-split translation, Coulomb stopping, pair restitution | 3D contact, rotation, end stops, fluids, compliant impact forces |
| Query | Full-state sampled model metrics and threshold/window answers | Unobserved between-step events, post-run new metrics, experimental calibration |
| Video | Fixed-camera screen-space continuous liquid surface with preset palettes and solid/glass styles | Interactive orbit UI, 3D reconstructed free-surface mesh, refraction/caustics, inferred liquid depth |

A water ball has no membrane; a fountain adds no new particles. `represented_water_volume_m3` is nominal `N * spacing³`, not occupied volume or a conservation proof. Native world bounds are invisible closed walls; geometry and contact-specific limitations are in [scene-v1](scene-v1.md). Plane contact has pressure support, projection, friction and optional bounded visual water adhesion, but no calibrated surface energy, hysteresis or dynamic contact angle; droplet impact is a single-phase visual model without entrained air.

The drop examples have distinct visual boundaries. `droplet_ground` uses a 3 mm radius drop on a dry floor and shows radial ground beads without demonstrated airborne spray. `droplet_ground_micro_wet` shows upward droplets with a finite 1.2 mm water film; disclose the film rather than presenting it as dry ground. `droplet_ground_splash` uses a 0.32 m radius drop on dry ground; its `0.8 s` early-impact window ends before particles contact the `±4 m` horizontal walls; this remains an uncalibrated macroscopic visual example. `droplet_ground_macro_wet` uses a 0.32 m drop over a finite 4 cm pre-existing water film and produces upward spray; its `surface_tension=0.055` on drop and film is an uncalibrated model coefficient adjustment, not a direct measure of molecular attraction. `droplet_cone` shows qualitative water–mesh contact. The fluid pressure solve supports analytic planes but not mesh-boundary density, while mesh contact has no fluid wetting adhesion or fluid–cone friction. A flat-plane versus flat-mesh numerical comparison found lower particle density in the mesh variant; it did not establish that upward ejection is erroneous or what caused it. Jet heights and splash thresholds need experimental validation. Preserve user-specified dimensions, wetness and duration when using any example.

Unsupported or oversized requests are rejected as stated, never renamed to appear supported. Offer a finite alternative if useful, and run it only when approximation is authorized. Do not retry unchanged input or loop around a capability/quality boundary.

One minute is a normal latency target, not a universal failure boundary. A host may grant a longer budget for larger requests and send one concise progress notice while work continues.
