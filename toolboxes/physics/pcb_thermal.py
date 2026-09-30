"""Conservative, board-level thermal model for a thin printed circuit board.

The board is represented by one through-thickness temperature per Cartesian
control volume.  Its in-plane conductivity is an *effective, calibrated* board
property; this is not a copper-trace, via, package, or airflow model.

Coordinates start at the lower-left board corner in metres.  Rows in returned
maps ascend in y and columns ascend in x.  The input schema is documented by
``validate_pcb_spec`` and the example in ``tests/test_pcb_thermal.py``.
"""

from __future__ import annotations

import math
from typing import Any


class PcbThermalError(ValueError):
    """Invalid model or numerical convergence failure."""


def _object(value: Any, path: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PcbThermalError(f"{path} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise PcbThermalError(f"{path} field names must be strings")
    extra = set(value) - allowed
    if extra:
        raise PcbThermalError(f"{path} has unknown fields: {', '.join(sorted(extra))}")
    return value


def _number(value: Any, path: str, *, positive: bool = False,
            nonnegative: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PcbThermalError(f"{path} must be a finite number")
    try:
        number = float(value)
    except OverflowError as error:
        raise PcbThermalError(f"{path} must be a finite number") from error
    if not math.isfinite(number):
        raise PcbThermalError(f"{path} must be a finite number")
    if positive and number <= 0:
        raise PcbThermalError(f"{path} must be positive")
    if nonnegative and number < 0:
        raise PcbThermalError(f"{path} must be nonnegative")
    return number


def _required_number(source: dict[str, Any], key: str, path: str,
                     *, positive: bool = False, nonnegative: bool = False) -> float:
    if key not in source:
        raise PcbThermalError(f"{path}.{key} is required")
    return _number(source[key], f"{path}.{key}", positive=positive,
                   nonnegative=nonnegative)


def _integer(value: Any, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise PcbThermalError(f"{path} must be an integer from {minimum} to {maximum}")
    return value


def validate_pcb_spec(spec: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a prescribed-power PCB thermal experiment.

    Required keys are ``board``, ``grid``, and ``components``.  ``mode`` is
    ``steady`` by default or ``transient`` with a required ``transient`` object.
    The board needs geometry, density, heat capacity, ambient temperature,
    top/bottom convection and either one isotropic or two directional
    in-plane conductivities.  No material numbers are silently invented.
    """
    source = _object(spec, "spec", {"schema_version", "title", "board", "grid",
                                    "components", "mode", "transient"})
    version = source.get("schema_version", 1)
    if version != 1 or isinstance(version, bool):
        raise PcbThermalError("schema_version must be 1")
    title = source.get("title", "PCB thermal simulation")
    if not isinstance(title, str) or not title.strip() or len(title) > 160:
        raise PcbThermalError("title must be a nonempty string of at most 160 characters")
    if any(ord(char) < 32 for char in title):
        raise PcbThermalError("title must not contain control characters")
    if "board" not in source:
        raise PcbThermalError("board is required")
    board = _object(source["board"], "board", {
        "width_m", "height_m", "thickness_m", "thermal_conductivity_w_mk",
        "thermal_conductivity_x_w_mk", "thermal_conductivity_y_w_mk",
        "density_kg_m3", "specific_heat_j_kgk",
        "convection_top_w_m2k", "convection_bottom_w_m2k", "ambient_c",
    })
    isotropic = "thermal_conductivity_w_mk" in board
    directional = ("thermal_conductivity_x_w_mk" in board or
                   "thermal_conductivity_y_w_mk" in board)
    if isotropic == directional:
        raise PcbThermalError("board requires either thermal_conductivity_w_mk or both directional conductivities")
    if isotropic:
        kx = ky = _required_number(board, "thermal_conductivity_w_mk", "board", positive=True)
    else:
        kx = _required_number(board, "thermal_conductivity_x_w_mk", "board", positive=True)
        ky = _required_number(board, "thermal_conductivity_y_w_mk", "board", positive=True)
    normalized_board = {
        "width_m": _required_number(board, "width_m", "board", positive=True),
        "height_m": _required_number(board, "height_m", "board", positive=True),
        "thickness_m": _required_number(board, "thickness_m", "board", positive=True),
        "thermal_conductivity_x_w_mk": kx,
        "thermal_conductivity_y_w_mk": ky,
        "density_kg_m3": _required_number(board, "density_kg_m3", "board", positive=True),
        "specific_heat_j_kgk": _required_number(board, "specific_heat_j_kgk", "board", positive=True),
        "convection_top_w_m2k": _required_number(board, "convection_top_w_m2k", "board", nonnegative=True),
        "convection_bottom_w_m2k": _required_number(board, "convection_bottom_w_m2k", "board", nonnegative=True),
        "ambient_c": _required_number(board, "ambient_c", "board"),
    }
    if normalized_board["ambient_c"] <= -273.15:
        raise PcbThermalError("board.ambient_c must be above absolute zero")

    if "grid" not in source:
        raise PcbThermalError("grid is required")
    grid = _object(source["grid"], "grid", {"nx", "ny"})
    nx = _integer(grid.get("nx"), "grid.nx", 1, 96)
    ny = _integer(grid.get("ny"), "grid.ny", 1, 96)
    if nx * ny > 4096:
        raise PcbThermalError("grid may contain at most 4096 cells")

    if "components" not in source or not isinstance(source["components"], list):
        raise PcbThermalError("components must be an array")
    if len(source["components"]) > 128:
        raise PcbThermalError("components may contain at most 128 sources")
    components = []
    ids = set()
    for index, raw in enumerate(source["components"]):
        path = f"components[{index}]"
        component = _object(raw, path, {"id", "x_m", "y_m", "width_m", "height_m", "power_w"})
        identifier = component.get("id")
        if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 80:
            raise PcbThermalError(f"{path}.id must be a nonempty string of at most 80 characters")
        if any(ord(char) < 32 for char in identifier):
            raise PcbThermalError(f"{path}.id must not contain control characters")
        if identifier in ids:
            raise PcbThermalError(f"{path}.id must be unique")
        ids.add(identifier)
        item = {"id": identifier,
                "x_m": _required_number(component, "x_m", path, nonnegative=True),
                "y_m": _required_number(component, "y_m", path, nonnegative=True),
                "width_m": _required_number(component, "width_m", path, positive=True),
                "height_m": _required_number(component, "height_m", path, positive=True),
                "power_w": _required_number(component, "power_w", path, nonnegative=True)}
        if item["x_m"] + item["width_m"] > normalized_board["width_m"] or \
                item["y_m"] + item["height_m"] > normalized_board["height_m"]:
            raise PcbThermalError(f"{path} must lie entirely inside the board")
        components.append(item)

    mode = source.get("mode", "steady")
    if mode not in ("steady", "transient"):
        raise PcbThermalError("mode must be steady or transient")
    normalized_transient = None
    if mode == "steady":
        if "transient" in source:
            raise PcbThermalError("transient is only valid when mode is transient")
        if (normalized_board["convection_top_w_m2k"] +
                normalized_board["convection_bottom_w_m2k"] == 0):
            raise PcbThermalError("steady mode requires positive total convection to anchor temperature")
    else:
        if "transient" not in source:
            raise PcbThermalError("transient is required when mode is transient")
        transient = _object(source["transient"], "transient", {
            "duration_s", "time_step_s", "initial_c", "snapshot_count"})
        duration = _required_number(transient, "duration_s", "transient", positive=True)
        time_step = _required_number(transient, "time_step_s", "transient", positive=True)
        step_count = duration / time_step
        if not math.isfinite(step_count) or step_count > 240:
            raise PcbThermalError("transient requires at most 240 time steps")
        steps = math.ceil(step_count)
        initial_c = _required_number(transient, "initial_c", "transient")
        if initial_c <= -273.15:
            raise PcbThermalError("transient.initial_c must be above absolute zero")
        snapshots = _integer(transient.get("snapshot_count", min(16, steps + 1)),
                             "transient.snapshot_count", 2, 48)
        if snapshots > steps + 1:
            raise PcbThermalError("transient.snapshot_count cannot exceed time steps plus one")
        normalized_transient = {
            "duration_s": duration, "time_step_s": time_step,
            "initial_c": initial_c,
            "snapshot_count": snapshots,
        }
    result = {"schema_version": 1, "title": title, "board": normalized_board,
              "grid": {"nx": nx, "ny": ny}, "components": components, "mode": mode}
    if normalized_transient is not None:
        result["transient"] = normalized_transient
    return result


def _source_power(spec: dict[str, Any], nx: int, ny: int, dx: float, dy: float) -> tuple[list[float], list[str]]:
    power = [0.0] * (nx * ny)
    warnings = []
    for component in spec["components"]:
        x0, y0 = component["x_m"], component["y_m"]
        x1 = x0 + component["width_m"]
        y1 = y0 + component["height_m"]
        if component["width_m"] < 2 * dx or component["height_m"] < 2 * dy:
            warnings.append(f"{component['id']}: footprint spans fewer than two cells in x or y; refine the grid for hotspot estimates")
        overlaps = []
        for j in range(ny):
            oy = max(0.0, min(y1, (j + 1) * dy) - max(y0, j * dy))
            if oy == 0:
                continue
            for i in range(nx):
                ox = max(0.0, min(x1, (i + 1) * dx) - max(x0, i * dx))
                if ox:
                    overlaps.append((j * nx + i, ox * oy))
        area = math.fsum(overlap for _, overlap in overlaps)
        if area <= 0:
            raise PcbThermalError(f"component {component['id']} does not overlap any grid cell")
        for index, overlap in overlaps:
            power[index] += component["power_w"] * overlap / area
    return power, warnings


def _rows(values: list[float], nx: int) -> list[list[float]]:
    return [values[start:start + nx] for start in range(0, len(values), nx)]


def _pcg(rhs: list[float], diagonal: list[float], neighbors: list[list[tuple[int, float]]],
         initial: list[float]) -> tuple[list[float], int, float]:
    """Jacobi-preconditioned CG for the symmetric positive-definite heat matrix."""
    def apply(vector: list[float]) -> list[float]:
        return [diagonal[i] * value - math.fsum(g * vector[j] for j, g in neighbors[i])
                for i, value in enumerate(vector)]

    x = initial[:]
    ax = apply(x)
    residual = [rhs[i] - ax[i] for i in range(len(rhs))]
    norm_rhs = math.sqrt(math.fsum(value * value for value in rhs))
    tolerance = max(1e-10 * norm_rhs, 1e-13)
    norm_residual = math.sqrt(math.fsum(value * value for value in residual))
    if norm_residual <= tolerance:
        return x, 0, norm_residual
    z = [residual[i] / diagonal[i] for i in range(len(rhs))]
    direction = z[:]
    rz = math.fsum(residual[i] * z[i] for i in range(len(rhs)))
    for iteration in range(1, max(200, 2 * len(rhs)) + 1):
        ad = apply(direction)
        denominator = math.fsum(direction[i] * ad[i] for i in range(len(rhs)))
        if denominator <= 0 or not math.isfinite(denominator):
            raise PcbThermalError("thermal matrix is not positive definite")
        alpha = rz / denominator
        x = [x[i] + alpha * direction[i] for i in range(len(rhs))]
        residual = [residual[i] - alpha * ad[i] for i in range(len(rhs))]
        norm_residual = math.sqrt(math.fsum(value * value for value in residual))
        if norm_residual <= tolerance:
            return x, iteration, norm_residual
        z = [residual[i] / diagonal[i] for i in range(len(rhs))]
        new_rz = math.fsum(residual[i] * z[i] for i in range(len(rhs)))
        beta = new_rz / rz
        direction = [z[i] + beta * direction[i] for i in range(len(rhs))]
        rz = new_rz
    raise PcbThermalError(f"thermal linear solve did not converge in {iteration} iterations")


def simulate_pcb_thermal(spec: dict[str, Any]) -> dict[str, Any]:
    """Solve steady or transient temperatures, returning conserved power maps.

    Each control volume obeys ``C dT/dt = sum(G*(T_neighbor-T)) + P - H*(T-T_ambient)``.
    Transients use backward Euler, making positive source and cooling robust to
    time steps beyond an explicit diffusion stability limit.  No-flux board
    edges are implicit in the absence of exterior neighbor connections.
    """
    model = validate_pcb_spec(spec)
    board, grid = model["board"], model["grid"]
    nx, ny = grid["nx"], grid["ny"]
    count = nx * ny
    dx, dy = board["width_m"] / nx, board["height_m"] / ny
    if dx <= 0 or dy <= 0:
        raise PcbThermalError("board dimensions are too small for the selected grid")
    gx = board["thermal_conductivity_x_w_mk"] * board["thickness_m"] * dy / dx
    gy = board["thermal_conductivity_y_w_mk"] * board["thickness_m"] * dx / dy
    conductance_air = (board["convection_top_w_m2k"] +
                       board["convection_bottom_w_m2k"]) * dx * dy
    capacity = (board["density_kg_m3"] * board["specific_heat_j_kgk"] *
                board["thickness_m"] * dx * dy)
    if not all(math.isfinite(value) and value > 0 for value in (dx, dy, gx, gy, capacity)):
        raise PcbThermalError("board/grid parameters produce nonfinite conductance or heat capacity")
    if not math.isfinite(conductance_air):
        raise PcbThermalError("board/grid parameters produce nonfinite convection")

    power, warnings = _source_power(model, nx, ny, dx, dy)
    total_power = math.fsum(power)
    if not math.isfinite(total_power):
        raise PcbThermalError("component power sum is nonfinite")
    neighbors: list[list[tuple[int, float]]] = []
    base_diagonal = []
    for index in range(count):
        i, j = index % nx, index // nx
        adjacent = []
        if i > 0:
            adjacent.append((index - 1, gx))
        if i + 1 < nx:
            adjacent.append((index + 1, gx))
        if j > 0:
            adjacent.append((index - nx, gy))
        if j + 1 < ny:
            adjacent.append((index + nx, gy))
        neighbors.append(adjacent)
        diagonal_value = conductance_air + math.fsum(g for _, g in adjacent)
        if not math.isfinite(diagonal_value):
            raise PcbThermalError("board/grid parameters produce nonfinite matrix entries")
        base_diagonal.append(diagonal_value)

    ambient = board["ambient_c"]
    iterations = 0
    linear_residual = 0.0
    snapshots = []
    time_series = []
    if model["mode"] == "steady":
        theta, iterations, linear_residual = _pcg(power, base_diagonal, neighbors,
                                                  [0.0] * count)
        duration = 0.0
        initial_energy = 0.0
        stored_energy = 0.0
        heat_rejection = conductance_air * math.fsum(theta)
        rejected_energy = 0.0
        energy_balance = total_power - heat_rejection
        temperature = [ambient + value for value in theta]
        snapshots.append({"time_s": 0.0, "temperature_c": _rows(temperature, nx)})
        time_series.append({"time_s": 0.0, "min_temperature_c": min(temperature),
                            "max_temperature_c": max(temperature)})
    else:
        transient = model["transient"]
        duration = transient["duration_s"]
        step = transient["time_step_s"]
        nsteps = math.ceil(duration / step)
        dt = duration / nsteps
        mass = capacity / dt
        if not math.isfinite(mass) or mass <= 0:
            raise PcbThermalError("transient parameters produce nonfinite time-step capacity")
        initial_theta = transient["initial_c"] - ambient
        theta = [initial_theta] * count
        initial_energy = capacity * count * initial_theta
        rejected_energy = 0.0
        initial_temperature = [transient["initial_c"]] * count
        snapshots.append({"time_s": 0.0,
                          "temperature_c": _rows(initial_temperature, nx)})
        time_series.append({"time_s": 0.0, "min_temperature_c": transient["initial_c"],
                            "max_temperature_c": transient["initial_c"]})
        snapshot_indices = {round(k * nsteps / (transient["snapshot_count"] - 1))
                            for k in range(1, transient["snapshot_count"])}
        for index in range(1, nsteps + 1):
            diagonal = [value + mass for value in base_diagonal]
            rhs = [power[i] + mass * theta[i] for i in range(count)]
            theta, used, linear_residual = _pcg(rhs, diagonal, neighbors, theta)
            iterations += used
            elapsed = duration if index == nsteps else index * dt
            rejected_energy += conductance_air * math.fsum(theta) * dt
            temperature = [ambient + value for value in theta]
            time_series.append({"time_s": elapsed,
                                "min_temperature_c": min(temperature),
                                "max_temperature_c": max(temperature)})
            if index in snapshot_indices or index == nsteps:
                snapshots.append({"time_s": elapsed,
                                  "temperature_c": _rows(temperature, nx)})
        stored_energy = capacity * math.fsum(theta) - initial_energy
        heat_rejection = conductance_air * math.fsum(theta)
        energy_balance = total_power * duration - rejected_energy - stored_energy

    result = {
        "schema_version": 1, "title": model["title"], "mode": model["mode"],
        "board": board, "grid": grid, "components": model["components"],
        "duration_s": duration,
        "temperature_c": _rows(temperature, nx),
        "power_w": _rows(power, nx),
        "snapshots": snapshots, "time_series": time_series,
        "min_temperature_c": min(temperature),
        "max_temperature_c": max(temperature),
        "mean_temperature_c": math.fsum(temperature) / count,
        "total_power_w": total_power,
        "heat_rejection_w": heat_rejection,
        "rejected_energy_j": rejected_energy,
        "stored_energy_j": stored_energy,
        "energy_balance": {
            "residual": energy_balance,
            "unit": "W" if model["mode"] == "steady" else "J",
            "interpretation": "input minus convection minus storage",
        },
        "solver": {"method": "finite_volume_backward_euler_pcg",
                   "iterations": iterations,
                   "last_linear_residual_w": linear_residual},
        "warnings": warnings + [
            "Effective in-plane conductivity and convection must be calibrated for the actual board and enclosure; package, trace, via and through-thickness hotspot physics are omitted."
        ],
    }
    if not all(math.isfinite(value) for value in temperature):
        raise PcbThermalError("thermal solve produced nonfinite temperature")
    if model["mode"] == "steady":
        balance_scale = max(abs(total_power), abs(heat_rejection), 1e-12)
    else:
        balance_scale = max(abs(total_power * duration), abs(rejected_energy),
                            abs(stored_energy), 1e-12)
    if not math.isfinite(energy_balance) or abs(energy_balance) > 1e-9 + 1e-7 * balance_scale:
        raise PcbThermalError("thermal energy balance failed verification")
    result["energy_balance"]["passed"] = True
    result["energy_balance"]["relative_error"] = abs(energy_balance) / balance_scale
    return result
