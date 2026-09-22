"""Persistent mesh integration and conservative cross-domain contact checks."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MeshContextTests(unittest.TestCase):
    def test_persistent_context_and_two_way_contact(self):
        compiler = shutil.which("clang") or shutil.which("cc")
        if not compiler:
            self.skipTest("C11 compiler unavailable")
        with tempfile.TemporaryDirectory(prefix="mesh-context-") as directory:
            for sanitizer in (False, True):
                if sanitizer and not shutil.which("clang"):
                    continue
                with self.subTest(sanitizer=sanitizer):
                    binary = Path(directory) / ("sanitized" if sanitizer else "regular")
                    command = [compiler, "-std=c11", "-O1", "-g", "-Wall", "-Wextra",
                               "-Wpedantic", "-Werror"]
                    if sanitizer:
                        command += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
                    command += [str(ROOT / "tests" / "mesh_context_test.c"),
                                str(ROOT / "physics_demo" / "core" / "native" / "mesh_native.c"),
                                "-lm", "-o", str(binary)]
                    built = subprocess.run(command, capture_output=True, text=True, timeout=30)
                    self.assertEqual(built.returncode, 0, built.stderr)
                    environment = os.environ.copy()
                    environment["ASAN_OPTIONS"] = "detect_leaks=0:halt_on_error=1"
                    environment["UBSAN_OPTIONS"] = "halt_on_error=1:print_stacktrace=1"
                    result = subprocess.run([str(binary)], capture_output=True, text=True,
                                            env=environment, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("all mesh context checks passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
