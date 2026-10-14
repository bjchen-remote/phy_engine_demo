"""Public scene inputs cannot grant themselves the host's unlimited runtime."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = PROJECT_ROOT / "toolboxes" / "physics" / "physics_release"
sys.path.insert(0, str(PACKAGE))

from physics_demo.analysis.results import _validated_result_scene
from physics_demo.api import call_tool
from physics_demo.jsonio import canonical_bytes
from physics_demo.limits import NO_DEADLINE_WALL_TIME_S
from physics_demo.runner import patch_scene, prepare, validate


def fixture() -> dict:
    scene_path = PACKAGE / "scenes" / "three_body.json"
    scene = json.loads(scene_path.read_text())
    scene["world"]["duration"] = 1.0
    return scene


class UnlimitedAuthorizationTests(unittest.TestCase):
    def test_scene_supplied_unlimited_marker_is_rejected_at_all_public_entries(self):
        scene = fixture()
        scene["budget"].update(wall_time_s=NO_DEADLINE_WALL_TIME_S,
                               unlimited_runtime=True)
        for result in (
            validate(scene),
            prepare(scene),
            patch_scene(scene, []),
            call_tool("physics_validate", {"scene_json": json.dumps(scene)}),
            call_tool("physics_prepare", {"scene_json": json.dumps(scene)}),
            call_tool("physics_patch", {"scene_json": json.dumps(scene), "operations": []}),
        ):
            self.assertFalse(result["ok"])
            self.assertIn("unlimited_runtime_authorization",
                          {error["code"] for error in result.get("errors", [])})

    def test_host_flag_prepares_and_patches_unlimited_scene(self):
        prepared = prepare(fixture(), unlimited=True)
        self.assertTrue(prepared["ok"], prepared.get("errors"))
        scene = prepared["scene"]
        self.assertEqual(scene["budget"]["wall_time_s"], NO_DEADLINE_WALL_TIME_S)
        self.assertIs(scene["budget"]["unlimited_runtime"], True)
        self.assertTrue(validate(scene, unlimited=True)["ok"])
        self.assertTrue(patch_scene(scene, [], unlimited=True)["ok"])
        via_api = call_tool("physics_validate", {"scene_json": json.dumps(scene)},
                            unlimited=True)
        self.assertTrue(via_api["ok"], via_api.get("errors"))

        result = {"scene": scene,
                  "scene_hash": hashlib.sha256(canonical_bytes(scene)).hexdigest()}
        self.assertEqual(_validated_result_scene(result)[0], scene)

    def test_finite_budget_and_physical_duration_bound_remain(self):
        scene = fixture()
        scene["world"]["duration"] = 60.0
        self.assertTrue(validate(scene)["ok"])
        scene["world"]["duration"] = 60.1
        over_duration = validate(scene, unlimited=True)
        self.assertFalse(over_duration["ok"])
        self.assertIn("duration_range", {error["code"] for error in over_duration["errors"]})

        finite = fixture()
        finite["budget"]["wall_time_s"] = 300.0
        self.assertTrue(validate(finite)["ok"])
        finite["budget"]["wall_time_s"] = 301.0
        over_budget = validate(finite)
        self.assertFalse(over_budget["ok"])
        self.assertIn("budget_range", {error["code"] for error in over_budget["errors"]})

        finite["budget"].update(wall_time_s=30.0, unlimited_runtime=True)
        marker_without_host = validate(finite)
        self.assertFalse(marker_without_host["ok"])
        self.assertIn("unlimited_runtime_authorization",
                      {error["code"] for error in marker_without_host["errors"]})


if __name__ == "__main__":
    unittest.main()
