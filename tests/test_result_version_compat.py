"""Versioned results cannot silently change old planning or fidelity claims."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from physics_demo.analysis.results import (
    _LEGACY_FLUID_CLAIM,
    _validated_result_scene,
)
from physics_demo.io.jsonio import canonical_bytes
from physics_demo.io.planning import make_plan
from physics_demo.io.schema import normalize_and_validate
from physics_demo.runner import inspect, simulate


ROOT = Path(__file__).resolve().parents[1]


def example(name: str) -> dict:
    return json.loads((ROOT / "examples" / f"{name}.json").read_text(encoding="utf-8"))


class ResultVersionCompatibilityTests(unittest.TestCase):
    def test_pre_v1_nonfluid_result_uses_the_legacy_global_fluid_claim(self):
        scene = example("three_body")
        scene["world"].update(duration=0.01, output_fps=10)
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(simulate(scene, directory, make_video=False)["ok"])
            result_path = Path(directory) / "result.json"
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result.pop("result_version")
            result["physics_claims"]["fluid"] = _LEGACY_FLUID_CLAIM
            result["plan"] = make_plan(
                result["scene"],
                include_video=False,
                legacy_video_estimate=True,
            )
            result_path.write_text(json.dumps(result), encoding="utf-8")

            inspected = inspect(result_path)

        self.assertTrue(inspected["ok"], inspected)
        self.assertEqual(inspected["result_version"], 0)
        self.assertEqual(inspected["fidelity"]["fluid"], _LEGACY_FLUID_CLAIM)

    def test_missing_version_cannot_downgrade_a_v1_liquid_scene(self):
        report = normalize_and_validate(example("droplet_ground"))
        self.assertTrue(report["valid"], report)
        scene = report["scene"]
        self.assertIn("preset", scene["entities"][0])
        result = {
            "scene": scene,
            "scene_hash": hashlib.sha256(canonical_bytes(scene)).hexdigest(),
        }

        with self.assertRaisesRegex(ValueError, "unversioned legacy result"):
            _validated_result_scene(result)

    def test_present_result_version_is_strictly_integer_one(self):
        report = normalize_and_validate(example("three_body"))
        self.assertTrue(report["valid"], report)
        scene = report["scene"]
        base = {
            "scene": scene,
            "scene_hash": hashlib.sha256(canonical_bytes(scene)).hexdigest(),
        }

        for value in (None, False, 0, 2, "1"):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError, "result_version must be 1 or absent"
            ):
                _validated_result_scene({**base, "result_version": value})

    def test_coupled_legacy_video_plan_keeps_v08_resolution(self):
        scene = example("water_spring_reaction")
        scene["world"].update(duration=2, output_fps=30)
        scene["budget"]["wall_time_s"] = 8
        report = normalize_and_validate(scene)
        self.assertTrue(report["valid"], report)

        plan = make_plan(
            report["scene"],
            include_video=True,
            legacy_video_estimate=True,
        )

        self.assertEqual(plan["planned_particles"], 179)
        self.assertEqual(plan["effective_spacing"], 0.06)
        self.assertNotIn("liquid_surface_p90_s", plan["timing_estimate"])


if __name__ == "__main__":
    unittest.main()
