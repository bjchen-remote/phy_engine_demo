"""The cached native grid preserves ordered neighbor lists and safety guards."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

from physics_demo.core.native_backend import _compiler


class NeighborGridCacheTests(unittest.TestCase):
    def test_sparse_dense_collision_rebuild_and_limits(self):
        source = Path(__file__).resolve().parents[1] / "benchmarks" / "benchmark_neighbor_grid.c"
        with tempfile.TemporaryDirectory(prefix="physics-neighbor-grid-") as directory:
            executable = Path(directory) / "neighbor-grid-check"
            subprocess.run(
                [_compiler(), "-std=c11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
                 "-fsanitize=address,undefined,float-cast-overflow", "-fno-omit-frame-pointer",
                 "-pthread", str(source), "-lm", "-o", str(executable)],
                check=True, capture_output=True, text=True, timeout=30,
            )
            result = subprocess.run(
                [str(executable), "--check"], capture_output=True, text=True, timeout=10,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("neighbor grid checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
