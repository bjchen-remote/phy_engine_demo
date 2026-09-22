"""Independent analytic and invariant tests of the shared C rigid-body kernel."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RigidMathTests(unittest.TestCase):
    def test_analytic_invariants_convergence_and_impulses(self):
        compiler = shutil.which("clang") or shutil.which("cc")
        if compiler is None:
            self.skipTest("C11 compiler unavailable")
        with tempfile.TemporaryDirectory(prefix="rigid-math-") as directory:
            binary = Path(directory) / "rigid_math_test"
            built = subprocess.run(
                [compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Wpedantic",
                 "-Werror", str(ROOT / "tests" / "rigid_math_test.c"), "-lm",
                 "-o", str(binary)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            tested = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)
            for case in ("quaternion_math", "free_sphere", "symmetric_top", "asymmetric_top",
                         "constant_torque", "gravity_precession", "impulse_response", "input_safety"):
                self.assertIn("PASS " + case, tested.stdout)

    @unittest.skipUnless(shutil.which("clang"), "Clang sanitizer runtime unavailable")
    def test_address_and_undefined_sanitizers(self):
        with tempfile.TemporaryDirectory(prefix="rigid-math-sanitize-") as directory:
            binary = Path(directory) / "rigid_math_test"
            built = subprocess.run(
                [shutil.which("clang"), "-std=c11", "-O1", "-g", "-Wall", "-Wextra",
                 "-Wpedantic", "-Werror", "-fsanitize=address,undefined",
                 "-fno-omit-frame-pointer", str(ROOT / "tests" / "rigid_math_test.c"),
                 "-lm", "-o", str(binary)],
                capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            env = os.environ.copy()
            env["ASAN_OPTIONS"] = "detect_leaks=0:halt_on_error=1"
            env["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
            tested = subprocess.run([str(binary)], capture_output=True, text=True,
                                    env=env, timeout=30)
            self.assertEqual(tested.returncode, 0, tested.stdout + tested.stderr)


if __name__ == "__main__":
    unittest.main()
