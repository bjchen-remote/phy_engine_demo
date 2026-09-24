"""Public scenario metadata must disclose decisive mechanism substitutions."""

from __future__ import annotations

import unittest

from physics_demo.api import call_tool


class ScenarioBoundaryTests(unittest.TestCase):
    def test_catalog_and_example_expose_consistent_mechanism_boundaries(self):
        capabilities = call_tool("physics_capabilities", {})
        self.assertTrue(capabilities["ok"], capabilities)
        catalog = {item["name"]: item for item in capabilities["examples"]}
        for name, item in catalog.items():
            if "mechanism_boundary" not in item:
                continue
            with self.subTest(example=name):
                boundary = item["mechanism_boundary"]
                self.assertEqual(set(boundary), {"modeled", "unavailable"})
                modeled, unavailable = map(set, (boundary["modeled"], boundary["unavailable"]))
                self.assertTrue(modeled)
                self.assertTrue(unavailable)
                self.assertFalse(modeled & unavailable)
                self.assertTrue(all(isinstance(tag, str) and tag for tag in modeled | unavailable))

                example = call_tool("physics_example", {"name": name})
                self.assertTrue(example["ok"], example)
                self.assertEqual(example["mechanism_boundary"], boundary)
                self.assertIn("mechanism_boundary", example["next_action"]["decision"])

    def test_high_risk_scenario_substitutions_are_explicit(self):
        cases = {
            "product_pour_tumbler": "continuous_inflow",
            "fountain_hoop": "nozzle_pressure",
            "geyser": "continuous_inflow",
            "flood_bridge_piers": "structural_load",
            "vehicle_wading_splash": "moving_vehicle",
            "sand_sculpture_wave": "calibrated_erosion",
            "liquid_preset_showcase": "phase_change",
            "self_gravitating_liquid": "particle_point_mass_gravity_exchange",
        }
        catalog = {item["name"]: item for item in call_tool("physics_capabilities", {})["examples"]}
        for name, requirement in cases.items():
            with self.subTest(example=name, requirement=requirement):
                self.assertIn(requirement, catalog[name]["mechanism_boundary"]["unavailable"])


if __name__ == "__main__":
    unittest.main()
