"""Named native liquids never suggest an impossible legacy-rigid backend."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from physics_demo.runner import prepare


ROOT = Path(__file__).resolve().parents[1]


class LiquidRigidRoutingTests(unittest.TestCase):
    def test_non_water_dynamic_legacy_sphere_has_one_actionable_error(self):
        for preset in ("honey", "glue", "molten_lead"):
            for backend in ("auto", "native", "python"):
                with self.subTest(preset=preset, backend=backend):
                    scene = json.loads(
                        (ROOT / "examples" / "droplet_ground.json").read_text()
                    )
                    scene["entities"][0]["preset"] = preset
                    scene["entities"].append({
                        "id": "moving-ball",
                        "type": "rigid",
                        "mass": 1.0,
                        "shape": {"type": "sphere", "radius": 0.1},
                        "position": [0.8, 0.5, 0.0],
                        "velocity": [0.0, 0.0, 0.0],
                    })
                    scene["budget"]["backend"] = backend

                    result = prepare(scene)

                    self.assertFalse(result["ok"], result)
                    self.assertFalse(result["ready_to_simulate"], result)
                    self.assertEqual(
                        [error["code"] for error in result["errors"]],
                        ["unsupported_liquid_dynamic_rigid_combination"],
                    )
                    error = result["errors"][0]
                    self.assertEqual(error["path"], "entities")
                    self.assertEqual(
                        error["suggestion"],
                        "Make the legacy rigid sphere a static obstacle with mass 0, "
                        "or remodel it as rigid_body and enable coupling.",
                    )


if __name__ == "__main__":
    unittest.main()
