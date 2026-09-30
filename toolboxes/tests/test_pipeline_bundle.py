"""Contract and hot-swap tests for the QQ-compatible pipeline composition."""
from __future__ import annotations

import copy
from contextlib import ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


TOOLBOXES = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLBOXES))

from build_pipeline import build, build_module
from pipeline import bundle
from registry import publish, read_json as registry_read_json, verify


_spec = importlib.util.spec_from_file_location("_pipeline_adapter_contract_test", TOOLBOXES / "pipeline/adapter.py")
adapter = importlib.util.module_from_spec(_spec)
with patch.dict(sys.modules, {"bundle": bundle}):
    _spec.loader.exec_module(adapter)


def save(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def task_payload():
    return {"schema_version": 1, "task_id": "pipeline-contract", "output_dir": "artifacts", "work_dir": "work",
            "request": {"text": "simulate", "plan_required": True},
            "limits": {"wall_time_seconds": 30, "max_output_bytes": 16 * 1024 * 1024, "network": False},
            "delivery": {"data_attachments": True, "max_data_bytes": 16 * 1024 * 1024}}


class PipelineBundleContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pipeline-bundle-contract-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.engine = self.directory / "engine-source"
        (self.engine / "physics_release/physics_demo").mkdir(parents=True)
        (self.engine / "physics_release/physics_demo/runner.py").write_text("# Contract fixture; never executed.\n")
        (self.engine / "toolbox_adapter.py").write_text("# Contract fixture; never executed.\n")
        save(self.engine / "toolbox.json", {"schema_version": 1, "id": "physics", "version": "1.4.0",
             "entrypoint": "toolbox_adapter.py", "capabilities": ["test contract"],
             "agent_api": {"operations": ["physics_prepare", "physics_simulate"]}})
        self.package = self.directory / "pipeline"
        self.lock = build(self.engine, self.package)
        self.job = self.directory / "job"
        (self.job / "work").mkdir(parents=True)
        (self.job / "artifacts").mkdir()
        self.prepared = {"schema_version": 1, "scene": {"name": "prepared-fixture", "world": {"duration": 1}}}
        save(self.job / "work/prepared-scene.json", self.prepared)
        self.task = task_payload()

    def freeze(self):
        return adapter.freeze(self.job, self.lock, estimate=2)

    def call_cli(self, package: Path, operation: str):
        save(self.job / "task.json", self.task)
        save(self.job / "work/toolbox-call.json", {"schema_version": 1, "operation": operation, "arguments": {}})
        return subprocess.run([sys.executable, str(package / "adapter.py"), "--phase", "api",
                               "--task", str(self.job / "task.json")], capture_output=True, text=True,
                              env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=15)

    def test_composition_pins_engine_and_all_three_modules(self):
        self.assertEqual(bundle.verify_bundle(self.package), self.lock)
        self.assertEqual(set(self.lock["modules"]), set(bundle.ROLES))
        info = registry_read_json(self.package / "toolbox.json")
        self.assertTrue(info["requires_data_delivery"])
        self.assertEqual(info["agent_api"]["execution_operation"], "physics_simulate")
        self.assertIn("pipeline_render_prepare", info["agent_api"]["operations"])
        for role in bundle.ROLES:
            module = self.package / self.lock["modules"][role]["path"]
            self.assertEqual(bundle.validate_module(module, role)["role"], role)

    def test_engine_or_any_module_tampering_invalidates_bundle(self):
        targets = [self.package / "engine/physics_release/physics_demo/runner.py"]
        targets += [self.package / "modules" / role / "adapter.py" for role in bundle.ROLES]
        for path in targets:
            with self.subTest(path=path.relative_to(self.package)):
                original = path.read_bytes()
                path.write_bytes(original + b"\n# changed after pin\n")
                with self.assertRaisesRegex(ValueError, "integrity mismatch"):
                    bundle.verify_bundle(self.package)
                path.write_bytes(original)
        self.assertEqual(bundle.verify_bundle(self.package), self.lock)

    def test_every_role_rejects_incompatible_input_and_output_schemas(self):
        for role in bundle.ROLES:
            path = self.package / "modules" / role / "module.json"
            original = bundle.read_json(path)
            for field in ("input_schema", "output_schema"):
                with self.subTest(role=role, field=field):
                    save(path, {**original, field: "incompatible/99"})
                    with self.assertRaisesRegex(ValueError, "incompatible module formats"):
                        bundle.validate_module(path.parent, role)
            save(path, original)

    def test_module_wrong_role_and_entrypoint_escape_are_rejected(self):
        module = self.directory / "separate-modeling"
        info = build_module("modeling", module)
        with self.assertRaisesRegex(ValueError, "role/protocol"):
            bundle.validate_module(module, "simulation")
        save(module / "module.json", {**info, "entrypoint": "../engine-source/toolbox_adapter.py"})
        with self.assertRaisesRegex(ValueError, "traversal"):
            bundle.validate_module(module, "modeling")

    def test_module_engine_contract_and_network_flags_must_match_executor(self):
        for role in bundle.ROLES:
            path = self.package / "modules" / role / "module.json"
            original = bundle.read_json(path)
            for update in ({"engine_contract": "another-engine/2"}, {"network": True},
                           {"network": None}, {"network": 0}):
                with self.subTest(role=role, update=update):
                    save(path, {**original, **update})
                    with self.assertRaisesRegex(ValueError, "engine contract or network"):
                        bundle.validate_module(path.parent, role)
            save(path, original)

    def test_misspelled_replacement_role_is_not_silently_ignored(self):
        output = self.directory / "mistyped-composition"
        with self.assertRaisesRegex(ValueError, "unknown module role override"):
            build(self.engine, output, {"render": self.package / "modules/rendering"})
        self.assertFalse(output.exists())

    def test_new_stage_can_be_composed_and_activated_without_invalidating_old_pin(self):
        registry = self.directory / "registry"
        old_pointer = publish(self.package, registry, activate=True)
        replacement = self.directory / "new-modeling"
        info = build_module("modeling", replacement)
        save(replacement / "module.json", {**info, "version": "0.1.1"})
        metadata = registry_read_json(replacement / "toolbox.json")
        save(replacement / "toolbox.json", {**metadata, "version": "0.1.1"})
        with (replacement / "manual/SKILL.md").open("a") as handle:
            handle.write("\nReplacement fixture documents the same model format.\n")
        module_pointer = publish(replacement, self.directory / "stage-registry", activate=False)
        self.assertEqual(module_pointer["id"], "physics-modeling")
        composed = self.directory / "pipeline-next"
        next_lock = build(self.engine, composed, {"modeling": replacement})
        next_pointer = publish(composed, registry, activate=True)
        self.assertNotEqual(old_pointer["digest"], next_pointer["digest"])
        self.assertEqual(registry_read_json(registry / "active.json"), next_pointer)
        self.assertEqual(bundle.verify_bundle(verify(registry, old_pointer)), self.lock)
        self.assertEqual(bundle.verify_bundle(verify(registry, next_pointer)), next_lock)
        self.assertEqual(self.lock["engine"], next_lock["engine"])
        self.assertNotEqual(self.lock["modules"]["modeling"]["digest"], next_lock["modules"]["modeling"]["digest"])
        for role in ("simulation", "rendering"):
            self.assertEqual(self.lock["modules"][role], next_lock["modules"][role])

    def test_task_cannot_switch_component_bundle_after_first_api_call(self):
        first = self.call_cli(self.package, "pipeline_capabilities")
        self.assertEqual(first.returncode, 0, first.stderr)
        replacement = self.directory / "replacement"
        info = build_module("modeling", replacement)
        save(replacement / "module.json", {**info, "version": "0.1.2"})
        next_package = self.directory / "pipeline-next"
        build(self.engine, next_package, {"modeling": replacement})
        changed = self.call_cli(next_package, "pipeline_capabilities")
        self.assertNotEqual(changed.returncode, 0)
        self.assertIn("task cannot change pinned pipeline components", changed.stderr)
        self.assertEqual(bundle.read_json(self.job / "work/pipeline/bundle-lock.json"), self.lock)

    def test_missing_or_invalid_file_delivery_support_rejects_before_execution(self):
        self.freeze()
        save(self.job / "work/toolbox-call.json", {"schema_version": 1, "operation": "physics_simulate", "arguments": {}})
        for delivery in (None, {}, {"data_attachments": False, "max_data_bytes": 10},
                         {"data_attachments": True}, {"data_attachments": True, "max_data_bytes": True},
                         {"data_attachments": True, "max_data_bytes": 0}):
            task = copy.deepcopy(self.task)
            if delivery is None:
                del task["delivery"]
            else:
                task["delivery"] = delivery
            with self.subTest(delivery=delivery):
                self.assertFalse(adapter.delivery_ready(task))
                with patch.object(adapter, "stage", side_effect=AssertionError("Stage must not start")):
                    with self.assertRaisesRegex(ValueError, "data_delivery_unavailable"):
                        adapter.run(self.package, self.package / "engine", self.job, task, self.lock)
                    with self.assertRaisesRegex(ValueError, "data_delivery_unavailable"):
                        adapter.api(self.package, self.package / "engine", self.job, task, self.lock)

    def test_prepared_model_settings_lock_and_plan_cannot_drift(self):
        self.freeze()
        self.assertEqual(adapter.check_plan(self.job, self.lock)[1], self.prepared)
        changed = copy.deepcopy(self.prepared)
        changed["scene"]["world"]["duration"] = 2
        save(self.job / "work/prepared-scene.json", changed)
        with self.assertRaisesRegex(ValueError, "plan changed"):
            adapter.check_plan(self.job, self.lock)
        save(self.job / "work/prepared-scene.json", self.prepared)
        save(self.job / "work/pipeline/settings.json", {**adapter.DEFAULT_SETTINGS, "rendering_quality": "preview"})
        with self.assertRaisesRegex(ValueError, "plan changed"):
            adapter.check_plan(self.job, self.lock)
        save(self.job / "work/pipeline/settings.json", adapter.DEFAULT_SETTINGS)
        changed_lock = copy.deepcopy(self.lock)
        changed_lock["modules"]["modeling"]["digest"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "plan changed"):
            adapter.check_plan(self.job, changed_lock)
        plan_path = self.job / "work/pipeline/plan.json"
        plan = bundle.read_json(plan_path)
        save(plan_path, {**plan, "mode": "render_only"})
        with self.assertRaisesRegex(ValueError, "plan changed"):
            adapter.check_plan(self.job, self.lock)

    def test_configure_invalidates_preparation_and_rejects_unavailable_quality(self):
        self.freeze()
        def configure(arguments):
            save(self.job / "work/toolbox-call.json", {"schema_version": 1, "operation": "pipeline_configure", "arguments": arguments})
            adapter.api(self.package, self.package / "engine", self.job, self.task, self.lock)
            return bundle.read_json(self.job / "work/toolbox-response.json")["result"]
        result = configure({"modeling_quality": "high"})
        self.assertEqual(result["code"], "unsupported_quality")
        self.assertTrue((self.job / "work/pipeline/plan.json").is_file())
        result = configure({"rendering_quality": "preview", "simulation_quality": "strict"})
        self.assertTrue(result["ok"])
        self.assertFalse((self.job / "work/pipeline/plan.json").exists())
        self.assertFalse((self.job / "work/prepared-scene.json").exists())

    def test_checkpoint_detects_changed_canonical_file(self):
        result_path = self.job / "work/saved-result.json"
        save(result_path, {"ok": True, "value": 1})
        checkpoint = adapter.seal_checkpoint({"ok": True, "result_path": str(result_path)}, self.job)
        adapter.verify_checkpoint(checkpoint, self.job)
        save(result_path, {"ok": True, "value": 2})
        with self.assertRaisesRegex(ValueError, "modified"):
            adapter.verify_checkpoint(checkpoint, self.job)

    def test_checkpoint_also_binds_paths_and_quality_metadata(self):
        result_path = self.job / "work/saved-result.json"
        save(result_path, {"ok": True, "value": 1})
        checkpoint = adapter.seal_checkpoint({"ok": True, "result_path": str(result_path),
            "quality_gate": {"passed": True, "numerical_passed": False}}, self.job)
        for field, value in (("result_path", str(self.job / "work/another-result.json")),
                             ("quality_gate", {"passed": True, "numerical_passed": True}), ("ok", False)):
            with self.subTest(field=field):
                changed = {**checkpoint, field: value}
                with self.assertRaisesRegex(ValueError, "invalid saved stage checkpoint"):
                    adapter.verify_checkpoint(changed, self.job)

    def test_legacy_model_compaction_preserves_the_original_admission_limit(self):
        path = self.job / "work/draft-scene.json"
        oversized = {"scene": {"notes": "x" * 1_000_000}}
        save(path, oversized)
        original = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "existing 1000000-byte data limit"):
            adapter.compact_engine_models(self.job)
        self.assertEqual(path.read_bytes(), original)

    def test_legacy_model_compaction_rejects_ambiguous_or_unbounded_files(self):
        path = self.job / "work/draft-scene.json"
        for encoded, reason in ((b'{"scene":{},"scene":{}}', "duplicate"),
                                (b'{"scene":{"mass":NaN}}', "nonfinite"),
                                (b'{"scene":{}}' + b' ' * 4_000_000, "oversized")):
            with self.subTest(reason=reason):
                path.write_bytes(encoded)
                with self.assertRaisesRegex(ValueError, reason):
                    adapter.compact_engine_models(self.job)
                self.assertEqual(path.read_bytes(), encoded)
        path.unlink()
        outside = self.directory / "external-model.json"
        save(outside, {"scene": {}})
        path.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "invalid"):
            adapter.compact_engine_models(self.job)
        self.assertTrue(path.is_symlink())

    def test_legacy_mesh_asset_compaction_preserves_mesh_reference_order(self):
        # The old reference hash preserves insertion order; sorting keys would
        # change the reference even though the decoded geometry is equivalent.
        mesh = {"vertices": list(range(40_000)), "triangles": []}
        encoded = json.dumps(mesh, ensure_ascii=False, separators=(",", ":"))
        reference = hashlib.sha256(encoded.encode()).hexdigest()
        path = self.job / "work/mesh-assets" / (reference + ".json")
        path.parent.mkdir()
        path.write_text(json.dumps(mesh, indent=32), encoding="utf-8")
        self.assertGreater(path.stat().st_size, 1_000_000)
        adapter.compact_engine_models(self.job)
        restored = bundle.read_json(path)
        self.assertEqual(restored, mesh)
        actual = json.dumps(restored, ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(hashlib.sha256(actual.encode()).hexdigest(), reference)
        compact = path.read_bytes()
        adapter.compact_engine_models(self.job)
        self.assertEqual(path.read_bytes(), compact)


class PipelineRealEngineCompatibilityTests(unittest.TestCase):
    def test_large_legacy_json_preserves_owned_reads_mesh_reference_and_plan_lock(self):
        """Use real engine serialization and admission without a timed large audit."""
        engine = Path(os.environ.get("PHYSICS_PIPELINE_PHYSICS_ROOT", str(TOOLBOXES / "physics")))
        with tempfile.TemporaryDirectory(prefix="pipeline-large-mesh-") as name:
            root = Path(name).resolve()
            package, job = root / "package", root / "job"
            lock = build(engine, package)
            (job / "work/mesh-assets").mkdir(parents=True)
            (job / "artifacts").mkdir()
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            fixture = """
import copy, hashlib, json, sys
from pathlib import Path
engine, job = map(Path, sys.argv[1:])
sys.path[:0] = [str(engine), str(engine / 'physics_release')]
from physics_demo.core.meshes import build_mesh
from run_simulation import _atomic_write_json
from modeling import _owned_json
scene = json.loads((engine / 'physics_release/examples/mesh_soft_drop.json').read_text())
# Reuse one small, fully audited closed asset. Nine distinct static instances
# exceed 1MB only through legacy indentation while staying in scene budgets.
mesh = build_mesh({'type':'ellipsoid', 'segments':32, 'rings':16, 'radii':[.15,.15,.15],
    'provenance':{'source':'image', 'notes':'Explicit assumed scale and hidden surface.'}})
original = scene['entities'][0]
scene['entities'] = []
for index in range(9):
    entity = copy.deepcopy(original)
    entity.update(id='soft' if index == 0 else 'mesh-'+str(index), mesh=mesh,
        motion='static', position=[(index%3-1)*.5,1.7,(index//3-1)*.5])
    scene['entities'].append(entity)
scene['world'].update(duration=.05, output_fps=20)
scene['queries'] = []
_atomic_write_json(job / 'work/draft-scene.json', scene)
_atomic_write_json(job / 'work/prepared-scene.json', {'schema_version':1, 'scene':scene})
encoded = json.dumps(mesh, ensure_ascii=False, separators=(',',':'), allow_nan=False)
reference = hashlib.sha256(encoded.encode()).hexdigest()
_atomic_write_json(job / 'work/mesh-assets' / (reference+'.json'), mesh)
(job / 'mesh-ref.txt').write_text(reference)
try:
    _owned_json(job / 'work/draft-scene.json')
except ValueError:
    pass
else:
    raise AssertionError('Legacy raw admission must reject the indented model')
"""
            created = subprocess.run([sys.executable, "-c", fixture, str(package / "engine"), str(job)],
                                     capture_output=True, text=True, env=env, timeout=15)
            self.assertEqual(created.returncode, 0, created.stderr)
            draft_path, prepared_path = job / "work/draft-scene.json", job / "work/prepared-scene.json"
            original = bundle.read_json(draft_path, 4_000_000)
            original_prepared = bundle.read_json(prepared_path, 4_000_000)
            self.assertGreater(draft_path.stat().st_size, 1_000_000)
            self.assertLess(len(json.dumps(original, ensure_ascii=False).encode()), 1_000_000)
            self.assertGreaterEqual(sum(len(e["mesh"]["vertices"]) for e in original["entities"]), 4_000)
            adapter.compact_engine_models(job)
            self.assertEqual(bundle.read_json(draft_path), original)
            self.assertEqual(bundle.read_json(prepared_path), original_prepared)
            admitted = """
import json, sys
from pathlib import Path
engine, job = map(Path, sys.argv[1:])
sys.path[:0] = [str(engine), str(engine / 'physics_release')]
from modeling import _owned_json, _mesh_value
reference = (job / 'mesh-ref.txt').read_text()
draft = _owned_json(job / 'work/draft-scene.json')
prepared = _owned_json(job / 'work/prepared-scene.json')
assert prepared['scene'] == draft
assert json.loads(_mesh_value(job, reference)) == draft['entities'][0]['mesh']
"""
            process = subprocess.run([sys.executable, "-c", admitted, str(package / "engine"), str(job)],
                                     capture_output=True, text=True, env=env, timeout=15)
            self.assertEqual(process.returncode, 0, process.stderr)
            adapter.freeze(job, lock)
            plan, prepared = adapter.check_plan(job, lock)
            self.assertEqual(prepared, original_prepared)
            self.assertEqual(plan["prepared_sha256"], adapter.object_hash(prepared))
            self.assertLessEqual(draft_path.stat().st_size, 1_000_000)
            self.assertLessEqual(prepared_path.stat().st_size, 1_000_000)
            self.assertEqual(bundle.verify_bundle(package), lock)

    def test_selected_real_engine_prepares_and_executes_model_worker_without_rendering(self):
        engine = Path(os.environ.get("PHYSICS_PIPELINE_PHYSICS_ROOT", str(TOOLBOXES / "physics")))
        with tempfile.TemporaryDirectory(prefix="pipeline-real-engine-") as name:
            root = Path(name).resolve()
            package, job = root / "package", root / "job"
            lock = build(engine, package)
            (job / "work").mkdir(parents=True)
            (job / "artifacts").mkdir()
            task = task_payload()
            save(job / "task.json", task)
            scene = json.loads((engine / "physics_release/scenes/three_body.json").read_text())
            scene["world"].update(duration=0.1, dt=0.005, output_fps=10)
            scene["budget"].update(backend="python", validation="strict", wall_time_s=30)
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            save(job / "work/toolbox-call.json", {"schema_version": 1, "operation": "physics_prepare",
                 "arguments": {"scene_json": json.dumps(scene)}})
            command = [sys.executable, str(package / "adapter.py"), "--phase", "api", "--task", str(job / "task.json")]
            prepared_process = subprocess.run(command, capture_output=True, text=True, env=env, timeout=15)
            self.assertEqual(prepared_process.returncode, 0, prepared_process.stderr)
            prepared = bundle.read_json(job / "work/toolbox-response.json")["result"]
            self.assertTrue(prepared["ok"] and prepared["ready_to_simulate"], prepared)
            self.assertEqual(prepared["pipeline"]["engine_version"], lock["engine"]["version"])
            request, response = job / "stage-call.json", job / "stage-result.json"
            save(request, {"schema_version": "stage-call/1", "stage": "modeling", "action": "run", "arguments": {
                 "domain": "physics", "model": json.loads(prepared["scene_json"]),
                 "output_dir": str(job / "work/canonical-model"), "budget_seconds": 30}})
            process = subprocess.run([sys.executable, str(package / "modules/modeling/adapter.py"),
                                      "--engine", str(package / "engine"), "--request", str(request),
                                      "--response", str(response)], capture_output=True, text=True, env=env, timeout=15)
            self.assertEqual(process.returncode, 0, process.stderr)
            modeled = bundle.read_json(response)
            self.assertTrue(modeled["ok"], modeled)
            self.assertEqual(bundle.read_json(Path(modeled["bundle_path"]))["source_engine"]["version"], lock["engine"]["version"])
            self.assertEqual(bundle.verify_bundle(package), lock)


class PipelineRecoveryContractTests(unittest.TestCase):
    """Exercise real model/solver workers and mock only presentation execution."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="pipeline-recovery-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        source = Path(os.environ.get("PHYSICS_PIPELINE_PHYSICS_ROOT", str(TOOLBOXES / "physics")))
        self.package, self.job = self.directory / "package", self.directory / "job"
        self.lock = build(source, self.package)
        self.engine = self.package / "engine"
        (self.job / "work").mkdir(parents=True)
        (self.job / "artifacts").mkdir()
        self.task = task_payload()
        scene = json.loads((source / "physics_release/scenes/three_body.json").read_text())
        scene["world"].update(duration=0.1, dt=0.005, output_fps=10)
        scene["budget"].update(backend="python", validation="strict", wall_time_s=30)
        scene["queries"] = [{"id": "body_a_x", "type": "series",
                             "metric": {"type": "centroid", "entity": "body-a", "axis": "x"}}]
        save(self.job / "work/prepared-scene.json", {"schema_version": 1, "scene": scene})
        adapter.freeze(self.job, self.lock, estimate=2)
        # Legacy reply helpers import from the pinned release inside the parent
        # process. Keep all such imports scoped to this one test fixture.
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        self.contexts.enter_context(patch.dict(sys.modules))
        self.contexts.enter_context(patch.object(sys, "path", [str(self.engine / "physics_release"), str(self.engine), *sys.path]))
        self.contexts.enter_context(patch.dict(os.environ, {"PYTHONDONTWRITEBYTECODE": "1"}))
        self.real_stage = adapter.stage
        self.calls = []

    def state(self):
        return bundle.read_json(self.job / "work/pipeline/state.json", 4_000_000)

    def fingerprint(self, path):
        path = Path(path)
        return (bundle.sha256(path), path.stat().st_mtime_ns)

    def fake_presentation(self, root, engine, job, lock, role, arguments, start, task, action="run"):
        """The real data exporter runs; only video encoding is a test double."""
        self.calls.append((role, action))
        if (role, action) == ("rendering", "render"):
            video = job / "artifacts/simulation.mp4"
            video.write_bytes(b"mock verified video; decoder tested separately")
            return {"ok": True, "sha256": bundle.sha256(video)}
        return self.real_stage(root, engine, job, lock, role, arguments, start, task, action)

    def run_adapter(self, dispatch):
        with patch.object(adapter, "stage", side_effect=dispatch):
            adapter.run(self.package, self.engine, self.job, self.task, self.lock)

    def prepare_rerender(self):
        save(self.job / "work/toolbox-call.json", {"schema_version": 1,
            "operation": "pipeline_render_prepare", "arguments": {"rendering_quality": "preview"}})
        adapter.api(self.package, self.engine, self.job, self.task, self.lock)
        response = bundle.read_json(self.job / "work/toolbox-response.json")["result"]
        self.assertTrue(response["ok"])
        self.assertFalse(response["solver_rerun"])
        self.assertEqual(bundle.read_json(self.job / "work/pipeline/plan.json")["mode"], "render_only")

    def test_render_failure_then_render_only_recovery_never_repeats_or_rewrites_solver(self):
        partial = self.job / "artifacts/.render-partial.bin"
        def first_render_fails(*args, **kwargs):
            role = args[4]
            action = args[8] if len(args) > 8 else kwargs.get("action", "run")
            if (role, action) == ("rendering", "render"):
                self.calls.append((role, action))
                partial.write_bytes(b"preserved interrupted presentation")
                raise adapter.StageFailure("rendering", {"ok": False, "code": "injected_render_failure"})
            return self.fake_presentation(*args, **kwargs)
        with self.assertRaises(adapter.StageFailure):
            self.run_adapter(first_render_fails)
        failed = self.state()
        self.assertEqual((failed["status"], failed["failed_stage"]), ("failed", "rendering"))
        self.assertTrue(failed["simulation"]["ok"])
        self.assertTrue(bundle.read_json(self.job / "artifacts/result-manifest.json")["preserved_simulation"])
        source = Path(failed["simulation"]["result_path"])
        solver_response = self.job / "work/pipeline/calls/simulation-run-result.json"
        original = (self.fingerprint(source), self.fingerprint(solver_response), self.fingerprint(partial))
        original_checkpoint = copy.deepcopy(failed["simulation"])
        self.prepare_rerender()
        self.run_adapter(self.fake_presentation)
        succeeded = self.state()
        self.assertEqual(succeeded["status"], "succeeded")
        self.assertFalse(succeeded["solver_rerun"])
        self.assertEqual(succeeded["simulation"], original_checkpoint)
        self.assertEqual(original, (self.fingerprint(source), self.fingerprint(solver_response), self.fingerprint(partial)))
        self.assertEqual(self.calls.count(("modeling", "run")), 1)
        self.assertEqual(self.calls.count(("simulation", "run")), 1)
        self.assertEqual(self.calls.count(("rendering", "render")), 2)

    def test_export_failure_preserves_simulation_and_partial_files_before_full_resume(self):
        partial = self.job / "artifacts/.export-partial.bin"
        def export_fails(*args, **kwargs):
            role = args[4]
            action = args[8] if len(args) > 8 else kwargs.get("action", "run")
            if (role, action) == ("rendering", "export"):
                self.calls.append((role, action))
                partial.write_bytes(b"preserved interrupted export")
                raise adapter.StageFailure("rendering", {"ok": False, "code": "injected_export_failure"})
            return self.fake_presentation(*args, **kwargs)
        with self.assertRaises(adapter.StageFailure):
            self.run_adapter(export_fails)
        failed = self.state()
        self.assertEqual(failed["failed_stage"], "data_export")
        self.assertNotIn("data", failed)
        self.assertNotIn(("rendering", "render"), self.calls)
        source = Path(failed["simulation"]["result_path"])
        response = self.job / "work/pipeline/calls/simulation-run-result.json"
        original = (self.fingerprint(source), self.fingerprint(response), self.fingerprint(partial))
        self.run_adapter(self.fake_presentation)
        self.assertEqual(self.state()["status"], "succeeded")
        self.assertEqual(original, (self.fingerprint(source), self.fingerprint(response), self.fingerprint(partial)))
        self.assertEqual(self.calls.count(("modeling", "run")), 1)
        self.assertEqual(self.calls.count(("simulation", "run")), 1)

    def test_unsealed_solver_output_is_not_overwritten_on_retry(self):
        def lose_simulation_response(*args, **kwargs):
            result = self.fake_presentation(*args, **kwargs)
            if args[4] == "simulation":
                raise adapter.StageFailure("simulation", {"ok": False, "code": "injected_crash_before_checkpoint"})
            return result
        with self.assertRaises(adapter.StageFailure):
            self.run_adapter(lose_simulation_response)
        failed = self.state()
        self.assertEqual(failed["failed_stage"], "simulation")
        self.assertNotIn("simulation", failed)
        result_path = self.job / "work/physics/artifacts/result.json"
        model_response = self.job / "work/pipeline/calls/modeling-run-result.json"
        solver_response = self.job / "work/pipeline/calls/simulation-run-result.json"
        solver_request = self.job / "work/pipeline/calls/simulation-run.json"
        original = tuple(self.fingerprint(path) for path in
                         (result_path, model_response, solver_response, solver_request))
        with self.assertRaises(adapter.StageFailure) as rejected:
            self.run_adapter(self.fake_presentation)
        self.assertEqual(rejected.exception.result["code"], "stage_incomplete")
        self.assertEqual(original, tuple(self.fingerprint(path) for path in
                         (result_path, model_response, solver_response, solver_request)))
        self.assertEqual(self.calls.count(("modeling", "run")), 1)
        self.assertNotIn(("rendering", "export"), self.calls)
        self.assertEqual(self.state()["status"], "failed")


if __name__ == "__main__":
    unittest.main()
