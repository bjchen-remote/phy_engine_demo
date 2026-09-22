import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import registry
from smoke import validate_result


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.package = self.root / "package"
        self.package.mkdir()
        self.registry = self.root / "registry"
        registry.write_json(self.package / "toolbox.json", {"schema_version": 1,
            "id": "fixture", "version": "1", "entrypoint": "run.py", "capabilities": ["fixture"]})
        (self.package / "run.py").write_text("print('version one')")

    def tearDown(self):
        self.temporary.cleanup()

    def test_publish_activate_and_rollback_preserve_old_snapshot(self):
        first = registry.publish(self.package, self.registry, True)
        old = registry.verify(self.registry, first)
        (self.package / "run.py").write_text("print('version two')")
        second = registry.publish(self.package, self.registry, True)
        self.assertNotEqual(first["digest"], second["digest"])
        self.assertEqual((old / "run.py").read_text(), "print('version one')")
        registry.activate(self.registry, first["digest"])
        self.assertEqual(registry.read_json(self.registry / "active.json"), first)

    def test_publish_without_activate_preserves_selection(self):
        first = registry.publish(self.package, self.registry, True)
        (self.package / "run.py").write_text("print('version two')")
        registry.publish(self.package, self.registry)
        self.assertEqual(registry.read_json(self.registry / "active.json"), first)

    def test_republish_identical_content_is_idempotent(self):
        first = registry.publish(self.package, self.registry, True)
        self.assertEqual(registry.publish(self.package, self.registry, True), first)
        self.assertEqual(len(list((self.registry / "versions").iterdir())), 1)

    def test_symlink_rejected_and_active_pointer_unchanged(self):
        first = registry.publish(self.package, self.registry, True)
        (self.package / "link").symlink_to(self.package / "run.py")
        with self.assertRaises(ValueError):
            registry.publish(self.package, self.registry, True)
        self.assertEqual(registry.read_json(self.registry / "active.json"), first)

    def test_tampered_version_cannot_be_activated(self):
        first = registry.publish(self.package, self.registry, True)
        (self.registry / "versions" / first["digest"] / "run.py").write_text("changed")
        with self.assertRaises(ValueError):
            registry.activate(self.registry, first["digest"])

    def test_entrypoint_escape_and_registry_containment_rejected(self):
        value = registry.read_json(self.package / "toolbox.json")
        value["entrypoint"] = "../outside.py"
        registry.write_json(self.package / "toolbox.json", value)
        (self.root / "outside.py").write_text("print('outside')")
        with self.assertRaises(ValueError):
            registry.publish(self.package, self.registry)
        with self.assertRaises(ValueError):
            registry.publish(self.package, self.package / "registry")

    def test_result_validation_rejects_corruption_escape_and_false_success(self):
        artifacts = self.root / "artifacts"; artifacts.mkdir()
        video = artifacts / "video.mp4"; video.write_bytes(b"\x00\x00\x00\x18ftypmp42data")
        value = {"schema_version": 1, "status": "succeeded", "verification": {"passed": True},
                 "outputs": [{"path": "video.mp4", "sha256": registry.sha256(video), "media_type": "video/mp4"}]}
        registry.write_json(artifacts / "result-manifest.json", value)
        self.assertEqual(validate_result(self.root, 100)[0], video)
        for change in ({"path": "../../outside.mp4"}, {"sha256": "wrong"}):
            invalid = copy.deepcopy(value); invalid["outputs"][0].update(change)
            registry.write_json(artifacts / "result-manifest.json", invalid)
            with self.assertRaises(ValueError):
                validate_result(self.root, 100)
        value["verification"]["passed"] = False
        registry.write_json(artifacts / "result-manifest.json", value)
        with self.assertRaises(ValueError):
            validate_result(self.root, 100)


class PhysicsProtocolTests(unittest.TestCase):
    @staticmethod
    def api(job, operation, arguments):
        registry.write_json(job/'work/toolbox-call.json',{'schema_version':1,
            'operation':operation,'arguments':arguments})
        subprocess.run([sys.executable,str(ROOT/'physics/toolbox_adapter.py'),
            '--phase','api','--task',str(job/'task.json')],check=True,
            capture_output=True,timeout=10)
        return registry.read_json(job/'work/toolbox-response.json')['result']

    def test_self_gravity_prepare_probe_and_help_are_discoverable(self):
        with tempfile.TemporaryDirectory() as temporary:
            job=Path(temporary);(job/'work').mkdir()
            registry.write_json(job/'task.json',{'schema_version':1,
                'request':{'text':'Liquid volumes with mutual attraction','plan_required':True},
                'limits':{'wall_time_seconds':180}})
            model=json.loads((ROOT.parent/'examples/self_gravitating_liquid.json').read_text())
            help_result=self.api(job, 'help',{'topic':'particle-gravity'})
            self.assertTrue(help_result['ok'])
            ready=self.api(job, 'physics_prepare',{'scene_json':json.dumps(model)})
            self.assertTrue(ready['ok'] and ready['ready_to_simulate'],ready)
            self.assertIn('particle_gravity',ready['agent_report'])
            subprocess.run([sys.executable,str(ROOT/'physics/toolbox_adapter.py'),
                '--phase','probe','--task',str(job/'task.json')],check=True,
                capture_output=True,timeout=10)
            probe=registry.read_json(job/'capability.json')
            self.assertTrue(probe['supported'])
            self.assertEqual(probe['estimated_seconds'],ready['agent_report']['estimated_wall_time_s']['p90'])

    def test_ballistic_lava_model_builds_and_prepares_through_module_api(self):
        with tempfile.TemporaryDirectory() as temporary:
            job=Path(temporary);(job/'work').mkdir()
            registry.write_json(job/'task.json',{'schema_version':1,
                'request':{'text':'Finite liquid launch','plan_required':True},
                'limits':{'wall_time_seconds':180}})
            self.assertTrue(self.api(job,'help',{'topic':'ballistic-burst'})['ok'])
            built=self.api(job,'physics_system',{'spec_json':json.dumps(
                {'type':'ballistic_burst','preset':'lava','wall_time_s':180})})
            self.assertTrue(built['ok'],built)
            prepared=self.api(job,'physics_prepare',{'scene_json':built['scene_json']})
            self.assertTrue(prepared['ok'] and prepared['ready_to_simulate'],prepared)
            self.assertFalse(prepared['scene']['force_fields'])
            self.assertTrue(all(e['preset']=='lava' for e in prepared['scene']['entities']))

    def probe(self, text, budget=30):
        with tempfile.TemporaryDirectory() as temporary:
            job = Path(temporary)
            registry.write_json(job / "task.json", {"schema_version": 1,
                "request": {"text": text}, "limits": {"wall_time_seconds": budget}})
            subprocess.run([sys.executable, str(ROOT / "physics/toolbox_adapter.py"),
                            "--phase", "probe", "--task", str(job / "task.json")], check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            return registry.read_json(job / "capability.json")

    def test_supported_physics_scenes(self):
        for text in ("快速模拟一滴水落到地面", "高清模拟水滴落到地板", "双摆运动", "展示三体轨道视频", "生成三体相互引力视频，使用随机初值"):
            self.assertTrue(self.probe(text)["supported"])

    def test_random_three_body_is_reproducible_and_preserves_zero_bulk_motion(self):
        sys.path.insert(0, str(ROOT/'physics'))
        import run_simulation
        first = run_simulation.route('随机三体运动', seed=1)[1]
        self.assertEqual(first, run_simulation.route('随机三体运动', seed=1)[1])
        self.assertNotEqual(first, run_simulation.route('随机三体运动', seed=2)[1])
        for field in ('position', 'velocity'):
            for axis in range(3):
                self.assertAlmostEqual(sum(b[field][axis] for b in first['entities']), 0)

    def test_unsupported_commands_and_insufficient_budget(self):
        self.assertFalse(self.probe("执行任意外部程序")['supported'])
        self.assertEqual(self.probe("双摆", 1)["reason"], "insufficient_time_budget")

    def test_packaged_engine_matches_repository_source(self):
        result = subprocess.run([sys.executable, str(ROOT / "build_physics.py"), "--check"],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ModelingAPITests(unittest.TestCase):
    def test_prepare_records_requested_parameters_and_invalidates_stale_scene(self):
        with tempfile.TemporaryDirectory() as folder:
            job=Path(folder);(job/'work').mkdir()
            registry.write_json(job/'task.json',{'schema_version':1,'request':{'text':'custom model','plan_required':True},'limits':{'wall_time_seconds':180}})
            def call(operation, arguments):
                registry.write_json(job/'work/toolbox-call.json',{'schema_version':1,'operation':operation,'arguments':arguments})
                subprocess.run([sys.executable,str(ROOT/'physics/toolbox_adapter.py'),'--phase','api','--task',str(job/'task.json')],check=True,capture_output=True,timeout=8)
                # API documents allow up to 1 MB, unlike the small registry manifest.
                return json.loads((job/'work/toolbox-response.json').read_text())['result']
            model=call('physics_system',{'spec_json':json.dumps({'type':'double_pendulum','lengths':[1.3,.7],'masses':[1,2],'angles':[.5,.8],'duration':3})})
            prepared=call('physics_prepare',{'scene_json':model['scene_json']})
            self.assertTrue(prepared['ready_to_simulate'])
            scene=registry.read_json(job/'work/prepared-scene.json')['scene']
            self.assertEqual([r['rest_length'] for r in scene['connections']],[1.3,.7])
            self.assertEqual([b['mass'] for b in scene['entities'] if not b.get('fixed')],[1,2])
            self.assertEqual(scene['budget']['wall_time_s'],180)
            self.assertEqual(scene['budget']['validation'],'visual')
            scene['budget']['validation']='strict'
            self.assertTrue(call('physics_prepare',{'scene_json':json.dumps(scene)})['ok'])
            self.assertEqual(registry.read_json(job/'work/prepared-scene.json')['scene']['budget']['validation'],'strict')
            self.assertEqual(call('context',{})['code'],'no_previous_verified_model')
            registry.write_json(job/'work/previous-context.json',{'schema_version':1,'previous_request':'original model','model':{'scene':scene}})
            restored=json.loads(call('context',{})['scene_json'])
            self.assertEqual(restored,scene)
            restored['world']['duration'] *= 3
            for field in restored['force_fields']:
                if field['end_time'] == scene['world']['duration']:
                    field['end_time'] = restored['world']['duration']
            followup=call('physics_prepare',{'scene_json':json.dumps(restored)})
            self.assertTrue(followup['ready_to_simulate'])
            saved=registry.read_json(job/'work/prepared-scene.json')['scene']
            self.assertEqual(saved['world']['duration'],9)
            self.assertEqual(saved['entities'],scene['entities'])
            self.assertEqual(saved['connections'],scene['connections'])
            for topic in ('tools','schema'):
                self.assertTrue(call('help',{'topic':topic})['ok'])
            bad=call('physics_prepare',{'scene_json':'{}'})
            self.assertFalse(bad['ok']);self.assertFalse((job/'work/prepared-scene.json').exists())
            for operation, arguments in [('physics_simulate',{'output_dir':'/tmp/escape'}),('physics_query',{'result_path':'/etc/passwd'})]:
                with self.assertRaises(subprocess.CalledProcessError): call(operation, arguments)
            registry.write_json(job/'task.json',{'schema_version':1,'request':{'text':'三体运动','plan_required':True},'limits':{'wall_time_seconds':30}})
            subprocess.run([sys.executable,str(ROOT/'physics/toolbox_adapter.py'),'--phase','probe','--task',str(job/'task.json')],check=True,capture_output=True)
            self.assertEqual(registry.read_json(job/'capability.json')['reason'],'model_not_prepared')


class VideoBudgetProtocolTests(unittest.TestCase):
    def test_compression_failure_is_not_a_physics_failure_or_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            job=Path(directory);(job/'work').mkdir();(job/'artifacts').mkdir()
            task={'schema_version':1,'task_id':'byte-budget-check',
                'request':{'text':'short physical video','plan_required':True},
                'limits':{'wall_time_seconds':30,'max_output_bytes':1024,'network':False}}
            registry.write_json(job/'task.json',task)
            scene=json.loads((ROOT.parent/'examples/three_body.json').read_text())
            scene['world'].update(duration=.04,output_fps=30)
            registry.write_json(job/'work/toolbox-call.json',{'schema_version':1,
                'operation':'physics_prepare','arguments':{'scene_json':json.dumps(scene)}})
            for phase in ('api','run'):
                completed=subprocess.run([sys.executable,str(ROOT/'physics/toolbox_adapter.py'),
                    '--phase',phase,'--task',str(job/'task.json')],capture_output=True,text=True,timeout=30)
                self.assertEqual(completed.returncode,0,completed.stderr)
            manifest=registry.read_json(job/'artifacts/result-manifest.json')
            self.assertEqual(manifest['status'],'failed')
            self.assertEqual(manifest['failure']['stage'],'presentation')
            self.assertFalse(manifest['failure']['retryable'])
            result=json.loads((job/'work/physics/artifacts/summary.json').read_text())
            self.assertTrue(result['ok'])
            self.assertFalse((job/'artifacts/simulation.mp4').exists())


if __name__ == "__main__":
    unittest.main()
