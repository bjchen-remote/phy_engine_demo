# PCB thermal and prescribed power (v1)

Use this domain only for a user request about board temperature, component heat dissipation or a thermal map. It is independent of the mechanical `scene-v1` solver.

## Workflow

1. Call `help(topic="pcb-thermal")` and optionally `pcb_example()` for an editable starting model. A PCB model is JSON passed as `spec_json`; do not pass it to `physics_prepare`.
2. Set all physical inputs from the request or disclose illustrative assumptions. In particular, effective in-plane conductivity, convection, board thickness and every component power must be explicit. If a requested parameter is unknown and a credible result depends on it, ask for it or label a sensitivity study.
3. Call `pcb_validate(spec_json)` and `pcb_prepare(spec_json)`. Check `ready_to_simulate`, normalized `spec_json`, cell count, time steps, estimate and host budget. Correct validation errors before running.
4. Call the toolbox's declared execution operation `physics_simulate` with the exact prepared `spec_json` (or no model argument). This name is retained for host compatibility; the tagged prepared model selects the PCB solver. Do not use `scene_json` for a PCB run.
5. After success, call `pcb_inspect()` for exact final temperature and balance metrics. Use `pcb_query(x_m,y_m)` for a final grid cell sample. These results describe the configured equivalent board, not a certified hardware temperature.

## Input structure

The input is an object with `schema_version:1`, optional `title`, required `board`, `grid`, `components`, optional `mode` (`steady` or `transient`), and `transient` only when needed. Coordinates originate at the lower-left board corner. Components are axis-aligned rectangles entirely inside the board and have unique IDs.

| Field | Meaning and unit |
|---|---|
| `board.width_m`, `height_m`, `thickness_m` | Board dimensions, m |
| `board.thermal_conductivity_w_mk` | Isotropic effective in-plane conductivity, W/(m K); use instead of both directional fields |
| `board.thermal_conductivity_x_w_mk`, `thermal_conductivity_y_w_mk` | Directional effective in-plane conductivities, W/(m K) |
| `board.density_kg_m3`, `specific_heat_j_kgk` | Effective board density and heat capacity |
| `board.convection_top_w_m2k`, `convection_bottom_w_m2k` | Top/bottom heat transfer to the same ambient, W/(m² K) |
| `board.ambient_c` | Ambient temperature, °C |
| `grid.nx`, `grid.ny` | Cell counts in x and y; at most 4096 cells total |
| `components[].x_m`, `y_m`, `width_m`, `height_m`, `power_w` | Rectangle origin, size and prescribed heat dissipation |
| `transient.duration_s`, `time_step_s`, `initial_c` | Duration, implicit time step and initial board temperature |
| `transient.snapshot_count` | Optional number of saved heat maps, 2–48 and no more than time steps plus one |

For a steady model, total top+bottom convection must be positive. The board edges are insulated. The solver deposits each component's full power using cell overlap, solves the conservative finite-volume equations and checks power and energy balance. It records a final temperature map and a video of the saved snapshots.

The board is a two-dimensional homogeneous thermal sheet with calibrated effective properties. It does not resolve individual copper traces, vias, package internal layers, heat sinks, radiation, airflow, through-thickness gradients or electrical power distribution. `power_w` is an input, never derived from voltage or current. A component narrower than two cells receives a resolution warning; refine the grid before treating its peak cell temperature as meaningful.
