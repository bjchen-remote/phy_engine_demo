"""Public harness discovery runs without the private QQ transport project."""
from contextlib import ExitStack
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


TOOLBOXES = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLBOXES))
from dev_host import (HOST_SOURCE_ENV, SDK_FILES, HostSourceUnavailable,
                      activate_host_source, resolve_host_source)


class DevelopmentHostDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="public-host-discovery-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "private-host"
        package = self.project / "src/qq_simulator_agent"
        package.mkdir(parents=True)
        for name in SDK_FILES:
            (package / name).write_text("# Source-discovery fixture, never imported.\n")

    def test_explicit_project_or_source_paths_take_priority(self):
        for source in (self.project, self.project / "src"):
            with self.subTest(source=source):
                self.assertEqual(resolve_host_source(source, repository=self.root,
                    environ={HOST_SOURCE_ENV: str(self.root / "missing")}), self.project / "src")

    def test_environment_and_private_sibling_default(self):
        self.assertEqual(resolve_host_source(repository=self.root,
            environ={HOST_SOURCE_ENV: str(self.project)}), self.project / "src")
        private = self.root / "qq-simulator-agent-opencode"
        self.project.rename(private)
        self.assertEqual(resolve_host_source(repository=self.root, environ={}), private / "src")

    def test_missing_optional_sdk_skips_but_bad_configuration_fails(self):
        self.assertIsNone(resolve_host_source(repository=self.root, environ={}, required=False))
        with self.assertRaisesRegex(HostSourceUnavailable, "--host-source"):
            resolve_host_source(repository=self.root, environ={})
        for required in (True, False):
            with self.subTest(required=required), self.assertRaises(HostSourceUnavailable):
                resolve_host_source(self.root / "missing", repository=self.root,
                    environ={}, required=required)

    def test_partial_sdk_does_not_count_as_available(self):
        (self.project / "src/qq_simulator_agent/toolbox.py").unlink()
        with self.assertRaises(HostSourceUnavailable):
            resolve_host_source(self.project, environ={})

    def test_cli_help_does_not_import_or_require_private_sdk(self):
        environment = dict(os.environ)
        environment.pop(HOST_SOURCE_ENV, None)
        environment.pop("PYTHONPATH", None)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        for script in ("pipeline_smoke.py", "modeling_acceptance.py"):
            with self.subTest(script=script):
                process = subprocess.run([sys.executable, str(TOOLBOXES / script), "--help"],
                    cwd=self.root, env=environment, capture_output=True, text=True, timeout=10)
                self.assertEqual(process.returncode, 0, process.stderr)
                self.assertIn("--host-source", process.stdout)
                self.assertNotIn("ModuleNotFoundError", process.stderr)

    def test_missing_sdk_cli_rejects_before_creating_output(self):
        for script, options in (("pipeline_smoke.py", ["--engine", str(self.root / "engine")]),
                ("modeling_acceptance.py", ["--runtime", str(self.root / "runtime"),
                    "--python-base", str(self.root / "python"), "--image", str(self.root / "input.png"),
                    "--provider-name", "test-provider"])):
            with self.subTest(script=script):
                output = self.root / (script + "-output")
                process = subprocess.run([sys.executable, str(TOOLBOXES / script), *options,
                    "--host-source", str(self.root / "missing"), "--output", str(output)],
                    cwd=self.root, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    capture_output=True, text=True, timeout=10)
                self.assertEqual(process.returncode, 2, process.stderr)
                self.assertIn("Private QQ host SDK unavailable", process.stderr)
                self.assertFalse(output.exists())


# Only this SDK import-contract test is optional. Public engine, pipeline,
# presentation and flow-worker tests continue running on a standalone checkout.
OPTIONAL_HOST = resolve_host_source(required=False)


@unittest.skipIf(OPTIONAL_HOST is None,
                 "Private QQ host SDK unavailable; set QQ_SIMULATOR_HOST_SOURCE for integration")
class OptionalPrivateHostContractTests(unittest.TestCase):
    def test_selected_real_sdk_exposes_the_harness_contract(self):
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules))
            stack.enter_context(patch.object(sys, "path", sys.path[:]))
            activate_host_source(OPTIONAL_HOST)
            toolbox = importlib.import_module("qq_simulator_agent.toolbox")
            self.assertTrue(Path(toolbox.__file__).resolve().is_relative_to(OPTIONAL_HOST))
            for name in ("call_api", "prepare_task", "command", "validate_result"):
                self.assertTrue(callable(getattr(toolbox, name)))


if __name__ == "__main__":
    unittest.main()
