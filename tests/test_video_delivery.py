"""Media limits change presentation only; physics and duration are immutable."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'toolboxes/physics'))
from delivery import publish_video
from physics_demo.runner import load_scene,simulate
from physics_demo.io.video import encode_bounded_mp4,encode_watchable_mp4,VideoEncodingError


def video_stream(path):
    result=subprocess.run(['ffprobe','-v','error','-count_frames','-select_streams','v:0',
        '-show_entries','stream=codec_name,codec_tag_string,pix_fmt,nb_read_frames,duration',
        '-of','json',str(path)],capture_output=True,text=True,check=True)
    return json.loads(result.stdout)['streams'][0]


class VideoDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.source=cls.root/'source';scene=load_scene(ROOT/'examples/three_body.json')
        scene['world'].update(duration=.3,dt=.005,output_fps=30)
        cls.summary=simulate(scene,cls.source,30,make_video=False)
        cls.assertion=cls.summary['ok']
        cls.metadata=encode_bounded_mp4(cls.source/'result.json',cls.source/'simulation.mp4',30,2_000_000,30)
        cls.summary['artifacts']['video']=cls.metadata

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_fit_preserves_every_frame_duration_and_source_hashes(self):
        self.assertTrue(self.assertion)
        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.iterdir() if p.is_file()}
        cap=(self.source/'simulation.mp4').stat().st_size*2//3
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'simulation.mp4';result=publish_video(self.source,path,self.summary,cap,30)
            self.assertTrue(result['compressed']);self.assertFalse(result['solver_rerun'])
            self.assertLessEqual(path.stat().st_size,cap)
            probe=video_stream(path)
            self.assertEqual(probe['codec_name'],'h264')
            self.assertEqual(probe['codec_tag_string'],'avc1')
            self.assertEqual(probe['pix_fmt'],'yuv420p')
            self.assertEqual(int(probe['nb_read_frames']),self.metadata['sample_count'])
            self.assertAlmostEqual(float(probe['duration']),self.metadata['duration_s'],delta=.002)
        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.source.iterdir() if p.is_file()})

    def test_small_mjpeg_video_is_transcoded_for_qq(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'simulation.mp4'
            result=publish_video(self.source,path,self.summary,2_000_000,30)
            self.assertTrue(result['compressed'])
            self.assertNotEqual(path.read_bytes(),(self.source/'simulation.mp4').read_bytes())
            self.assertEqual(video_stream(path)['codec_name'],'h264')
            self.assertAlmostEqual(result['duration_s'],self.metadata['duration_s'],delta=.002)
            self.assertEqual(result['physical_duration_s'],self.metadata['duration_s'])

    def test_valid_h264_source_is_copied(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            first=root/'first.mp4'
            publish_video(self.source,first,self.summary,2_000_000,30)
            second_source=root/'h264';second_source.mkdir()
            (second_source/'simulation.mp4').write_bytes(first.read_bytes())
            second=root/'second.mp4'
            result=publish_video(second_source,second,self.summary,2_000_000,30)
            self.assertFalse(result['compressed'])
            self.assertEqual(second.read_bytes(),first.read_bytes())

    def test_impossible_budget_never_publishes_partial_video(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'simulation.mp4'
            with self.assertRaises(VideoEncodingError):publish_video(self.source,path,self.summary,1024,30)
            self.assertFalse(path.exists());self.assertFalse(path.with_name('.delivery-video.tmp.mp4').exists())

    def test_invalid_limits_and_deadline_do_not_produce_deliverables(self):
        with tempfile.TemporaryDirectory() as folder:
            for cap in (True,-1,0,1023,1.5):
                with self.assertRaises(VideoEncodingError):encode_bounded_mp4(self.source/'result.json',Path(folder)/'invalid.mp4',30,cap,30)
            with self.assertRaises(VideoEncodingError):encode_bounded_mp4(self.source/'result.json',Path(folder)/'timeout.mp4',30,100000,-1)

    def test_slow_motion_presentation_keeps_retimed_frame_count(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source';source.mkdir()
            (source/'result.json').write_bytes((self.source/'result.json').read_bytes())
            segments=[{'physical_start_s':0,'physical_end_s':.3,'playback_duration_s':1.5}]
            metadata=encode_watchable_mp4(source/'result.json',source/'simulation.mp4',fps=30,segments=segments,max_bytes=2_000_000)
            cap=(source/'simulation.mp4').stat().st_size*2//3
            result=publish_video(source,root/'simulation.mp4',{'artifacts':{'video':metadata}},cap,30)
            self.assertTrue(result['compressed']);self.assertEqual(result['sample_count'],45)
            self.assertEqual(result['duration_s'],1.5)
            self.assertEqual(video_stream(root/'simulation.mp4')['codec_name'],'h264')
