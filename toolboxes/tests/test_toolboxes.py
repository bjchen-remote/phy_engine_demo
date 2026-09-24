import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

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

    def test_cone_probe_uses_scene_runtime_estimate(self):
        short = self.probe("一滴水落到圆锥上", 30)
        sufficient = self.probe("一滴水落到圆锥上", 180)
        self.assertFalse(short["supported"])
        self.assertEqual(short["reason"], "insufficient_time_budget")
        self.assertTrue(sufficient["supported"])
        self.assertGreater(sufficient["estimated_seconds"], 30)

    def test_random_three_body_is_reproducible_and_preserves_zero_bulk_motion(self):
        sys.path.insert(0, str(ROOT/'physics'))
        import run_simulation
        first = run_simulation.route('随机三体运动', seed=1)[1]
        self.assertEqual(first, run_simulation.route('随机三体运动', seed=1)[1])
        self.assertNotEqual(first, run_simulation.route('随机三体运动', seed=2)[1])
        for field in ('position', 'velocity'):
            for axis in range(3):
                self.assertAlmostEqual(sum(b[field][axis] for b in first['entities']), 0)

    def test_water_drop_quick_route_preserves_size_and_splash_intent(self):
        sys.path.insert(0, str(ROOT/'physics'))
        import run_simulation

        for prompt in (
            '一滴半径3毫米的水滴落到地面',
            '一滴三毫米水滴落到地面',
            '直径 6 mm 的水滴落在干地板并飞溅',
            '3 mm water drop onto the floor',
            '3-mm water droplet onto the floor',
            '3mmwaterdrop onto the floor',
            'a water droplet with radius 0.003 m hits the floor',
            'water droplet diameter 6 onto the floor',
            'water droplet diameter of about 6 onto the floor',
            '半径为3的水滴落到地面',
            '一滴水落到圆锥上，直径 6 mm',
        ):
            with self.subTest(prompt=prompt):
                with self.assertRaises(run_simulation.UnsupportedRequest) as raised:
                    run_simulation.route(prompt)
                self.assertIn('author scene_json', str(raised.exception))
                self.assertIn('radius/diameter', str(raised.exception))
        self.assertFalse(run_simulation._has_explicit_size('3min'))

        for prompt in (
            '一滴水落到干燥地面，产生明显水花',
            'water droplet splashes on dry floor',
        ):
            with self.subTest(prompt=prompt):
                route_name, scene = run_simulation.route(prompt)
                self.assertEqual(route_name, 'water_droplet_ground_splash')
                self.assertEqual(scene['entities'][0]['shape']['radius'], 0.32)
        for prompt in (
            '细水珠落到干燥地面飞溅',
            '毫米级水滴落到干燥地面产生水花',
            'microdroplet splashes on dry floor',
        ):
            with self.subTest(prompt=prompt):
                route_name, scene = run_simulation.route(prompt)
                self.assertEqual(route_name, 'water_droplet_ground_dry')
                self.assertEqual(scene['entities'][0]['shape']['radius'], 0.003)

    def test_prepared_short_water_impact_gets_watchable_video(self):
        sys.path.insert(0, str(ROOT/'physics'))
        import run_simulation
        scene=json.loads((ROOT.parent/'examples/droplet_ground.json').read_text())
        default_name, default_scene=run_simulation.route('一滴水落到地面并飞溅')
        fast_name, fast_scene=run_simulation.route('快速预览一滴水落到地面')
        dry_name, dry_scene=run_simulation.route('一滴水落到干燥地面')
        micro_name, micro_scene=run_simulation.route('细水珠落到地面飞溅')
        wet_name, wet_scene=run_simulation.route('细水珠落到有水膜的地面飞溅')
        cone_name, cone_scene=run_simulation.route('一滴水落到圆锥上')
        self.assertEqual(default_name,'water_droplet_ground_macro_wet')
        self.assertEqual(fast_name,'water_droplet_ground_splash')
        self.assertEqual(default_scene['entities'][1]['shape']['size'][1],.04)
        self.assertEqual(dry_name,'water_droplet_ground_dry')
        self.assertEqual(micro_name,'water_droplet_ground_dry')
        self.assertEqual(wet_name,'water_droplet_ground_micro_wet')
        self.assertEqual(cone_name,'water_droplet_cone')
        self.assertEqual(default_scene['world']['duration'],.8)
        self.assertEqual(fast_scene['world']['duration'],.8)
        self.assertEqual(default_scene['world']['output_fps'],60)
        self.assertEqual(dry_scene['world']['output_fps'],120)
        self.assertEqual(micro_scene['world']['output_fps'],120)
        self.assertEqual(wet_scene['world']['output_fps'],120)
        self.assertEqual(dry_scene['world']['duration'],.3)
        self.assertEqual(len(default_scene['entities']),2)
        self.assertEqual(len(fast_scene['entities']),1)
        self.assertEqual(len(dry_scene['entities']),1)
        self.assertEqual(default_scene['entities'][0]['shape']['radius'],.32)
        self.assertEqual(dry_scene['entities'][0]['shape']['radius'],.003)
        self.assertEqual(len(micro_scene['entities']),1)
        self.assertEqual(len(wet_scene['entities']),2)
        self.assertEqual(cone_scene['entities'][1]['color'],[.79,.49,.24])
        with tempfile.TemporaryDirectory() as temporary:
            artifacts=Path(temporary)
            def simulated(_scene, path, *, make_video):
                self.assertTrue(make_video)
                (path/'simulation.mp4').write_bytes(b'video')
                return {'ok':True,'quality_gate':{'passed':True}}
            with patch('physics_demo.runner.simulate',side_effect=simulated), \
                 patch.object(run_simulation,'_publish_watchable_video',
                              return_value={'presentation':{'playback_duration_s':6.8}}) as publish, \
                 redirect_stdout(StringIO()):
                self.assertEqual(run_simulation.simulate_scene(scene,artifacts,
                    request_text='一滴水落到地面'),0)
                publish.assert_called_once()
                publish.reset_mock()
                self.assertEqual(run_simulation.simulate_scene(scene,artifacts,
                    request_text='一滴水落到地面，原速播放'),0)
                publish.assert_not_called()
        self.assertFalse(run_simulation._short_water_impact(
            json.loads((ROOT.parent/'examples/three_body.json').read_text())))

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
