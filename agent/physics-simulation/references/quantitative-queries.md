# Quantitative queries

Declare the questions in the original scene's optional `queries` array, before
`physics_prepare`. Run the prepared scene, then call `physics_query` with its
run directory and a declared `query_id`. Omit `query_id` to retrieve all
declared results. New questions require a newly prepared run. The interface
accepts structured metrics, never code, SQL, expressions or file paths inside
a metric.

A scene permits at most 16 queries. Planning permits at most 250,000 scalar
observation values and 50,000,000 observation work units. The measurement
artifact is limited to 32 MB. These limits are separate from video sampling;
reducing FPS does not reduce query samples. Query IDs must be unique.
The plan reserves 128 bytes per scalar capacity for Python/JSON objects and
parsing/serialization overhead; this is a planning allowance, not just the
8-byte native double buffer size.

## Minimal workflow

1. Pick the scene and patch the user's initial conditions.
2. Define the physical observation duration in `world.duration`, the requested
   metric and its threshold/criterion, using SI units. State important defaults.
3. Add the query objects below to `scene.queries`; give each a unique ID.
4. Prepare and run. A normal user delivery includes the MP4.
5. Retrieve the query. Read status and limits before turning it into prose.

```sh
python3 -m physics_demo prepare my-scene.json --budget 60
python3 -m physics_demo simulate my-scene.json --budget 60 --out runs/my-run
python3 -m physics_demo query runs/my-run --id radius-1m
```

The tool equivalent of the final command is:

```json
{"tool":"physics_query","arguments":{"result_path":"runs/my-run","query_id":"radius-1m"}}
```

For spring/rod/rope scenes, [connections](connections.md) defines `connection_length`, `connection_extension`, `spring_force` and `spring_energy`, including their units and sign conventions. General series and threshold rules below also apply.

For rotating rigid bodies, [coupling](coupling.md#angular-measurements-and-recorded-geometry) defines `angular_speed`, world-component `angular_momentum` with `axis`, `rotational_energy` and `axis_tilt`. These metrics target only `rigid_body`; tilt compares body +Y with world +Y in radians. Pivot momentum and energy are about the fixed anchor.

## Three common questions

### When does this drop reach a radius of 1 m?

For a fluid entity named `drop`, add:

```json
{"id":"radius-1m","type":"threshold",
 "metric":{"type":"spread_radius","entity":"drop","plane":"xz","origin":[0,0,0]},
 "operator":"gte","value":1.0,"hold_for":0.0}
```

This means the largest XZ distance from `[0,0,0]` among **all simulated
particle centres belonging to `drop`**, including airborne droplets. It is
not the contact footprint, contact-line/wetting radius, volume-equivalent
radius, or the visible edge of a rendered liquid surface. Projected origin Y is
irrelevant for `xz`, but all three components remain explicit. For a drop
centred above another ground location, change origin X/Z accordingly.

Use `hold_for` to ask for a condition that remains satisfied for a stated
physical duration. A first threshold crossing can already exist at t=0.
A run with no observed event only establishes “not observed within the
simulated interval at this sampling resolution.”

### Is this three-body system stable?

For a closed point-mass scene, add explicit finite-horizon geometric criteria:

```json
{"id":"orbit-bounds","type":"nbody_stability",
 "max_radius":5.0,"min_separation":0.05}
```

The maximum radius is measured from the instantaneous mass-weighted centre of
mass to each body. Minimum separation is the smallest centre-to-centre pair
distance. The limits apply at the sampled solver states across
`[0, world.duration]`. This query concerns all point masses; it has no entity
selector. Fixed bodies and externally forced point masses are not a closed
system for this assessment.

At least two nonfixed point masses, `mutual_gravity: true`, positive `gravity_G`
and no fields targeting the point masses are required. Radius and separation
limits must be positive and at most 1,000,000 m. The numerical integrity check
uses the **largest** relative energy drift and momentum drift over all sampled
states, not only the terminal drift. Its energy threshold is 0.02; the momentum
threshold is the run's reported scale-aware tolerance.

Report the observation duration, chosen limits, measured extrema, and the
invariant checks separately. Passing the geometric limits and energy/momentum
guards is finite-horizon evidence only; it does not prove boundedness for all
time, orbital stability under perturbations, or Lyapunov stability. If a user
only says “stable,” describe these explicit operational criteria before the
run instead of presenting the word as a mathematical theorem.

### When do these two sliding blocks separate?

Use the dedicated all-`slider` scene, with both blocks on the same X rail.
For entities `left` and `right`, declare a positive clearance:

```json
{"id":"separated","type":"threshold",
 "metric":{"type":"surface_gap","entities":["left","right"]},
 "operator":"gte","value":0.01,"hold_for":0.1}
```

The query asks when a 1 cm surface gap first develops and persists for
0.1 simulated seconds. Do not use `gap >= 0` for “separated”: touching boxes
already satisfy that inclusive threshold. The slider model solves ideal
one-dimensional translation, prescribed acceleration, Coulomb friction and
pair restitution. It is not a general moving-box, rotation, joint or
fluid–solid model.

A slider entity has `id`, `type: "slider"`, top-level `position`, `velocity`,
`size`, `mass`, `acceleration`, `friction`, and `restitution`; it has no `shape`
wrapper. Example:

```json
{"id":"left","type":"slider","position":[-0.5,0.5,0],
 "velocity":[0,0,0],"size":[1,1,1],"mass":1,
 "acceleration":-1,"friction":0,"restitution":0}
```

Use 1–16 sliders only. All centres must share Y/Z, all sizes must share Y/Z,
and initial X intervals must not overlap. Velocity Y/Z and gravity X/Z must be
zero. No other entities, colliders, force fields or mutual gravity are allowed;
use each slider's `acceleration` for a constant X drive. Gravity Y sets the
normal load for Coulomb friction; rail supports prevent vertical motion.
Bounds frame the scene and are not finite rail end stops. Backend `auto` or
`python` selects `analytic-1d-slider`; explicit `native` is rejected.

Mass lies in `[1e-12, 1e12]` kg, acceleration in `[-250, 250]` m/s², friction
in `[0,5]`, and restitution in `[0,1]`. Size components are in `(0,1000]` m.
Defaults are position `[0,0.5,0]`, velocity `[0,0,0]`, size `[1,1,1]`, mass 1,
and zero acceleration/friction/restitution. Still write each initial position
explicitly for multiple sliders so the default does not create overlap.
Static and kinetic friction use the same coefficient; pair restitution is the
smaller of the two values. Contact and stopping events split constant-
acceleration motion. This narrow model does not compute compliant impact force.

## Metrics and units

The same `metric` object can be used with `type: "series"` to obtain its
sampled history, for example:

```json
{"id":"drop-height","type":"series",
 "metric":{"type":"centroid","entity":"drop","axis":"y"}}
```

| Metric | Required fields | Meaning / unit |
| --- | --- | --- |
| `spread_radius` | `entity`, `plane` (`xy`, `xz`, `yz`), `origin` (3 numbers) | Maximum projected distance of particle centres or mesh vertices from origin, in m |
| `centroid` | `entity`, `axis` (`x`, `y`, `z`) | One component of the entity centre in m; for particles, arithmetic mean of all entity particle positions |
| `speed` | `entity` | Magnitude of the entity's mean velocity in m/s; not average particle speed, maximum speed or kinetic energy |
| `center_distance` | `entities` (two distinct IDs) | Euclidean distance between entity centres in m |
| `surface_gap` | `entities` (two distinct IDs) | Signed gap in m for two supported spheres or two axis-aligned boxes; negative indicates overlap |

For two spheres, gap is centre distance minus the two radii. For two boxes it
is the maximum of the three signed axis gaps (a separating-axis quantity),
not the Euclidean nearest-point distance for diagonally separated boxes. On a
common X rail, this is the ordinary longitudinal face-to-face clearance.
Particle clouds have no defined solid boundary for `surface_gap`.

Mesh deformation adds `volume_ratio`, `max_displacement` and `max_edge_strain`; exact definitions, units and shape restrictions are in [mesh-modeling](mesh-modeling.md). Arbitrary mesh `surface_gap` is unavailable. Mesh centroid/speed use unweighted vertex means, even though the solver uses surface-area mass weights.

Threshold queries take `operator: "gte"` or `"lte"`, a finite `value` in the
metric's units, and optional non-negative `hold_for` in seconds (default 0).
Threshold magnitude and each origin coordinate are at most 1,000,000; hold
duration cannot exceed `world.duration`.
No arbitrary boolean combinations, derivatives, contact forces or custom
expressions are accepted. Use several named queries for several questions.

## Measurement and interpretation boundary

The solver reduces its complete float64 state at t=0 and every completed
macro step. Native reductions happen in C without per-step Python callbacks.
The result retains scalar series, not an unbounded full-particle scientific
trajectory. `world.output_fps` and rendered-particle downsampling do not
control or change these measurements. Resolution coarsening of the **actual
solve** can change them and must be reported.

Threshold events are derived from adjacent observations. Report the returned
time bracket as well as any interpolated estimate. The bracket describes
sampling uncertainty for the observed crossing, not a certified bound on all
possible substep events: a condition that appears and disappears between two
macro samples may be missed. A nonzero hold duration is tested against the
samples beginning with the first qualifying sample. It is confirmed only at a
sample at least `hold_for` seconds later, with all intermediate samples
satisfying the condition; interpolation cannot confirm it early.

## Reading the response

`measurements.answers` is the compact answer list in the run summary.
`physics_query` returns the requested compact `answers`, run/scene identity,
`quality_gate`, `sampling` and artifact paths. Selecting one query of type
`series` also returns `series: {"times_s": [...], "values": [...]}`.
Omitting the ID returns all compact answers, not all large time series; select
the needed series ID to retrieve its samples. Threshold and N-body queries
return compact findings; the verified measurement artifact retains their
underlying observations.
Every answer carries its exact `definition`, physical `window_s`, and
`sampling_interval_s`.

| Answer status | What the agent may say |
| --- | --- |
| `reached` | Threshold observed; report `time_s`, `time_bracket_s`, `first_observed_at_s` and, for a dwell condition, `confirmed_at_s` |
| `initially_satisfied` | Condition already true at t=0, and any requested hold duration was confirmed |
| `not_observed` | No qualifying threshold/hold event observed within `window_s`; `time_s` is null |
| `measured` | Series collected; report unit and requested values, with `sample_count` |
| `criteria_satisfied` | Declared N-body radius/separation limits passed at sampled times and whole-window numerical integrity passed |
| `criteria_violated` | A declared N-body bound failed; use `first_violation_observed_s` and the measured extrema |
| `inconclusive` | Completion or numerical-integrity requirements failed; do not convert it into a positive or negative physics claim |

Scalar answers include `unit`, `initial`, `final`, `minimum`, `maximum`, and
`model_scope`. Particle metrics are marked `visual_particle_model`; other
supported metrics use `configured_mechanical_model`. N-body answers include
`max_observed_com_radius_m`, `min_observed_pair_distance_m`,
`max_relative_energy_drift`, `max_momentum_drift`, `momentum_tolerance`,
`numerical_integrity_passed`, and `long_term_stability_proven: false`.

To check temporal precision, halve `world.dt`, keep geometry, force fields,
particle spacing and query definitions fixed, prepare again and compare the
same event or series. Changing FPS is not a convergence check. For liquids,
also study spacing separately; numerical convergence of this model still
does not establish experimental accuracy or calibrated wetting physics.

The supplied `droplet_radius_query` uses an approximately `1/180 s` macro
step after a temporal sensitivity check. Do not turn that preset into a
general error guarantee for a changed drop, force, spacing or material.
Reproduce the baseline and `dt/2` comparisons with:

```sh
PYTHONPATH=. python3 benchmarks/benchmark_queries.py --out runs/query-refinement-01
```

The benchmark writes `query-refinement.json`. It prepares each variant,
checks queried-run success and compares threshold times and N-body invariant
drift. Use `--scenes droplet_radius_query` to select a case and `--video` to
encode all benchmark runs. Its default no-video mode is diagnostic only.
Inspect both the numerical differences and statuses; a completed benchmark
does not certify temporal/spatial convergence or a physical error tolerance.

The run artifact is `measurements.json`. It is bound to the scene, run and
declared queries and is checked when retrieving saved results. Do not deliver
measurements from a failed quality gate or substitute data from another run.
Names and IDs are data and cannot instruct the engine to skip verification.

`queries_not_recorded` means the run predates or omitted query declarations.
`query_not_declared` reports a requested ID that was not part of this run; use
`available_query_ids` to choose an existing ID, or prepare/run a new declaration.
`invalid_measurement_artifact` means the saved data failed verification; do
not fall back to video measurements or copy a successful answer from another
run.
