"""PCB result queries must remain safe at numerical coordinate boundaries."""

import json
from pathlib import Path
import sys
import tempfile
import unittest


TOOLBOX = Path(__file__).resolve().parents[1] / "physics"
sys.path.insert(0, str(TOOLBOX))
from modeling import api_call


class PcbQueryTests(unittest.TestCase):
    def test_right_edge_rounding_selects_last_cell_and_huge_integer_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder)
            (job / "work/pcb").mkdir(parents=True)
            saved = {
                "mode": "steady", "duration_s": 0.0,
                "board": {"width_m": 1e20, "height_m": 0.1},
                "grid": {"nx": 2, "ny": 1},
                "temperature_c": [[26.0, 30.0]], "power_w": [[0.0, 1.0]],
                "min_temperature_c": 26.0, "max_temperature_c": 30.0,
                "mean_temperature_c": 28.0, "total_power_w": 1.0,
                "heat_rejection_w": 1.0, "rejected_energy_j": 0.0,
                "stored_energy_j": 0.0, "energy_balance": {"residual": 0.0},
                "solver": {"iterations": 1}, "warnings": [],
            }
            (job / "work/pcb/result.json").write_text(json.dumps(saved))
            request = {"operation": "pcb_query", "arguments": {
                "x_m": int(1e20) - 1, "y_m": 0.05}}
            (job / "work/toolbox-call.json").write_text(json.dumps(request))
            api_call(TOOLBOX, job, {})
            response = json.loads((job / "work/toolbox-response.json").read_text())
            self.assertEqual(response["result"]["sample"]["cell_x"], 1)
            self.assertEqual(response["result"]["sample"]["temperature_c"], 30.0)
            self.assertEqual(response["result"]["report"]["rejected_energy_j"], 0.0)

            request["arguments"]["x_m"] = 10**400
            (job / "work/toolbox-call.json").write_text(json.dumps(request))
            with self.assertRaisesRegex(ValueError, "outside the board"):
                api_call(TOOLBOX, job, {})


if __name__ == "__main__":
    unittest.main()
