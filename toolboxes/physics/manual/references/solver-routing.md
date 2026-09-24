# Solver algorithms and routing

This reference owns algorithm distinctions. Scene syntax is in [scene-v1](scene-v1.md), caps/planning in [capability-boundaries](capability-boundaries.md), diagnostics and gates in [tool-results](tool-results.md).

## Route selection

| Scene / request | Actual route |
|---|---|
| Supported particle/static geometry, `auto` or `native` | Native C11 DFSPH/PBD/Verlet |
| Honey/glue/molten-lead preset | Native C11 only; explicit Python is rejected and auto never falls back |
| `python` or a supported dynamic sphere under `auto` | Python PBF/PBD/Verlet reference |
| All-slider common-X-rail scene, `auto`/`python` | `analytic-1d-slider` |
| `coupling:{}` or any `rigid_body`, `auto`/`native` | `native-c11-coupled`: shared-clock DFSPH/XPBD, finite-mass contact and quaternion rotation; [coupling](coupling.md); no Python fallback |
| Standalone connection scene, `auto`/`native` | Independent C11 point-mass network; no Python fallback; [connections](connections.md) |
| Standalone mesh scene, `auto`/`native` | C11 XPBD triangle surfaces; no Python fallback; contract and algorithm limits in [mesh-modeling](mesh-modeling.md) |
| Legacy `rigid` dynamic sphere forced native, legacy positive-mass box, slider forced native | Explicit rejection |

Under `auto`, native loading or `NativeSimulationError` may restart from the initial state on Python **only if** its p90 fits the remaining deadline. It records `diagnostics.native_fallback`; otherwise `fallback_budget_exhausted` is a retryable estimate error requiring a revised plan. Forced `native` never switches algorithms; missing compiler/runtime yields non-retryable `stage=environment`, `native_backend_unavailable`. Always report `diagnostics.backend`, not the requested route. A failed solver is never silently rerun as preview or with a smaller hidden cap.

## Native water and sand

One synchronous C call owns all timesteps. Hot state is float64 SoA; fixed-radius neighbours use sparse cell hashes and CSR lists, supporting negative/widely separated coordinates without a dense world grid. Scratch capacities are reused. A persistent pthread pool parallelizes neighbour enumeration and Jacobi gather, each worker owning its output indices.

Each particle substep:

1. Save positions; apply gravity and targeted acceleration fields.
2. Build neighbours; apply dt-consistent XSPH filtering, water-sand velocity exchange, density factors and Akinci cohesion/curvature surface tension.
3. Project velocity divergence and then density using cubic-spline DFSPH constraints.
4. Integrate positions; apply particle contacts, sand anchors, position-level Coulomb collider friction and boundary projection.
5. Reconstruct velocity from displacement and damp.

XSPH strength uses exponential dt conversion so extra substeps do not multiply intended damping. Water-water PBD separation below `0.82 * spacing` suppresses clumping; it is a numerical/visual stabilizer, not finite-volume flux or molecular repulsion. Sand wetness accumulates once per touched grain, independently of water-neighbour count. Cohesion uses a wetness-weakened implicit critically damped anchor spring; granular friction decay also uses actual dt. These are qualitative material controls.

Only explicit planes add the cubic-kernel solid-half-space integral to density, pressure factor/source/acceleration/update. Boxes additionally have swept segment/AABB contact, preserving remaining tangential displacement; sphere/capsule/world bounds retain discrete endpoint projection. See scene reference before relying on wall support or anti-tunnelling behavior.

Adaptive subdivision targets travel of roughly `0.4 * spacing` and also applies the capillary scale `0.4 * sqrt(1000 * h³ / maximum_surface_tension)`, all within the quality tier's bounded substep count. The capillary branch is essential for nearly stationary millimetre interfaces that velocity CFL alone cannot see. Force start/end events split segments at the active window. Floating remnants smaller than `max(1e-12, dt*1e-5)` merge with adjacent segments to avoid nanosecond pressure steps. `steps` counts macro steps; `substeps` includes CFL, capillary and field-event segments.

The Akinci near-distance cohesion branch is `32/(pi*h^9) * (2*(h-r)^3*r^3 - h^6/64)`, preserving the paper's whole-expression normalization. The upstream SPlisHSPlasH implementation checked during development placed the constant outside that coefficient despite its adjacent formula comment. Do not replace this branch without checking the definition and controlled tests.

## Point masses and sliders

On the legacy route, point masses use pairwise Plummer-softened Newtonian gravity with velocity Verlet. They receive targeted fields, not world gravity, particle/rigid collisions, world-bound projection or attraction to water/sand. The opt-in mixed route disables mutual gravity and can give a point `collision_radius` for finite-mass contact; world gravity still does not act on it. Fixed bodies receive unmodeled support impulses; external fields do work. Either disables closed-system invariant reporting with an explicit reason. Initial timestep/final conservation gates and whole-window query checks are distinct; definitions are in the result/query references.

Sliders solve constant-acceleration X-motion segments, splitting at Coulomb stopping and pair contact events. Static/kinetic friction share one coefficient, pair restitution is the smaller input value, and equal-speed compressed contacts combine force/friction by mass. World Y gravity sets normal load; each slider supplies X acceleration. Event/contact iteration exhaustion is an explicit failure. Full scene restrictions/defaults are in [quantitative-queries](quantitative-queries.md); this is no general dynamic-box exception.

Python liquid is a PBF approximation, not a line-by-line DFSPH translation. Its dynamic sphere path has translation but no rotation. Compare controlled physical/numerical invariants across backends; exact frame agreement is not a general correctness criterion.

Standalone connections use a separate solver and ABI. Spring integration, damping, rod/rope projection, frequency controls and limitations are maintained in [connections](connections.md). They do not change the existing softened-gravity route.

## Shared native coupling

[Coupling](coupling.md) owns the mixed contract and algorithm boundary. Its C scheduler reuses persistent particle and mesh contexts: external contact impulses modify the same live x/v buffers, preserving neighbour/pressure scratch and mesh rest state. Free rigid rotation integrates world angular momentum with an implicit-midpoint/Cayley quaternion step. Contact effective mass includes inertia and lever arms; point–triangle reactions use barycentric weights. Existing standalone ABIs and one-shot entry points remain available. This is partitioned contact, without new moving-boundary DFSPH pressure terms or calibrated buoyancy.

## Observation and presentation

Queries reduce complete float64 state at t=0 and every finished macro step; native does so in C without per-step Python callbacks. Only scalar histories are saved. Video particle frames are float32 display subsets, point-mass frames float64. Particle/N-body presentation frames interpolate macro states; slider frames evaluate analytic motion segments. FPS changes presentation cost, never dynamics or query sampling.

Rendering reads trajectory without modifying it: one elevated auto-fit fixed camera, a screen-space continuous surface reconstructed separately for each liquid preset, isolated drops, and depth-ordered surface/obstacle details. Water, honey, glue and molten lead have distinct palettes; appearance never changes trajectory or query values. Box glass appearance is renderer-only. Encoding prefers H.264; unavailable encoding or sandboxed pixel decoding falls back to ImageIO Motion JPEG in MP4. Every accepted frame is verified as described in [tool-results](tool-results.md).

Mesh trajectories retain every vertex and stable local triangle indices. Their camera fits the complete trajectory; surfaces share a per-pixel depth buffer across objects, preserving holes and crossing-face occlusion. Lighting and edge accents use recorded geometry without modifying deformation. Quaternion frames add rotated rigid shape triangles, physical solid capsules and depth-tested particle/point spheres to the shared buffer. Non-solid spring helices remain annotations. Frames without q retain the existing rendering path.
