# Agent Physics

**Turn a prompt into a physics simulation, a video, and queryable results.**

Agent Physics is a lightweight simulator built for AI agents. An agent describes a scene through structured APIs: objects, materials, initial conditions, constraints, and measurements. The engine handles simulation, rendering, and validation, then returns an MP4 video alongside the scene parameters and results.

The project focuses on **composable scenes, fast visual feedback, and reproducible experiments**. Python provides the interfaces and orchestration; C11 powers the numerical kernels. A standalone toolbox connects the simulator to different agents and applications.

[Quick start](#quick-start) · [Agent integration](#agent-integration) · [Examples](examples) · [Architecture](docs/architecture.md) · [PCB thermal design](docs/pcb-thermal-design.md) · [Toolbox 1.4.2 release notes](docs/release-1.4.2.md)

## What it can simulate

| Area | Examples and capabilities |
|---|---|
| Gravity and orbits | Three-body motion, softened N-body gravity, trajectories, and finite-window stability measurements |
| Liquids and particles | Droplets hitting a floor, a pool, or a sphere; moving liquid blobs; water washing away sand structures |
| Materials and appearance | Water, honey, glue, and molten-lead presets with continuous liquid surfaces and spray |
| Constraints and rigid bodies | Single and double pendulums, springs, rods, ropes, sliders, rigid-body rotation, and gyroscopes |
| Meshes and soft bodies | Triangle-mesh construction, soft-body collisions, cloth draping, and insertion scenes |
| Coupled scenes | Supported contact and two-way reactions between liquids, soft meshes, springs, and rigid bodies |
| Quantitative experiments | Threshold times, distances, velocities, energies, and time-series queries |
| PCB thermal maps (toolbox) | Prescribed component powers, board heat spreading, top/bottom convection, steady or transient temperature maps |

With an agent connected, requests can look like this:

> “Drop honey onto a sphere and show how it flows down.”
>
> “Double the mass of the second pendulum bob and generate a video.”
>
> “Simulate two sliders separating and record when their gap first reaches a specified distance.”

The external agent turns mechanical requests into scene configurations. Examples are starting points; the API also supports building scenes from entities, constraints, force fields, and meshes. PCB requests use a separate [thermal model](docs/pcb-thermal-design.md) within the same toolbox.

## From prompt to result

```text
Prompt → Agent + Skill → Scene construction and validation → Simulation → MP4 + Parameters + Measurements
```

Each run saves its normalized scene, execution plan, results, and diagnostics. This makes it possible to change parameters, compare experiments, or reopen a saved run for analysis. Video and measurements come from the same simulation: rendering visualizes motion, while queries read solver state.

The `visual` validation mode serves ordinary video requests; `strict` serves quantitative analysis. The standalone toolbox defaults to `visual`. Direct engine calls retain `strict` when no mode is specified.

## Quick start

The complete video workflow currently targets **macOS** and requires **Python 3.9+** and **Xcode Command Line Tools**. Mechanical runs need no third-party Python packages or model API key. The PCB thermal video path additionally requires `ffmpeg` on the host. Native code is compiled on first use and cached for subsequent runs. The prebuilt toolbox targets Apple Silicon.

```sh
git clone https://github.com/bjchen-remote/phy_engine_demo.git
cd phy_engine_demo

# Generate a three-body simulation video
python3 -m physics_demo simulate examples/three_body.json --out runs/three-body

# Inspect the saved result
python3 -m physics_demo inspect runs/three-body
```

The video is saved to `runs/three-body/simulation.mp4`. Try a water drop or a double pendulum:

```sh
python3 -m physics_demo simulate examples/droplet_ground.json --out runs/water-drop
PYTHONPATH=. python3 examples/chaotic_double_pendulum.py --out runs/double-pendulum
```

## Agent integration

Twelve physics tools, scene schemas, and a topic-based Skill give agents access to capability discovery, system and mesh construction, liquid presets, parameter editing, cost estimates, simulation, and result queries.

- **Direct engine integration:** register the [tool definitions](agent/tools.json), supply the [Skill](agent/physics-simulation/SKILL.md), and call the engine through its CLI or Python API.
- **Standalone module integration:** use the [physics toolbox](toolboxes/physics/README.md) and [task protocol](toolboxes/PROTOCOL.md). The host supplies a task directory and resource limits.
- **PCB thermal integration:** the same active toolbox exposes `pcb_example`, `pcb_validate`, `pcb_prepare`, `pcb_inspect`, and `pcb_query`. It keeps the mechanical `scene-v1` solver unchanged and uses the [PCB workflow](agent/physics-simulation/references/pcb-thermal.md).

Example tool call:

```sh
python3 -m physics_demo tool '{"tool":"physics_capabilities","arguments":{}}'
```

Toolboxes are published by version and content digest. Switching modules affects new tasks; running tasks keep their original version. The engine includes no language-model service, so hosts can choose their own agent, model, and user interface. See the [Agent guide](docs/agent-guide.md) for the complete workflow.

## Project structure

```text
physics_demo/
  core/          State, solvers, constraints, collisions, and C11 kernels
  systems/       System construction from physical parameters
  io/            Scene configuration, result storage, and video rendering
  analysis/      Measurements, queries, and trajectory analysis
agent/           Tool contracts, schemas, and Skill
examples/        Scene configurations and API examples
toolboxes/       Standalone modules, version registry, and host protocol
tests/           Numerical, interface, invalid-input, and video regressions
benchmarks/      Performance and refinement comparisons
docs/            Integration, architecture, and release documentation
```

This repository contains the standalone simulator. It runs independently of any larger research project or messaging client.

## Current scope

One minute is a response-time target for common scenes. Actual runtime depends on scene size, resolution, and hardware; the engine accepts explicit computation budgets of 1–300 seconds. Liquids and sand emphasize fast visual simulation. Material presets are not calibrated physical material models, and soft bodies and coupling use simplified formulations.

An external agent interprets descriptions or images; this project provides geometry construction and simulation. Quantitative results apply to the selected model and observation window. Passing visual validation does not certify numerical accuracy. Detailed limits are documented in the [capability reference](agent/physics-simulation/references/capability-boundaries.md).

## Development and testing

```sh
# Optional test dependencies; a virtual environment is recommended
python3 -m pip install jsonschema PyYAML
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s toolboxes/tests -v
python3 toolboxes/build_physics.py --check
```

The [toolbox manifest](toolboxes/physics/toolbox.json) in this checkout declares version **1.4.2**. The [1.4.2 release notes](docs/release-1.4.2.md) contain current verification results; older notes remain historical records. The Python distribution in `pyproject.toml` has its own version. Run the commands above or check the [CI workflow](.github/workflows/physics-quality.yml) for validation of the current source; test counts change as coverage grows. Bug reports and improvements are welcome through [Issues](https://github.com/bjchen-remote/phy_engine_demo/issues). A reproducible scene configuration helps make a report actionable.

## License

[MIT](LICENSE)
