"""Installer contracts tested with mock processes and tiny streamed fixtures."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install_modeling_flow as installer


def checksum(data):
    return hashlib.sha256(data).hexdigest()


class Response(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {}


class ModelingFlowInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="flow-installer-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def test_pinned_stream_is_published_only_after_exact_hash_and_size(self):
        path = self.root / "weights"
        opener = Mock()
        opener.open.return_value = Response(b"tiny weights")
        installer.download_pinned("https://example.invalid/pinned", path, checksum(b"tiny weights"), 12, opener)
        self.assertEqual(path.read_bytes(), b"tiny weights")
        self.assertFalse((self.root / "weights.part").exists())
        installer.download_pinned("https://example.invalid/pinned", path, checksum(b"tiny weights"), 12, Mock())

    def test_hash_failure_keeps_partial_file_without_publishing(self):
        path = self.root / "weights"
        opener = Mock()
        opener.open.return_value = Response(b"wrong")
        with self.assertRaisesRegex(installer.InstallError, "SHA-256"):
            installer.download_pinned("https://example.invalid/pinned", path, "0" * 64, 5, opener)
        self.assertFalse(path.exists())
        self.assertEqual(path.with_name("weights.part").read_bytes(), b"wrong")

    def test_partial_download_resumes_only_matching_http_range(self):
        path = self.root / "weights"
        path.with_name("weights.part").write_bytes(b"abc")
        opener = Mock()
        opener.open.return_value = Response(b"def", 206, {"Content-Range": "bytes 3-5/6"})
        installer.download_pinned("https://example.invalid/pinned", path, checksum(b"abcdef"), 6, opener)
        self.assertEqual(path.read_bytes(), b"abcdef")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Range"), "bytes=3-")

    def test_server_ignoring_range_never_overwrites_retained_partial(self):
        path = self.root / "weights"
        path.with_name("weights.part").write_bytes(b"abc")
        opener = Mock()
        opener.open.return_value = Response(b"abcdef")
        with self.assertRaisesRegex(installer.InstallError, "did not honor"):
            installer.download_pinned("https://example.invalid/pinned", path, checksum(b"abcdef"), 6, opener)
        self.assertEqual(path.with_name("weights.part").read_bytes(), b"abc")

    def test_oversized_download_is_bounded(self):
        path = self.root / "weights"
        opener = Mock()
        opener.open.return_value = Response(b"123456789")
        with self.assertRaisesRegex(installer.InstallError, "exceeded"):
            installer.download_pinned("https://example.invalid/pinned", path, checksum(b"123"), 3, opener)
        self.assertFalse(path.exists())
        self.assertLessEqual(path.with_name("weights.part").stat().st_size, 3)

    def test_existing_operator_files_symlinks_and_http_are_preserved(self):
        path = self.root / "weights"
        path.write_bytes(b"operator data")
        with self.assertRaisesRegex(installer.InstallError, "preserved"):
            installer.download_pinned("https://example.invalid/pinned", path, "0" * 64, 3)
        self.assertEqual(path.read_bytes(), b"operator data")
        link = self.root / "linked"
        link.symlink_to(path)
        with self.assertRaises(installer.InstallError):
            installer.download_pinned("https://example.invalid/pinned", link, "0" * 64, 3)
        with self.assertRaises(installer.InstallError):
            installer.download_pinned("http://example.invalid/pinned", self.root / "http", "0" * 64, 3)

    def test_failed_install_is_retained_and_requires_explicit_matching_resume(self):
        output = self.root / "runtime"
        metadata = {"version": [3, 12, 0], "system": "Darwin", "machine": "arm64"}
        with patch.object(installer, "_python_metadata", return_value=metadata), \
                patch.object(installer, "_run", side_effect=installer.InstallError("fixture failure")):
            with self.assertRaisesRegex(installer.InstallError, "--resume"):
                installer.install(output, sys.executable, "Test operator")
            state = json.loads((output / "installer-state.json").read_text())
            self.assertEqual(state["stage"], "upstream")
            self.assertFalse(state["complete"])
            with self.assertRaisesRegex(installer.InstallError, "must be new"):
                installer.install(output, sys.executable, "Test operator")
            with self.assertRaisesRegex(installer.InstallError, "identity differs"):
                installer.install(output, sys.executable, "Changed operator", resume=True)

    def test_python_platform_and_version_are_verified_without_shell(self):
        good = {"version": [3, 12, 4], "system": "Darwin", "machine": "arm64"}
        for update in ({"version": [3, 11, 9]}, {"machine": "x86_64"}, {"system": "Linux"}):
            completed = types.SimpleNamespace(stdout=json.dumps({**good, **update}))
            with self.subTest(update=update), patch.object(installer.subprocess, "run", return_value=completed):
                with self.assertRaisesRegex(installer.InstallError, "Apple Silicon"):
                    installer._python_metadata(Path(sys.executable))
        with patch.object(installer.subprocess, "run", return_value=types.SimpleNamespace(stdout=json.dumps(good))) as run:
            installer._python_metadata(Path(sys.executable))
        self.assertEqual(run.call_args.args[0][:3], [sys.executable, "-I", "-c"])
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_complete_install_emits_host_fragment_and_fixed_commands(self):
        output = self.root / "runtime"
        base = self.root / "python-base"
        base.mkdir()
        commands = []
        def metadata(path):
            return {"version": [3, 12, 4], "system": "Darwin", "machine": "arm64",
                    "prefix": str(output / ".venv") if ".venv" in str(path) else str(base),
                    "base_prefix": str(base)}
        def run(arguments, log, timeout=1800):
            commands.append(arguments)
            if arguments[:2] == ["git", "clone"]:
                (output / "upstream/.git").mkdir(parents=True)
                (output / "upstream/LICENSE").write_text("license")
                (output / "upstream/THIRD_PARTY_LICENSES.md").write_text("notice")
            if "venv" in arguments:
                python = output / ".venv/bin/python"
                python.parent.mkdir(parents=True)
                python.write_text("fixture")
        def download(url, path, checksum, size):
            path.write_bytes(b"fixture pinned download")
        def health(arguments, **kwargs):
            kwargs["stdout"].write('{"ok":true,"inference_verified":false}\n')
            return types.SimpleNamespace(returncode=0)
        with patch.object(installer, "_python_metadata", side_effect=metadata), \
                patch.object(installer, "_run", side_effect=run), \
                patch.object(installer, "download_pinned", side_effect=download), \
                patch.object(installer.subprocess, "run", side_effect=health), \
                patch.object(installer, "MLX_LICENSE_SHA256", checksum(b"license")), \
                patch.object(installer, "THIRD_PARTY_NOTICE_SHA256", checksum(b"notice")):
            result = installer.install(output, sys.executable, "Test operator")
        self.assertTrue(result["ok"])
        self.assertFalse(result["inference_verified"])
        fragment = json.loads(Path(result["host_config_fragment"]).read_text())["modeling_runtime"]
        self.assertEqual(fragment["provider_name"], "Test operator")
        self.assertEqual(fragment["read_roots"], [str(base)])
        self.assertEqual(fragment["python_executable"], str(output / ".venv/bin/python"))
        self.assertIn(["git", "-C", str(output / "upstream"), "checkout", "--detach", installer.UPSTREAM_COMMIT], commands)
        pip_install = next(command for command in commands if "install" in command)
        self.assertIn("--no-deps", pip_install)
        self.assertIn("--only-binary=:all:", pip_install)
        self.assertNotIn("torch", (output / "requirements-mlx.lock.txt").read_text())
        self.assertTrue((output / "licenses/TENCENT-HUNYUAN-LICENSE.txt").is_file())
        config = json.loads((output / "config.json").read_text())
        self.assertEqual(config["background_removal"], "u2net")
        self.assertEqual(config["foreground_model"]["sha256"], installer.FOREGROUND_SHA256)
        self.assertTrue((output / "licenses/U2NET-APACHE-2.0.txt").is_file())
        self.assertTrue((output / "licenses/REMBG-MIT.txt").is_file())

    def test_none_configuration_cannot_start_a_foreground_download(self):
        configured = installer._runtime_config(self.root / "runtime", "none")
        self.assertEqual(configured["background_removal"], "none")
        self.assertNotIn("foreground_model", configured)


if __name__ == "__main__":
    unittest.main()
