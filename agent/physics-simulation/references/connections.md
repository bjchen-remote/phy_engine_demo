# Springs, rods and ropes

This page describes the existing standalone point-mass network for oscillators, damped motion, coupled springs and pendulums. For rigid torque, mesh attachments or a physical thick link interacting with water, read [mixed coupling](coupling.md). For single/double pendulums use the parameterized [system factory](systems.md); change the specification rather than example files. For other networks start with `spring_oscillator`, `damped_spring`, `coupled_springs`, `rod_pendulum`, `rope_catch`, `spring_chain` or `spring_pendulum`, then patch the requested parameters. The twelve-tool interface adds `physics_system` for named configurations and `physics_liquid` for liquid presets; all scenes still pass prepare → simulate → query. No separate integration loop is added for pendulums.

## Scene and physical meaning

A nonempty `scene.connections`, without `coupling` or `rigid_body`, selects the native standalone connection route. It accepts only `point_mass` entities, no colliders, `interactions.mutual_gravity: false`, and `budget.backend: "auto"` or `"native"`. There is no Python fallback. Each connection names two distinct existing entity IDs; its own `id` must be unique.

```json
"connections": [
  {"id":"spring", "type":"spring", "entities":["anchor","bob"],
   "rest_length":1.0, "stiffness":20.0, "damping":0.5}
],
"connection_settings": {"substeps":4, "iterations":16}
```

| Field or model | Meaning |
|---|---|
| Entity `mass` | Positive point mass in kg, at least `1e-9` |
| Entity `fixed: true` | A stationary support; initial velocity must be `[0,0,0]` |
| `rest_length` | Natural spring length, rod length or maximum rope length, in m |
| Spring `stiffness` | Hooke coefficient `k`, N/m; positive |
| Spring `damping` | Axial relative-velocity damping `c`, N·s/m; defaults to zero |
| Spring `break_tensile_strain` | Optional dimensionless threshold in `[0,10]`; once `(length-rest_length)/rest_length` is strictly greater, the spring permanently stops exerting force and stores no elastic energy |
| `rod` | Ideal massless link holding its endpoint distance at `rest_length`; can push or pull |
| `rope` | Ideal massless tension-only length constraint; slack below `rest_length`, no compression force |

For endpoints a/b, write `n=(x_b-x_a)/length` and `radial_speed=(v_b-v_a)·n`. An intact spring's signed force is `k*(length-rest_length)+c*radial_speed`, positive in tension and negative in compression. Endpoint a receives this force along n; b receives the opposite. Damping resists relative motion only along the spring, not tangential motion. Optional failure is checked at the initial state and after each complete native integration substep; a spring already over threshold fails at `t=0`. At a later failure, that completed substep includes the spring's force and damping, and all subsequent substeps omit it. A failed spring never heals under compression. This is a discrete link failure proxy, not calibrated material fracture or a breakable rigid beam. Standalone connections have no mass, bending, thickness or collision surface. The separate opt-in mixed contract supports `solid` capsules and point-endpoint lumped mass; it does not support this failure field.

Point masses retain their existing gravity semantics: **`world.gravity` does not accelerate them**. For a gravity pendulum, explicitly add a uniform field such as `{"id":"gravity","type":"uniform","targets":["bob"],"acceleration":[0,-9.81,0]}`. Uniform, radial and vortex fields use the existing target and finite time-window rules. Fixed supports stay fixed even when targeted. `world.bounds` does not provide point-mass collision walls.

Initial rods must already match their target length; initial ropes must not be overlength beyond `max(1e-9,1e-7*rest_length)` m. Spring and rod endpoints must start more than `1e-9` m apart. A slack rope may start with coincident endpoints. An initial rod must also have zero relative radial velocity, within `max(1e-9,1e-7*max(1,|v_a|,|v_b|))` m/s; incompatible velocities are rejected rather than silently changed. Mutually inconsistent constraint networks can still fail to converge; prepare is not a proof of constraint solvability.

## Queries

Declare metrics before prepare; the target key is `connection`, not `entity`:

```json
{"id":"extension", "type":"series",
 "metric":{"type":"connection_extension","connection":"spring"}}
```

| Metric | Definition and unit |
|---|---|
| `connection_length` | Current 3D endpoint distance, m |
| `connection_extension` | Distance minus `rest_length`, m; negative means compression or rope slack |
| `spring_force` | Signed axial spring-plus-damper force above, N; spring only |
| `spring_energy` | `0.5*k*(length-rest_length)^2`, J; spring only, excludes kinetic energy and dissipated work |

After failure, `spring_force` and `spring_energy` report zero; length and extension remain geometric measurements. `diagnostics.connection_break_times_s` and `connection_break_lengths_m` each list one value per scene link (`null` for intact links), and `broken_connection_count` is the total. The recorded length must strictly exceed the declared tensile threshold; the initial witness must match the scene's initial geometry. Fracture-enabled trajectory frames contain `connection_active` booleans in scene-link order. A frame at or after a recorded failure time omits that link from the video, including in display-only slow motion. Positions are still interpolated across macro steps, so a video frame is not a resolved picture of the within-step failure instant. The saved length witness checks internal consistency; it does not cryptographically authenticate the native solver output.

`centroid`, `speed` and `center_distance` remain available for point-mass entities. A point-mass centroid is its position. Rod/rope reaction force and tension are not query outputs. These scenes cannot use `nbody_stability`. General series/threshold/hold semantics are maintained in [quantitative-queries](quantitative-queries.md).

Measurements use the complete float64 solver state at t=0 and each finished macro step. Video FPS does not change measurement resolution. Threshold brackets describe sampling; they do not bound integration error or detect every between-step event.

## Numerical controls and budgets

| Resource or parameter | Limit |
|---|---|
| Entities / connections | At most 64 / 256; connections nonempty |
| `rest_length` | `[1e-6,1000]` m |
| `stiffness` / `damping` | `[1e-9,1e7]` N/m / `[0,1e5]` N·s/m |
| Optional `break_tensile_strain` | `[0,10]` dimensionless; spring only, standalone route |
| Requested substeps / iterations | Integers `[1,64]` each |
| Duration / macro dt | `[1e-4,60]` s / `[1e-4,0.05]` s |
| Macro steps / planned solver work | 250,000 / 100,000,000 work units |

`connection_settings` is valid only with connections. Quality defaults are preview `(2 substeps,8 iterations)`, balanced `(4,16)` and high `(8,32)`. Planning increases substeps as needed for a conservative network frequency bound `Omega`, targeting `h*Omega <= 0.05`. A damping bound `Gamma = 2*max_i(sum incident c / m_i)` over mobile nodes also requires `h*Gamma <= 0.25` as a time-accuracy guard; exact pair damping alone does not resolve rapidly damped coupled motion. Rods/ropes require at least eight. The effective count is at least the requested count. A requirement beyond 64 is rejected instead of silently exceeding the cap. Read the returned connection plan and adjustments rather than assuming the requested count was used.

Large k, small masses, long runs and many links increase cost. The plan reports `connection_nodes`, `connection_count`, `connection_substeps`, `connection_iterations`, `connection_omega_bound_rad_s`, `connection_damping_bound_s_inv` and `connection_fits_limits`. Its wall-time estimate is provisional; target-machine runtime remains authoritative.

The C solver wraps Hooke velocity-Verlet steps in exact pairwise axial dashpot half-steps, reversing link order for the final half-step. Rod/rope position projection is mass weighted; its correction updates velocity, followed by current-direction radial velocity projection. The rod position solve uses the previous substep direction and a local quadratic length solve (SHAKE), followed by current-direction velocity projection (RATTLE). Rope projection remains unilateral and uses the current direction. Finite iteration counts can leave network residuals. Rods enforce both directions; ropes constrain outward motion only when taut and engage nonelastically. Force-window boundaries split actual substeps. Damping is treated at the actual substep duration, not as per-frame decay. Constraint projection can introduce numerical dissipation. Do not describe a mixed damped/constraint network as globally second order, exactly energy conserving or a calibrated engineering model.

Keep the normal wall-clock and measurement budgets from [capability-boundaries](capability-boundaries.md). Prepare must report feasibility before running. For a timestep sensitivity check, halve `world.dt`, re-prepare and compare the same declared quantities at matching physical times. A useful independent check for one mass on an undamped spring with a fixed anchor is `omega=sqrt(k/m)` and `period=2*pi/omega`, within a collinear positive-length oscillation. The damped undercritical period uses `sqrt(k/m-(c/(2m))^2)`. Do not infer accuracy from smooth animation.

`diagnostics.backend` is `native-c11-connections`. `max_rod_error_m`, `max_rope_extension_m` and `max_speed_m_s` are whole-run peaks over the initial state and completed substeps. `max_constraint_error_ratio` normalizes each rod error/rope excess by its own `max(1e-6,1e-4*rest_length)` m tolerance before taking the peak. Initial/final kinetic and spring energies exclude external potential and field work. `connection_energy_conservation_applicable` is true only for all-spring scenes with zero damping, no fields and no actual broken spring; it is not an accuracy certificate. For a later first break in such a scene, `connection_prebreak_peak_relative_energy_drift` records the peak through the last fully integrated state with every spring still intact and must remain at most 2%. Failure can dissipate stored spring energy, so the conservative whole-run energy gate does not apply after a spring breaks. A spring collapsing to effectively zero length fails rather than inventing a force direction. Failed constraint residual or finite/completion gates cannot be delivered as success.

For an applicable undamped all-spring scene, `connection_peak_relative_energy_drift` tracks the largest substep energy drift, normalized by `max(initial_energy,1e-12 J)`. Both endpoint and whole-run peak must pass the 2% delivery gate. This prevents a nearly conserved final state from hiding a larger error mid-run; passing the gate still does not certify trajectory convergence.

## Rendering and scope

The renderer reads real endpoint positions from `trajectory.frames[].g` with `gravity_body_ids`; it reads link type and rest length from the normalized scene. Springs use visible coils, rods use straight lines, and slack ropes use a schematic curved line. Square supports distinguish fixed endpoints from round mobile masses. A rope's drawn curve is only a slack cue: cable shape, sag equilibrium and distributed rope mass are not solved.

These standalone networks are independent of water, sand, dynamic rigid bodies, sliders and mesh soft bodies. To request cross-domain attachment/contact, explicitly use [coupling](coupling.md); its connections do not support `break_tensile_strain`. Standalone links have no contact, self-collision, frictional joints or rigid-body torque. Only opted-in springs can break under tension; rods and ropes cannot. Crossings pass through each other. Pendulum pivots are fixed points, not simulated bearings. Report this scope and the effective substeps/iterations with the normal verified MP4 and query answers.
