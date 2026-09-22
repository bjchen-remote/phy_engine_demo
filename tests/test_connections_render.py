"""Connection pixels and real MP4s, independent of the dynamics solver."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from physics_demo.video import _compile_renderer, probe_mp4


SNAPSHOT_SOURCE = r'''
#import <Foundation/Foundation.h>
#import "video_render_core.h"
int main(int argc, const char *argv[]) {
  @autoreleasepool {
    if (argc != 3) return 2;
    NSDictionary *root = [NSJSONSerialization JSONObjectWithData:
        [NSData dataWithContentsOfFile:@(argv[1])] options:0 error:nil];
    NSDictionary *scene = root[@"scene"], *trajectory = root[@"trajectory"];
    NSDictionary *frame = trajectory[@"frames"][0];
    PhyVideoCamera camera = {1, 0, 1, 0, 0, 0, 45, 160, 120, -5, 5};
    if ([root[@"floor_test"] boolValue]) {camera.sinePitch=.5;camera.cosinePitch=sqrt(.75);}
    CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
    CGContextRef context = CGBitmapContextCreate(NULL, 320, 240, 8, 320 * 4,
        space, (CGBitmapInfo)kCGBitmapByteOrder32Big |
                   (CGBitmapInfo)kCGImageAlphaPremultipliedLast);
    CGColorSpaceRelease(space);
    if (!context) return 3;
    BOOL rendered = PhyVideoDrawFrame(context, frame, frame, trajectory[@"particle_materials"] ?: @[], @[], scene[@"colliders"] ?: @[], @[],
        scene[@"world"][@"bounds"][@"min"], scene[@"world"][@"bounds"][@"max"], camera, 0.03, 320, 240);
    if (rendered && ![root[@"skip_overlay"] boolValue])
      rendered = PhyVideoDrawConnections(context, frame, scene,
          trajectory[@"gravity_body_ids"], camera);
    if (!rendered) { CGContextRelease(context); return 4; }
    NSData *pixels = [NSData dataWithBytes:CGBitmapContextGetData(context)
                                  length:320 * 240 * 4];
    BOOL saved = [pixels writeToFile:@(argv[2]) atomically:YES];
    CGContextRelease(context);
    return saved ? 0 : 5;
  }
}
'''


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("clang"), "macOS renderer")
class ConnectionRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        cls.core = Path(__file__).resolve().parents[1] / "physics_demo" / "io"
        source = cls.root / "snapshot.m"
        source.write_text(SNAPSHOT_SOURCE.replace(
            '"video_render_core.h"', json.dumps(str(cls.core / "video_render_core.h"))))
        cls.renderer = _compile_renderer(
            shutil.which("clang"),
            sources=[source, cls.core / "video_render_core.m", cls.core / "video_render_core.h"],
            frameworks=("Foundation", "CoreGraphics"),
            cache_name="connections-render-test", timeout_seconds=20,
        )

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    @staticmethod
    def payload(kind="spring", rest=3):
        link = {"id": "link", "type": kind, "entities": ["anchor", "mass"], "rest_length": rest}
        if kind == "spring":
            link.update(stiffness=10, damping=0)
        return {
            "scene": {"world": {"bounds": {"min": [-4, -4, -4], "max": [4, 4, 4]}},
                      "entities": [{"id": "anchor", "type": "point_mass", "fixed": True},
                                   {"id": "mass", "type": "point_mass", "fixed": False}],
                      "connections": [link]},
            "trajectory": {"frames": [{"t": 0, "p": [], "g": [[-2, 0, 0], [2, 0, 0]], "r": []}],
                           "gravity_body_ids": ["anchor", "mass"]},
        }

    def test_distant_walls_do_not_change_visible_ground_grid_scale(self):
        payload=self.payload();payload["skip_overlay"]=True;payload["floor_test"]=True
        payload["trajectory"]["frames"][0]["g"]=[]
        payload["scene"]["colliders"]=[{"type":"plane","normal":[0,1,0],"offset":0}]
        payload["scene"]["world"]["bounds"]={"min":[-50,-1,-50],"max":[50,4,50]}
        first=self.render(payload)
        payload["scene"]["world"]["bounds"]={"min":[-100,-1,-100],"max":[100,4,100]}
        self.assertEqual(first,self.render(payload))

    def test_lava_has_warm_opaque_pixels_instead_of_metallic_grey(self):
        payload = self.payload()
        payload["skip_overlay"] = True
        frame = payload["trajectory"]["frames"][0]
        frame["g"] = []
        frame["p"] = [[x*.05, y*.05, 0] for x in range(-3,4) for y in range(-3,4)]
        payload["trajectory"]["particle_materials"] = ["lava"] * len(frame["p"])
        lava = self.render(payload)
        payload["trajectory"]["particle_materials"] = ["molten_lead"] * len(frame["p"])
        metal = self.render(payload)
        def warm_count(data):
            return sum(data[i]>100 and data[i]>2*data[i+1] and data[i]>3*data[i+2]
                       for i in range(0,len(data),4))
        self.assertGreater(warm_count(lava),40)
        self.assertEqual(warm_count(metal),0)

    def render(self, payload, *, success=True):
        source, raw = self.root / "frame.json", self.root / "frame.rgba"
        source.write_text(json.dumps(payload))
        result = subprocess.run([str(self.renderer), str(source), str(raw)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0 if success else 4, result.stderr)
        return raw.read_bytes() if success else None

    @staticmethod
    def pixel(data, x, y):
        offset = ((239 - y) * 320 + x) * 4
        return tuple(data[offset:offset + 3])

    def test_old_scene_pixels_are_identical_when_overlay_is_absent(self):
        payload = self.payload()
        del payload["scene"]["connections"]
        image = self.render(payload)
        payload["skip_overlay"] = True
        self.assertEqual(image, self.render(payload))

    def test_types_have_distinct_geometry_and_true_endpoint_mounts(self):
        images = {kind: self.render(self.payload(kind, 5)) for kind in ("spring", "rod", "rope")}
        self.assertEqual(len(set(images.values())), 3)
        for data in images.values():
            # Endpoint centres stay at the recorded x=+-2, irrespective of rest length.
            self.assertGreater(min(self.pixel(data, 70, 120)), 190)
            mass = self.pixel(data, 250, 120)
            self.assertGreater(mass[0], 230)
            self.assertLess(mass[2], 100)
        rod = images["rod"]
        self.assertGreater(min(self.pixel(rod, 160, 120)), 160)
        # A slack rope bends below the direct segment; the rod stays on it.
        rope = images["rope"]
        self.assertGreater(self.pixel(rope, 160, 108)[0], 220)
        self.assertLess(self.pixel(rod, 160, 108)[0], 100)
        # Spring windings occupy both sides of the centre line.
        spring = images["spring"]
        for y_range in (range(113, 118), range(123, 128)):
            self.assertTrue(any(self.pixel(spring, x, y)[1] > 170 and
                                self.pixel(spring, x, y)[2] > 180
                                for x in range(100, 220) for y in y_range))

    def test_metadata_ids_control_endpoint_mapping(self):
        payload = self.payload()
        original = self.render(payload)
        payload["trajectory"]["gravity_body_ids"].reverse()
        payload["trajectory"]["frames"][0]["g"].reverse()
        self.assertEqual(original, self.render(payload))

    def test_slack_uses_world_length_and_taut_rope_is_straight(self):
        payload = self.payload("rope", 4)
        taut = self.render(payload)
        self.assertGreater(self.pixel(taut, 160, 120)[0], 220)
        payload["trajectory"]["frames"][0]["g"] = [[-1, 0, -2], [1, 0, 2]]
        # Projected separation is only 2 m, but 3D length exceeds the 4 m rope.
        projected = self.render(payload)
        self.assertGreater(self.pixel(projected, 160, 120)[0], 220)

    def test_malformed_connection_fails_instead_of_drawing_wrong_endpoints(self):
        for field, value in (("entities", ["anchor", "unknown"]),
                             ("entities", ["anchor", "anchor"]),
                             ("rest_length", 0), ("type", "unknown")):
            payload = self.payload()
            payload["scene"]["connections"][0][field] = value
            self.render(payload, success=False)
        payload = self.payload()
        payload["trajectory"]["gravity_body_ids"] = ["anchor", "anchor"]
        self.render(payload, success=False)
        payload = self.payload()
        payload["trajectory"]["frames"][0]["g"][0] = ["bad", 0, 0]
        self.render(payload, success=False)
        payload = self.payload()
        del payload["scene"]["entities"][0]["id"]
        self.render(payload, success=False)
        payload = self.payload()
        payload["scene"]["entities"][0]["fixed"] = []
        self.render(payload, success=False)

    def test_real_mjpeg_mp4_contains_all_connection_frames(self):
        encoder = _compile_renderer(
            shutil.which("clang"),
            sources=[self.core / "video_mjpeg_renderer.m", self.core / "video_render_core.m",
                     self.core / "video_render_core.h"],
            frameworks=("Foundation", "CoreGraphics", "ImageIO"),
            cache_name="connections-mjpeg-test", timeout_seconds=20,
        )
        payload = self.payload()
        for index in range(1, 4):
            frame = copy.deepcopy(payload["trajectory"]["frames"][0])
            frame.update(t=index / 10, g=[[-2, 0, 0], [2 - index * 0.3, 0, 0]])
            payload["trajectory"]["frames"].append(frame)
        source, output = self.root / "video.json", self.root / "simulation.mp4"
        source.write_text(json.dumps(payload))
        process = subprocess.run([str(encoder), str(source), str(output), "10"],
                                 capture_output=True, text=True, timeout=20)
        self.assertEqual(process.returncode, 0, process.stderr)
        metadata = probe_mp4(output)
        self.assertEqual(metadata["sample_count"], 4)
        self.assertEqual(metadata["codec_tag"], "jpeg")


if __name__ == "__main__":
    unittest.main()
