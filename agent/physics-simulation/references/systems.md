# Physical system configurations

Use `physics_system(spec_json)` for a named system. It constructs an ordinary scene-v1 configuration with compatible initial constraints and declared observations; it does not integrate or bypass prepare. Change the specification for a new mass, length, angle or angular velocity. Do not edit an example file or add a separate solver for each configuration.

The initial factories are `pendulum` and `double_pendulum`. They use the existing native point-mass rod solver: ideal massless rods, a fixed origin anchor and an explicit uniform gravitational acceleration on the bobs. There is no rod collision geometry, joint friction or three-dimensional joint freedom. Angles and motion are in the world XY plane.

## Specification

```json
{
  "type": "double_pendulum",
  "lengths": [1.0, 1.0],
  "masses": [1.0, 1.0],
  "angles": [2.0, 2.4],
  "angular_velocities": [0.0, 0.0],
  "gravity": 9.81,
  "duration": 8.0,
  "dt": 0.002,
  "output_fps": 30,
  "quality": "balanced",
  "wall_time_s": 60
}
```

`type` is required. Arrays have one element for `pendulum` and two for `double_pendulum`. Unknown fields, booleans as numbers, nonfinite values and wrong array lengths are rejected. Angles are **absolute world angles from downward Y toward +X**, in radians; the second angle is not relative to the first rod. Each bob position and velocity includes all preceding links, so compatible rod lengths and radial velocities are built together.

| Field | Default | Bounds and meaning |
|---|---|---|
| `lengths` | 1 per rod | Each `[1e-6,1000]` m; sum at most 400 m |
| `masses` | 1 per bob | Each `[1e-9,1e12]` kg |
| `angles` | `[0.6]` or `[2.0,2.4]` | Each `[-2π,2π]` rad |
| `angular_velocities` | 0 per rod | Each `[-500,500]` rad/s; derived bob speed at most 500 m/s |
| `gravity` | 9.81 | Positive magnitude `[1e-9,250]` m/s² toward world −Y |
| `duration` | 8 | `[1e-4,30]` s |
| `dt` | 0.002 | `[1e-4,0.05]` s; preparation still checks resolution/work |
| `output_fps` | 30 | Integer `[1,60]`; affects video, not observation cadence |
| `quality` | `balanced` | `preview`, `balanced`, `high` |
| `wall_time_s` | 60 | `[1,300]` s including video |

Entity IDs are `anchor`, `bob1`, and optionally `bob2`; rod IDs are `rod1` and optionally `rod2`. Built-in query IDs are `bob1-x`, `bob1-y`, `bob1-z`, `bob1-speed`, and the corresponding `bob2-*`. Positions are measured in metres and speed in m/s at t=0 and every completed solver macro step. Additional questions still require declarations before prepare.

## Agent workflow

1. Call `physics_capabilities`, then `physics_system` with the JSON-serialized specification.
2. Pass the returned `scene_json` to `physics_prepare`; require `ok` and `ready_to_simulate`. Report the plan and any adjustments.
3. Pass prepare's exact `scene_json` to `physics_simulate` with the same budget and a dedicated output directory.
4. Require a passed result/video gate. Call `physics_query` with a built-in or additionally declared query ID; inspect actual video frames for presentation.

For parameter sweeps, build and prepare each specification independently. A valid factory input can still exceed planning limits or fail accuracy/constraint gates. Factory construction is not a guarantee of chaotic behaviour or numerical convergence.

## Python interface and analysis

```python
from physics_demo import build_system, run_system, load_run
from physics_demo.analysis import pendulum_report, compare_pendulums

spec = {"type": "double_pendulum", "angles": [2.0, 2.4], "duration": 8.0}
scene = build_system(spec)
summary = run_system(spec, "runs/pendulum-a", make_video=True)
if not (summary["ok"] and summary["quality_gate"]["passed"]):
    raise RuntimeError(summary)
run = load_run("runs/pendulum-a")
state = run.state("bob2")
series = run.series("bob2-x")
report = pendulum_report(run)

nearby = dict(spec, angles=[2.0, 2.400001])
other_summary = run_system(nearby, "runs/pendulum-b", make_video=True)
if not (other_summary["ok"] and other_summary["quality_gate"]["passed"]):
    raise RuntimeError(other_summary)
comparison = compare_pendulums(run, load_run("runs/pendulum-b"))
```

`build_system` returns a fresh raw scene without mutating the input. `run_system` returns the ordinary checked summary; `make_video=False` is for diagnostic runs. `load_run` verifies the saved run before exposing observations. `state("bob2", index=-1)` returns the selected macro-step sample as `time_s`, `entity`, `position_m`, `speed_m_s`, and `source`; the default is the final sample. It uses declared observations, not display frames, and is not a mutable state or restart checkpoint. `series(query_id)` returns `times_s` and `values` for an existing declared scalar query.

`pendulum_report(run)` returns macro-step absolute angles, kinetic/potential/total energy, peak energy drift relative to a gravitational energy scale, and maximum rod-length error. `compare_pendulums(first, second, entity="bob2", separation_m=0.1)` requires identical masses, lengths, gravity and physical observation windows. It returns the separation series, its maximum and the first recorded sample reaching the threshold (or `not_observed`); this is a sampled crossing, not an interpolated event time. For a single pendulum select `entity="bob1"`. For dt/2 refinement it automatically selects common macro-step timestamps within the same physical window, with no interpolation. `sample_counts` reports the first, second and common sample counts; `alignment` records the matching rule. The returned times and separation series contain only those common samples.

Interpret nearby-trajectory separation together with a separate dt refinement. Large separation over a finite window is sensitivity evidence for the configured model; it does not alone establish a Lyapunov exponent, prove chaos or separate physical sensitivity from numerical error. Rod projection can affect energy, so report its drift and constraint residuals alongside separation. For a small-angle single pendulum, compare the measured period with `2π√(length/gravity)` as an independent reference.
