"""Analytic and conservation checks for the standalone board thermal model."""

from __future__ import annotations

import copy
import math
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "toolboxes" / "physics"))
from pcb_thermal import PcbThermalError, simulate_pcb_thermal, validate_pcb_spec


def example(nx: int = 1, ny: int = 1) -> dict:
    return {
        "schema_version": 1,
        "title": "Board thermal test",
        "board": {
            "width_m": 0.1, "height_m": 0.1, "thickness_m": 0.001,
            "thermal_conductivity_w_mk": 10.0,
            "density_kg_m3": 2000.0, "specific_heat_j_kgk": 1000.0,
            "convection_top_w_m2k": 5.0,
            "convection_bottom_w_m2k": 5.0,
            "ambient_c": 25.0,
        },
        "grid": {"nx": nx, "ny": ny},
        "components": [{"id": "U1", "x_m": 0.04, "y_m": 0.04,
                        "width_m": 0.02, "height_m": 0.02, "power_w": 1.0}],
        "mode": "steady",
    }


class PcbThermalTests(unittest.TestCase):
    def test_one_cell_steady_analytical_balance(self):
        result = simulate_pcb_thermal(example())
        # H = (5 + 5) W/m^2/K * 0.01 m^2 = 0.1 W/K.
        self.assertAlmostEqual(result["temperature_c"][0][0], 35.0, places=11)
        self.assertAlmostEqual(result["total_power_w"], 1.0, places=12)
        self.assertAlmostEqual(result["heat_rejection_w"], 1.0, places=11)
        self.assertLess(abs(result["energy_balance"]["residual"]), 1e-11)

    def test_two_cell_conduction_matches_exact_resistor_network(self):
        spec = example(2, 1)
        spec["components"][0].update({"x_m": 0.01, "y_m": 0.04,
                                       "width_m": 0.02, "height_m": 0.02})
        result = simulate_pcb_thermal(spec)
        # Cell-to-cell conductance G=0.02 W/K; ambient loss H=0.05 W/K
        # for each cell. Solve (H+G)*dT_left-G*dT_right=1 and
        # (H+G)*dT_right-G*dT_left=0.
        self.assertAlmostEqual(result["temperature_c"][0][0],
                               25.0 + 140.0 / 9.0, places=9)
        self.assertAlmostEqual(result["temperature_c"][0][1],
                               25.0 + 40.0 / 9.0, places=9)

    def test_one_cell_transient_backward_euler_and_energy(self):
        spec = example()
        spec["mode"] = "transient"
        spec["transient"] = {"duration_s": 2.0, "time_step_s": 2.0,
                             "initial_c": 25.0, "snapshot_count": 2}
        result = simulate_pcb_thermal(spec)
        # C = rho*c*t*A = 20 J/K; H = 0.1 W/K.
        expected = 25.0 + 2.0 / (20.0 + 0.1 * 2.0)
        self.assertAlmostEqual(result["temperature_c"][0][0], expected, places=11)
        self.assertEqual(len(result["snapshots"]), 2)
        self.assertEqual(result["snapshots"][0]["time_s"], 0.0)
        self.assertEqual(result["snapshots"][-1]["time_s"], 2.0)
        self.assertAlmostEqual(result["rejected_energy_j"],
                               0.1 * (expected - 25.0) * 2.0, places=11)
        self.assertAlmostEqual(result["stored_energy_j"] +
                               result["rejected_energy_j"], 2.0, places=10)
        self.assertLess(abs(result["energy_balance"]["residual"]), 1e-10)

    def test_insulated_transient_stores_all_input_energy(self):
        spec = example(4, 4)
        spec["board"]["convection_top_w_m2k"] = 0.0
        spec["board"]["convection_bottom_w_m2k"] = 0.0
        spec["mode"] = "transient"
        spec["transient"] = {"duration_s": 10.0, "time_step_s": 2.0,
                             "initial_c": 25.0}
        result = simulate_pcb_thermal(spec)
        self.assertAlmostEqual(result["stored_energy_j"], 10.0, places=9)
        self.assertAlmostEqual(result["heat_rejection_w"], 0.0)
        self.assertTrue(result["energy_balance"]["passed"])
        self.assertLess(abs(result["energy_balance"]["residual"]), 1e-9)

    def test_rectangular_source_conserves_power_and_symmetry(self):
        spec = example(12, 12)
        result = simulate_pcb_thermal(spec)
        power = result["power_w"]
        temperature = result["temperature_c"]
        self.assertAlmostEqual(math.fsum(map(math.fsum, power)), 1.0, places=12)
        self.assertGreater(temperature[5][5], temperature[0][0])
        for j in range(12):
            for i in range(12):
                self.assertAlmostEqual(temperature[j][i],
                                       temperature[11 - j][11 - i], places=8)
        self.assertLess(abs(result["energy_balance"]["residual"]), 1e-9)

    def test_anisotropy_affects_horizontal_and_vertical_heat_spread(self):
        spec = example(15, 15)
        spec["board"].pop("thermal_conductivity_w_mk")
        spec["board"].update({"thermal_conductivity_x_w_mk": 80.0,
                              "thermal_conductivity_y_w_mk": 2.0})
        spec["components"][0].update({"x_m": 0.046, "y_m": 0.046,
                                       "width_m": 0.008, "height_m": 0.008})
        result = simulate_pcb_thermal(spec)
        rise_x = result["temperature_c"][7][10] - 25.0
        rise_y = result["temperature_c"][10][7] - 25.0
        self.assertGreater(rise_x, rise_y)

    def test_time_and_grid_refinement_reduce_observed_error(self):
        exact = 25.0 + 10.0 * (1.0 - math.exp(-0.1))
        time_errors = []
        for step in (10.0, 5.0, 2.5):
            spec = example()
            spec['mode'] = 'transient'
            spec['transient'] = {'duration_s':20.0, 'time_step_s':step,
                                 'initial_c':25.0}
            result = simulate_pcb_thermal(spec)
            time_errors.append(abs(result['max_temperature_c'] - exact))
        self.assertGreater(time_errors[0], time_errors[1])
        self.assertGreater(time_errors[1], time_errors[2])

        peaks = [simulate_pcb_thermal(example(n, n))['max_temperature_c']
                 for n in (8, 16, 32, 64)]
        changes = [abs(right - left) for left, right in zip(peaks, peaks[1:])]
        self.assertGreater(changes[0], changes[1])
        self.assertGreater(changes[1], changes[2])

    def test_rejects_unanchored_steady_and_invalid_inputs(self):
        spec = example()
        spec["board"]["convection_top_w_m2k"] = 0
        spec["board"]["convection_bottom_w_m2k"] = 0
        with self.assertRaisesRegex(PcbThermalError, "positive total convection"):
            validate_pcb_spec(spec)
        spec = example()
        spec["components"][0]["x_m"] = 0.09
        with self.assertRaisesRegex(PcbThermalError, "inside the board"):
            validate_pcb_spec(spec)
        spec = example()
        spec["components"][0]["power_w"] = float("nan")
        with self.assertRaisesRegex(PcbThermalError, "finite number"):
            validate_pcb_spec(spec)
        spec = example()
        spec["grid"]["nx"] = True
        with self.assertRaisesRegex(PcbThermalError, "integer"):
            validate_pcb_spec(spec)
        spec = example()
        spec['mode'] = 'transient'
        spec['transient'] = {'duration_s':1.0, 'time_step_s':1.0,
                             'initial_c':25.0, 'snapshot_count':3}
        with self.assertRaisesRegex(PcbThermalError, 'snapshot_count'):
            validate_pcb_spec(spec)

    def test_input_is_not_mutated_and_normalization_is_idempotent(self):
        spec = example(2, 2)
        copy_spec = copy.deepcopy(spec)
        normalized = validate_pcb_spec(spec)
        self.assertEqual(spec, copy_spec)
        self.assertEqual(normalized, validate_pcb_spec(normalized))


if __name__ == "__main__":
    unittest.main()
