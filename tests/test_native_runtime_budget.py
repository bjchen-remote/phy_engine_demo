"""A cold native build follows the task's wall budget."""
from __future__ import annotations

from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from physics_demo.core.native_runtime import load_library


class NativeRuntimeBudgetTests(unittest.TestCase):
    def run_fake_build(self, remaining_seconds: float) -> list[float | None]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "fixture.c"
            source.write_text("int fixture(void) { return 1; }\n")
            source.with_suffix(".h").write_text("#define FIXTURE 1\n")
            timeouts: list[float | None] = []

            def fake_run(_command, **options):
                timeouts.append(options["timeout"])
                return SimpleNamespace(stdout=b"fake compiler", stderr=b"", returncode=0)

            with patch("physics_demo.core.native_runtime._compiler", return_value="fake-cc"), \
                    patch("physics_demo.core.native_runtime.subprocess.run", side_effect=fake_run), \
                    patch("physics_demo.core.native_runtime.tempfile.gettempdir", return_value=directory), \
                    patch("physics_demo.core.native_runtime.C.CDLL", return_value=object()):
                load_library(source, prefix="test-", name="fixture",
                             deadline=time.monotonic() + remaining_seconds,
                             libraries={}, bind=lambda _library: None)
            return timeouts

    def test_finite_cold_build_uses_remaining_task_budget(self):
        timeouts = self.run_fake_build(9)
        self.assertEqual(len(timeouts), 2)
        self.assertTrue(all(value is not None and 5 < value <= 9 for value in timeouts))

    def test_host_unlimited_cold_build_has_no_subprocess_deadline(self):
        self.assertEqual(self.run_fake_build(1_000_000_000_000), [None, None])


if __name__ == "__main__":
    unittest.main()
