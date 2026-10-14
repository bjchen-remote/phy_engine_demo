"""Synthetic source-to-host proof through the real read-only subprocess gate."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "toolboxes"), str(ROOT / "toolboxes/physics/physics_release"),
               str(ROOT / "toolboxes/physics")]

from build_pipeline import build
from dev_host import activate_host_source, resolve_host_source
from pipeline.bundle import digest_tree
from pipeline_presentation.artifacts import canonical, digest, export_data
from physics_demo.runner import load_scene, simulate

OPTIONAL_HOST = resolve_host_source(repository=ROOT, required=False)
if OPTIONAL_HOST is not None:
    activate_host_source(OPTIONAL_HOST)
    from qq_simulator_agent import portable_data_result as host
    from qq_simulator_agent.toolbox import ToolboxError, publish


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False))


@unittest.skipUnless(sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file(),
                     "real read-only process gate requires macOS sandbox-exec")
@unittest.skipIf(OPTIONAL_HOST is None,
                 "Private QQ host SDK unavailable; set QQ_SIMULATOR_HOST_SOURCE for host integration. "
                 "Neutral portable-result verification remains covered by test_portable_result.")
class PortableDataHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.package_temp = tempfile.TemporaryDirectory(prefix="portable-host-package-")
        cls.addClassCleanup(cls.package_temp.cleanup)
        cls.directory = Path(cls.package_temp.name).resolve()
        cls.seed = cls.directory / "seed"
        scene = load_scene(ROOT / "examples/three_body.json")
        scene["world"].update(duration=.03, dt=.005, output_fps=20)
        scene["budget"]["validation"] = "visual"
        scene["queries"] = [{"id": "x", "type": "series", "metric": {
            "type": "centroid", "entity": scene["entities"][0]["id"], "axis": "x"}}]
        checked = simulate(scene, cls.seed, 30, make_video=False)
        if not checked["ok"]:
            raise AssertionError(checked)
        package = cls.directory / "package"
        cls.lock = build(ROOT / "toolboxes/physics", package)
        manifest = json.loads((package / "toolbox.json").read_bytes())
        if (manifest.get("portable_physics_data") != "pipeline-data/2"
                or not (package / "verify_portable.py").is_file()):
            raise AssertionError("builder must publish the portable contract and fixed verifier entrypoint")
        # Successful verification must remain possible when solver and neural
        # entrypoints are unusable. The package remains fully content-pinned.
        runner = package / "engine/physics_release/physics_demo/runner.py"
        with runner.open("a") as stream:
            stream.write("\ndef simulate(*args, **kwargs):\n    raise AssertionError('verifier must never run a solver')\n")
        (package / "modules/modeling/flow_runner.py").write_text("raise AssertionError('verifier must never run neural inference')\n")
        cls.lock["engine"]["digest"] = digest_tree(package / "engine")
        cls.lock["modules"]["modeling"]["digest"] = digest_tree(package / "modules/modeling")
        save(package / "bundle-lock.json", cls.lock)
        cls.registry = cls.directory / "registry"
        cls.pointer = publish(package, cls.registry)
        cls.package = cls.registry / "versions" / cls.pointer["digest"]

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="portable-host-task-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.job = self.root / "portable-event"
        self.source = self.job / "work/physics/artifacts"
        self.source.mkdir(parents=True)
        (self.job / "artifacts").mkdir()
        for path in self.seed.iterdir():
            if path.is_file():
                (self.source / path.name).write_bytes(path.read_bytes())
        result = json.loads((self.source / "result.json").read_bytes())
        for key, value in result["artifacts"].items():
            if isinstance(value, str):
                result["artifacts"][key] = str(self.source / Path(value).name)
        result["private_diagnostic"] = "synthetic compiler /Users/synthetic/path/source.c:18"
        save(self.source / "result.json", result)
        model = json.loads((self.source / "scene.normalized.json").read_bytes())
        model_document = {"schema_version": "pipeline-model/1", "domain": "physics", "model": model}
        save(self.source / "model.json", model_document)
        save(self.job / "work/pipeline/model/model.json", model_document)
        self.prepared = {"schema_version": 1, "scene": model}
        save(self.job / "work/prepared-scene.json", self.prepared)
        self.task = {"schema_version": 1, "task_id": self.job.name, "output_dir": "artifacts", "work_dir": "work",
            "limits": {"network": False, "max_output_bytes": 16 * 1024 * 1024, "wall_time_seconds": 30},
            "delivery": {"data_attachments": True, "max_data_bytes": 16 * 1024 * 1024},
            "modeling_runtime": {"python_executable": "/private/tmp/task-controlled-interpreter-must-not-run"}}
        save(self.job / "task.json", self.task)
        save(self.job / "request.json", {"schema_version": 1, "event_id": self.job.name})
        save(self.job / "toolbox-pin.json", {**self.pointer, "registry": str(self.registry)})
        save(self.job / "work/pipeline/bundle-lock.json", self.lock)
        core = {"schema_version": "pipeline-plan/1", "mode": "full", "settings": host.DEFAULT_SETTINGS,
            "prepared_sha256": host._hash(self.prepared), "bundle_sha256": host._hash(self.lock)}
        self.plan = {**core, "plan_id": host._hash(core)}
        save(self.job / "work/pipeline/plan.json", self.plan)
        roles = {"result.json": "canonical_result", "model.json": "canonical_model",
            "scene.normalized.json": "solver_model", "plan.json": "solver_plan", "summary.json": "verification",
            "completion.json": "completion", "measurements.json": "solver_observations"}
        artifacts = [{"path": name, "role": role, "media_type": "application/json",
            "bytes": (self.source / name).stat().st_size, "sha256": digest((self.source / name).read_bytes())}
            for name, role in roles.items()]
        refs = {item["path"]: item for item in artifacts}
        save(self.source / "simulation-manifest.json", {"schema_version": "pipeline-simulation/1", "domain": "physics",
            "ok": True, "data_usable": True, "quality_gate": {"passed": True, "numerical_passed": True},
            "model": refs["model.json"], "result": refs["result.json"], "artifacts": artifacts})

        def seal(value, paths):
            body = {**value, "checkpoint_files": [{"path": path.relative_to(self.job).as_posix(),
                "sha256": digest(path.read_bytes())} for path in paths]}
            return {**body, "checkpoint_sha256": host._hash(body)}

        model_file = self.job / "work/pipeline/model/model.json"
        self.simulation = seal({"ok": True, "domain": "physics", "result_path": str(self.source / "result.json"),
            "model_path": str(self.source / "model.json"), "manifest_path": str(self.source / "simulation-manifest.json")},
            sorted(path for path in self.source.iterdir() if path.is_file()))
        modeling = seal({"ok": True, "bundle_path": str(model_file), "model_path": str(model_file)}, [model_file])
        self.exported = export_data(self.source / "result.json", self.source / "model.json", self.job / "artifacts", portable=True)
        self.state = {"schema_version": "pipeline-state/1", "status": "succeeded", "bundle": self.lock,
            "plan_id": self.plan["plan_id"], "compute_key": host._hash({"prepared": self.prepared,
                "engine": self.lock["engine"], "modules": {role: self.lock["modules"][role] for role in ("modeling", "simulation")}}),
            "modeling": modeling, "simulation": self.simulation, "data": self.exported}
        save(self.job / "work/pipeline/state.json", self.state)
        self.result = {"schema_version": 1, "task_id": self.job.name, "status": "succeeded",
            "portable_physics_data": "pipeline-data/2", "verification": {"passed": True},
            "pipeline": {"bundle": self.lock, "plan_id": self.plan["plan_id"],
                "simulation_sha256": digest((self.source / "result.json").read_bytes())}}
        self.attachment = {"path": "data.zip", "media_type": "application/zip", "role": "data",
            "size_bytes": self.exported["bytes"], "sha256": self.exported["sha256"]}

    def call(self):
        return host.validate_portable_data(self.job, self.result, self.attachment, 16 * 1024 * 1024)

    def snapshots(self):
        return {str(path): digest(path.read_bytes()) for directory in (self.job, self.package)
                for path in directory.rglob("*") if path.is_file()}

    def test_real_readonly_subprocess_recomputes_source_without_solver(self):
        before = self.snapshots()
        checked = self.call()
        self.assertEqual(checked["portable_physics_data"], "pipeline-data/2")
        self.assertEqual(checked["source_result_sha256"], self.result["pipeline"]["simulation_sha256"])
        self.assertEqual(checked["sha256"], self.attachment["sha256"])
        self.assertEqual(before, self.snapshots())

    def test_self_consistent_rehashed_zip_cannot_substitute_scientific_values(self):
        archive_path = self.job / "artifacts/data.zip"
        with zipfile.ZipFile(archive_path) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        public = json.loads(members["result.json"])
        public["trajectory"]["frames"][0]["t"] += 99
        members["result.json"] = canonical(public)
        manifest = json.loads(members["manifest.json"])
        for item in manifest["members"]:
            item.update(sha256=digest(members[item["path"]]), bytes=len(members[item["path"]]))
        for item in manifest["portable_projection"]["members"]:
            item.update(projected_sha256=digest(members[item["path"]]), projected_bytes=len(members[item["path"]]))
        members["manifest.json"] = canonical(manifest)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in sorted(members.items()):
                archive.writestr(name, payload)
        archive_path.write_bytes(output.getvalue())
        self.attachment.update(sha256=digest(output.getvalue()), size_bytes=len(output.getvalue()))
        with self.assertRaises(ToolboxError):
            self.call()

    def test_modified_source_wrong_pin_and_missing_portable_marker_are_rejected(self):
        source = self.source / "result.json"
        original = source.read_bytes()
        source.write_bytes(original + b" ")
        with self.assertRaisesRegex(ToolboxError, "checkpoint source changed"):
            self.call()
        source.write_bytes(original)
        pin_path = self.job / "toolbox-pin.json"
        pointer = json.loads(pin_path.read_bytes())
        save(pin_path, {**pointer, "digest": "0" * 64})
        with self.assertRaises(ToolboxError):
            self.call()
        save(pin_path, pointer)
        self.result.pop("portable_physics_data")
        with self.assertRaisesRegex(ToolboxError, "cannot downgrade"):
            self.call()

    def test_legacy_zip_cannot_downgrade_a_portable_result(self):
        legacy = export_data(self.source / "result.json", self.source / "model.json", self.job / "artifacts")
        self.attachment.update(sha256=legacy["sha256"], size_bytes=legacy["bytes"])
        with self.assertRaises(ToolboxError):
            self.call()

    def test_host_rejects_forged_success_and_uses_fixed_interpreter(self):
        observed = []

        def forged(argv, **kwargs):
            observed.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout=b'{"verified":true}', stderr=b"")

        with patch.object(host.subprocess, "run", side_effect=forged):
            with self.assertRaisesRegex(ToolboxError, "proof differs"):
                self.call()
        argv, options = observed[0]
        self.assertEqual(argv[0], "/usr/bin/sandbox-exec")
        self.assertEqual(argv[3], str(Path(sys.executable).resolve()))
        self.assertEqual(argv[4:6], ["-I", "-B"])
        self.assertIn("(deny file-write*)", argv[2])
        self.assertIn("(deny network*)", argv[2])
        self.assertNotIn(self.task["modeling_runtime"]["python_executable"], argv)
        self.assertNotIn("PYTHONPATH", options["env"])

    def test_host_rejects_colon_and_empty_segment_archive_members(self):
        archive_path = self.job / "artifacts/data.zip"
        original = archive_path.read_bytes()
        for name in ("modeling/unsafe:receipt.json", "modeling//receipt.json"):
            with self.subTest(name=name):
                archive_path.write_bytes(original)
                with zipfile.ZipFile(archive_path, "a") as archive:
                    archive.writestr(name, b"{}")
                self.attachment.update(sha256=digest(archive_path.read_bytes()), size_bytes=archive_path.stat().st_size)
                with self.assertRaises(ToolboxError):
                    self.call()


if __name__ == "__main__":
    unittest.main()
