"""The QQ toolbox guidance must match the task-confined execution API."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from physics_demo.agent_contract import guide_tool_result
from physics_demo.api import call_tool


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "toolboxes" / "physics"))
import modeling  # noqa: E402


class WeakAgentToolboxContractTests(unittest.TestCase):
    def test_ground_example_does_not_direct_agent_to_reapply_water(self):
        example = call_tool("physics_example", {"name": "droplet_ground"})
        self.assertTrue(example["ok"])
        self.assertEqual(example["scene"]["entities"][0]["preset"], "water")
        self.assertEqual(example["scene"]["entities"][0]["properties"]["surface_tension"], 0.5)
        guidance = example["next_action"]
        self.assertIn("preserve", guidance["decision"].lower())
        liquid = next(option for option in guidance["options"] if option["tool"] == "physics_liquid")
        self.assertIn("changes", liquid["when"])
        self.assertNotIn("prompt names water", liquid["when"])
        self.assertEqual(
            next(option for option in guidance["options"] if option["tool"] == "physics_prepare")["tool"],
            "physics_prepare",
        )

    def test_estimate_alone_cannot_advance_to_host_execution(self):
        estimate = guide_tool_result(
            "physics_estimate",
            {"ok": True, "plan": {"timing_estimate": {"fits_budget": True}}},
        )
        self.assertEqual(estimate["next_action"]["tool"], "physics_prepare")

    def test_host_help_describes_only_host_accepted_arguments(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder)
            (job / "work").mkdir()
            (job / "work" / "toolbox-call.json").write_text(
                json.dumps({"operation": "help", "arguments": {"topic": "tools"}})
            )
            modeling.api_call(
                ROOT / "toolboxes" / "physics",
                job,
                {"limits": {"wall_time_seconds": 180}},
            )
            result = json.loads((job / "work" / "toolbox-response.json").read_text())["result"]
            self.assertTrue(result["ok"])
            definitions = {item["name"]: item for item in result["tool_definitions"]}
            routing = definitions["physics_example"]["description"]
            for name in (
                "droplet_ground_splash",
                "droplet_ground",
                "droplet_ground_micro_wet",
                "droplet_cone",
            ):
                self.assertIn(name, routing)
            self.assertIn("preserve an example fluid", definitions["physics_liquid"]["description"])
            self.assertEqual(definitions["physics_simulate"]["input_schema"]["required"], [])
            self.assertEqual(
                set(definitions["physics_simulate"]["input_schema"]["properties"]),
                {"scene_json"},
            )
            self.assertIn("ready_to_run, not an MP4", definitions["physics_simulate"]["description"])
            self.assertEqual(definitions["physics_inspect"]["input_schema"]["properties"], {})
            self.assertEqual(
                set(definitions["physics_query"]["input_schema"]["properties"]),
                {"query_id"},
            )
            self.assertNotIn(
                "budget_seconds",
                definitions["physics_prepare"]["input_schema"]["properties"],
            )
            self.assertEqual(json.loads(result["text"]), result["tool_definitions"])

    def test_host_accepts_empty_simulate_arguments_only_after_prepare(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder)
            (job / "work").mkdir()
            root = ROOT / "toolboxes" / "physics"
            task = {"limits": {"wall_time_seconds": 180}}

            def call(operation, arguments):
                (job / "work" / "toolbox-call.json").write_text(
                    json.dumps({"operation": operation, "arguments": arguments})
                )
                modeling.api_call(root, job, task)
                return json.loads((job / "work" / "toolbox-response.json").read_text())["result"]

            system = call("physics_system", {
                "spec_json": json.dumps({
                    "type": "double_pendulum",
                    "lengths": [1, 1],
                    "masses": [1, 1],
                    "angles": [0.3, 0.4],
                    "duration": 1,
                })
            })
            prepared = call("physics_prepare", {"scene_json": system["scene_json"]})
            self.assertTrue(prepared["ready_to_simulate"], prepared)
            authorized = call("physics_simulate", {})
            self.assertEqual(authorized, {"ok": True, "ready_to_run": True})
            self.assertFalse((job / "artifacts" / "simulation.mp4").exists())


if __name__ == "__main__":
    unittest.main()
