"""Recovery rejects damaged presentation frames even when the MP4 is valid."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from physics_demo.api import call_tool
from physics_demo.results import _summary
from physics_demo.runner import inspect, load_scene, query, simulate


ROOT = Path(__file__).resolve().parents[1]


def mixed_scene(backend="python"):
    return {
        "world": {"duration": 0.12, "dt": 0.01, "output_fps": 20, "gravity": [0, 0, 0]},
        "budget": {"wall_time_s": 30, "backend": backend},
        "entities": [
            {"id": "body", "type": "point_mass", "position": [-1, 1, 0]},
            {"id": "drop", "type": "fluid", "spacing": 0.1,
             "shape": {"type": "sphere", "center": [0, 1, 0], "radius": 0.15}},
            {"id": "ball", "type": "rigid", "mass": 0, "position": [1, 1, 0],
             "shape": {"type": "sphere", "radius": 0.2}},
        ],
        "queries": [{"id": "height", "type": "series",
                     "metric": {"type": "centroid", "entity": "body", "axis": "y"}}],
    }


class SavedFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.root = Path(cls.directory.name)
        response = simulate(mixed_scene(), cls.root, make_video=False)
        if not response["ok"]:
            raise AssertionError(response)
        cls.original = json.loads((cls.root / "result.json").read_text())

    def setUp(self):
        (self.root / "result.json").write_text(json.dumps(self.original))

    def test_saved_frames_reject_wrong_objects_schedule_and_populations(self):
        mutations = [
            lambda frame: None,
            lambda frame: [],
            lambda frame: {**frame, "t": -1000},
            lambda frame: {**frame, "t": True},
            lambda frame: {**frame, "t": 0.11},
            lambda frame: {**frame, "m": [[0, 0, 0]]},
        ]
        for channel in ("p", "g", "r"):
            for value in (None, [], [[0, 0]], [[True, 0, 0]], [[0, 0, 10 ** 500]]):
                mutations.append(lambda frame, channel=channel, value=value: {**frame, channel: value})
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                document = copy.deepcopy(self.original)
                document["trajectory"]["frames"][-1] = mutate(document["trajectory"]["frames"][-1])
                (self.root / "result.json").write_text(json.dumps(document))
                checked = inspect(self.root)
                self.assertFalse(checked["ok"], checked)
                self.assertEqual(checked["errors"][0]["code"], "invalid_result_contract")
                self.assertFalse(query(self.root, "height")["ok"])
        for index in (0, 1):
            document = copy.deepcopy(self.original)
            document["trajectory"]["frames"][index]["t"] += 0.001
            with self.subTest(frame=index), self.assertRaises(ValueError):
                _summary(document, self.root)
        for channel in ("p", "g", "r"):
            document = copy.deepcopy(self.original)
            document["trajectory"]["frames"][-1][channel][0][0] = float("inf")
            with self.subTest(channel=channel), self.assertRaises(ValueError):
                _summary(document, self.root)

    def test_missing_completion_is_a_structured_recovery_failure(self):
        path = self.root / "completion.json"
        original = path.read_bytes()
        path.unlink()
        try:
            responses = (inspect(self.root), query(self.root),
                         call_tool("physics_inspect", {"result_path": str(self.root)}))
            for response in responses:
                self.assertFalse(response["ok"], response)
                self.assertEqual(response["stage"], "inspect")
                self.assertEqual(response["errors"][0]["code"], "invalid_result_contract")
            cli = subprocess.run([sys.executable, "-m", "physics_demo", "inspect", str(self.root)],
                                 cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(cli.returncode, 2, cli.stderr)
            self.assertEqual(json.loads(cli.stdout)["stage"], "inspect")
            self.assertEqual(cli.stderr, "")
        finally:
            path.write_bytes(original)

    def test_normal_native_python_and_slider_runs_recover(self):
        self.assertTrue(inspect(self.root)["ok"])
        slider = load_scene(ROOT / "examples" / "sliders_separate.json")
        slider["world"].update(duration=0.12, output_fps=20)
        for scene in (mixed_scene("native"), slider):
            with tempfile.TemporaryDirectory() as directory:
                response = simulate(scene, directory, make_video=False)
                self.assertTrue(response["ok"], response)
                self.assertTrue(inspect(directory)["ok"])

    @unittest.skipUnless(sys.platform == "darwin" and shutil.which("clang"), "macOS renderer")
    def test_valid_mp4_cannot_hide_damaged_trajectory_frames(self):
        scene = mixed_scene("native")
        scene["entities"] = scene["entities"][:1]
        with tempfile.TemporaryDirectory() as directory:
            response = simulate(scene, directory)
            self.assertTrue(response["ok"], response)
            self.assertTrue(inspect(directory)["ok"])
            root = Path(directory)
            path = root / "result.json"
            original = json.loads(path.read_text())
            video = (root / "simulation.mp4").read_bytes()
            for frame in (None, {**original["trajectory"]["frames"][-1], "g": []}):
                document = copy.deepcopy(original)
                document["trajectory"]["frames"][-1] = frame
                path.write_text(json.dumps(document))
                checked = call_tool("physics_inspect", {"result_path": directory})
                self.assertFalse(checked["ok"], checked)
                self.assertEqual(checked["status"], "inspection_failed")
                self.assertEqual(checked["errors"][0]["code"], "invalid_result_contract")
                self.assertEqual((root / "simulation.mp4").read_bytes(), video)


if __name__ == "__main__":
    unittest.main()
