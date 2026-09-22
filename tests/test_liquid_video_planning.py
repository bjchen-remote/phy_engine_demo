"""Public video budgets include continuous liquid surfaces, not just samples."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from physics_demo.io.planning import make_plan
from physics_demo.runner import prepare, validate


ROOT = Path(__file__).resolve().parents[1]


def showcase() -> dict:
    return json.loads((ROOT / "examples" / "liquid_preset_showcase.json").read_text())


class LiquidVideoPlanningTests(unittest.TestCase):
    def test_current_showcase_fits_and_estimate_covers_warm_frame_calibration(self):
        result = prepare(showcase())
        self.assertTrue(result["ready_to_simulate"], result)
        plan = result["plan"]
        timing = plan["timing_estimate"]
        self.assertEqual(plan["output_frames"], 31)
        self.assertEqual(plan["planned_particles"], 1920)
        self.assertEqual(timing["liquid_materials_per_frame"], 4)
        # The measured four-material frame takes 0.153-0.156 s on the target
        # Mac. Bound its scale without asserting the formula's coefficients.
        self.assertGreater(timing["video_p50_s"], 31 * .14)
        self.assertLess(timing["video_p50_s"], 31 * .25)
        self.assertGreater(timing["video_p90_s"], 31 * .156)
        self.assertLess(timing["total_p90_s"], 55)

    def test_long_high_fps_video_is_rejected_despite_render_sample_cap(self):
        scene = showcase()
        scene["world"].update(duration=30, output_fps=60)
        scene["budget"]["wall_time_s"] = 60
        result = prepare(scene)
        self.assertFalse(result["ready_to_simulate"])
        self.assertEqual(result["errors"][0]["code"], "budget_infeasible")
        plan = result["plan"]
        self.assertEqual(plan["output_frames"], 1801)
        self.assertLessEqual(plan["frame_particle_samples"], 600_000)
        self.assertGreater(plan["timing_estimate"]["liquid_surface_p90_s"], 60)
        # Reducing solver samples cannot remove mandatory pixel/material work.
        self.assertEqual(plan["planned_particles"], 1920)
        self.assertEqual(plan["effective_spacing"], .05)

    def test_distinct_materials_cost_more_than_repeated_water_entities(self):
        four = prepare(showcase())["plan"]["timing_estimate"]
        scene = showcase()
        for entity in scene["entities"]:
            entity["preset"] = "water"
        same = prepare(scene)["plan"]["timing_estimate"]
        self.assertEqual(same["liquid_materials_per_frame"], 1)
        self.assertGreater(four["video_p50_s"], 2 * same["video_p50_s"])

    def test_no_video_and_sand_do_not_pay_liquid_surface_cost(self):
        normalized = validate(showcase())["scene"]
        no_video = make_plan(normalized, include_video=False)["timing_estimate"]
        self.assertEqual(no_video["liquid_surface_p90_s"], 0)
        self.assertLess(no_video["video_p90_s"], 1)
        sand = showcase()
        for entity in sand["entities"]:
            entity["type"] = "granular"
            entity.pop("preset")
        prepared = prepare(sand)
        self.assertTrue(prepared["ready_to_simulate"], prepared)
        self.assertEqual(prepared["plan"]["timing_estimate"]["liquid_surface_p50_s"], 0)

    def test_mixed_solver_cannot_bypass_shared_liquid_video_cost(self):
        scene = showcase()
        scene["coupling"] = {}
        result = prepare(scene)
        self.assertTrue(result["ready_to_simulate"], result)
        timing = result["plan"]["timing_estimate"]
        self.assertEqual(result["plan"]["backend"], "coupled")
        self.assertEqual(timing["liquid_materials_per_frame"], 4)
        self.assertGreater(timing["liquid_surface_p50_s"], 31 * .14)
        self.assertGreater(timing["video_p50_s"], timing["liquid_surface_p50_s"])
        long_scene = copy.deepcopy(scene)
        long_scene["world"].update(duration=30, output_fps=60)
        long_scene["budget"]["wall_time_s"] = 60
        long_result = prepare(long_scene)
        self.assertFalse(long_result["ready_to_simulate"])
        self.assertFalse(long_result["plan"]["timing_estimate"]["fits_budget"])
        self.assertGreater(long_result["plan"]["timing_estimate"]["video_p90_s"], 60)


if __name__ == "__main__":
    unittest.main()
