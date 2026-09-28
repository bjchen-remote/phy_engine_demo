"""Synthetic native counter boundaries and geometry refresh under UBSan."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from physics_demo import coupled_solver
from physics_demo.native_backend import _compiler


class CoupledContactCounterTests(unittest.TestCase):
    def test_uint64_contacts_preserve_refresh_and_fail_before_overflow(self):
        native = Path(coupled_solver.__file__).with_name("native")
        source = Path(__file__).with_name("coupled_contact_counter.c")
        with tempfile.TemporaryDirectory(prefix="coupled-contact-counter-") as work:
            executable = Path(work) / "counter"
            subprocess.run([
                _compiler(), "-std=c11", "-O1", "-ffp-contract=off",
                "-fsanitize=undefined", "-fno-sanitize-recover=all", "-pthread",
                "-I", str(native), str(source),
                str(native / "physics_native.c"), str(native / "mesh_native.c"),
                "-lm", "-o", str(executable),
            ], check=True, capture_output=True)
            result = subprocess.run([str(executable)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("runtime error:", result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["abi"], coupled_solver.COUPLED_ABI_VERSION)
        self.assertTrue(report["counter_tests_passed"])


if __name__ == "__main__":
    unittest.main()
