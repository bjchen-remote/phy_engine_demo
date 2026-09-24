"""Focused delivery checks for the independent PCB heatmap renderer."""

from pathlib import Path
import sys
import tempfile
import unittest


TOOLBOX = Path(__file__).resolve().parents[1] / "physics"
sys.path.insert(0, str(TOOLBOX))
from pcb_video import PcbVideoError, render_pcb_video


def _sample_result():
    return {
        "board": {"width_m": 0.04, "height_m": 0.03},
        "grid": {"nx": 4, "ny": 3},
        "components": [{"id": "U1", "x_m": 0.012, "y_m": 0.01,
                        "width_m": 0.008, "height_m": 0.006, "power_w": 1.2}],
        "power_w": [[0.0, 0.0, 0.0, 0.0],
                    [0.0, 0.3, 0.3, 0.0],
                    [0.0, 0.3, 0.3, 0.0]],
        "snapshots": [
            {"time_s": 0.0, "temperature_c": [[25.0] * 4 for _ in range(3)]},
            {"time_s": 2.0, "temperature_c": [[27.0, 31.0, 30.0, 26.0],
                                                   [28.0, 42.0, 40.0, 27.0],
                                                   [26.0, 33.0, 32.0, 26.0]]},
        ],
        "total_power_w": 1.2,
    }


class PcbVideoTests(unittest.TestCase):
    def test_real_video_has_all_decoded_frames(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "thermal.mp4"
            metadata = render_pcb_video(_sample_result(), {}, output,
                                        max_bytes=2_000_000, timeout_seconds=45)
            self.assertTrue(metadata["decode_verified"])
            self.assertTrue(metadata["all_frames_decoded"])
            self.assertEqual(metadata["frame_count"], 24)
            self.assertEqual(metadata["duration_s"], 3.0)
            self.assertEqual(output.stat().st_size, metadata["bytes"])
            self.assertEqual(output.read_bytes()[4:8], b"ftyp")

    def test_invalid_grid_and_tiny_byte_budget_do_not_publish_video(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "thermal.mp4"
            bad = _sample_result()
            bad["snapshots"][1]["temperature_c"][0][0] = float("nan")
            with self.assertRaises(PcbVideoError):
                render_pcb_video(bad, {}, output,
                                 max_bytes=2_000_000, timeout_seconds=45)
            self.assertFalse(output.exists())
            with self.assertRaises(PcbVideoError):
                render_pcb_video(_sample_result(), {}, output,
                                 max_bytes=1024, timeout_seconds=45)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
