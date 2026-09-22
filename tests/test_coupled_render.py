"""Quaternion shape pixels, mixed depth and physical attachment rendering."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from physics_demo.video import _compile_renderer, probe_mp4


SNAPSHOT_SOURCE = r'''
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>
#import "video_render_core.h"
int main(int argc,const char *argv[]) {
  @autoreleasepool {
    if(argc!=4) return 2;
    NSDictionary *root=[NSJSONSerialization JSONObjectWithData:
        [NSData dataWithContentsOfFile:@(argv[1])] options:0 error:nil];
    NSDictionary *scene=root[@"scene"],*trajectory=root[@"trajectory"],*frame=trajectory[@"frames"][0];
    const size_t width=320,height=240;
    PhyVideoCamera camera={1,0,1,0,0,0,45,160,120,-5,5};
    if([root[@"auto_camera"] boolValue]) camera=PhyVideoBuildCamera(trajectory[@"frames"],
        trajectory[@"rigid_shapes"] ?: @[],scene[@"colliders"] ?: @[],
        scene[@"world"][@"bounds"][@"min"],scene[@"world"][@"bounds"][@"max"],
        [trajectory[@"particle_radius"] doubleValue],width,height);
    printf("%.12g %.12g %.12g\n",camera.centerU,camera.centerV,camera.scale);
    CGColorSpaceRef space=CGColorSpaceCreateDeviceRGB();
    CGContextRef context=CGBitmapContextCreate(NULL,width,height,8,width*4,space,
        (CGBitmapInfo)kCGBitmapByteOrder32Big|(CGBitmapInfo)kCGImageAlphaPremultipliedLast);
    CGColorSpaceRelease(space);
    if(!context) return 3;
    BOOL rendered=NO;
    if([root[@"legacy_entry"] boolValue]) {
      rendered=PhyVideoDrawFrame(context,frame,frame,trajectory[@"particle_materials"] ?: @[],
          trajectory[@"rigid_shapes"] ?: @[],scene[@"colliders"] ?: @[],
          trajectory[@"mesh_objects"] ?: @[],scene[@"world"][@"bounds"][@"min"],
          scene[@"world"][@"bounds"][@"max"],camera,[trajectory[@"particle_radius"] doubleValue],width,height);
      if(rendered && scene[@"connections"]) rendered=PhyVideoDrawConnections(context,frame,scene,
          trajectory[@"gravity_body_ids"],camera);
    } else rendered=PhyVideoDrawTrajectoryFrame(context,frame,frame,scene,trajectory,camera,width,height);
    if(!rendered) {CGContextRelease(context);return 4;}
    NSData *pixels=[NSData dataWithBytes:CGBitmapContextGetData(context) length:width*height*4];
    [pixels writeToFile:@(argv[3]) atomically:YES];
    CGImageRef image=CGBitmapContextCreateImage(context);
    CGImageDestinationRef output=CGImageDestinationCreateWithURL(
        (__bridge CFURLRef)[NSURL fileURLWithPath:@(argv[2])],CFSTR("public.png"),1,NULL);
    CGImageDestinationAddImage(output,image,NULL);BOOL saved=CGImageDestinationFinalize(output);
    CFRelease(output);CGImageRelease(image);CGContextRelease(context);return saved?0:5;
  }
}
'''


def quat(axis, angle):
    sine = math.sin(angle / 2)
    return [math.cos(angle / 2), *[sine * value for value in axis]]


def multiply(a, b):
    w, x, y, z = a
    v, i, j, k = b
    return [w*v-x*i-y*j-z*k, w*i+x*v+y*k-z*j,
            w*j-x*k+y*v+z*i, w*k+x*j-y*i+z*v]


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("clang"), "macOS renderer")
class CoupledRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="coupled-render-")
        cls.root = Path(cls.directory.name)
        cls.core = Path(__file__).resolve().parents[1] / "physics_demo" / "io"
        source = cls.root / "snapshot.m"
        source.write_text(SNAPSHOT_SOURCE.replace('"video_render_core.h"', json.dumps(str(cls.core / "video_render_core.h"))))
        cls.renderer = _compile_renderer(shutil.which("clang"),
            sources=[source, cls.core / "video_render_core.m", cls.core / "video_render_core.h"],
            frameworks=("Foundation", "CoreGraphics", "ImageIO"), cache_name="coupled-render-test", timeout_seconds=30)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    @staticmethod
    def payload(shape=None, orientation=None):
        has_body = shape is not None
        return {"scene": {"coupling": {}, "world": {"bounds": {"min": [-4, -4, -4], "max": [4, 4, 4]}},
                          "entities": [{"id": "body", "type": "rigid_body", "shape": shape}] if has_body else []},
                "trajectory": {"frames": [{"t": 0, "p": [], "g": [], "r": [[0, 0, 0]] if has_body else [],
                                            "q": [orientation or [1, 0, 0, 0]] if has_body else [], "m": []}],
                               "gravity_body_ids": [], "rigid_ids": ["body"] if has_body else [],
                               "rigid_shapes": [shape] if has_body else [], "mesh_objects": [],
                               "particle_materials": [], "particle_radius": .10}}

    @classmethod
    def liquid_payload(cls, material="water", *, mixed=True, centers=((0, 0, 0),)):
        payload = cls.payload()
        spacing, radius = .18, .64
        points = []
        for center in centers:
            for ix in range(-4, 5):
                for iy in range(-4, 5):
                    for iz in range(-4, 5):
                        offset = [spacing * ix, spacing * iy, spacing * iz]
                        if sum(value * value for value in offset) <= radius * radius:
                            points.append([center[axis] + offset[axis] for axis in range(3)])
        frame = payload["trajectory"]["frames"][0]
        frame["p"] = points
        payload["trajectory"]["particle_materials"] = [material] * len(points)
        payload["trajectory"]["particle_radius"] = .46 * spacing
        if not mixed:
            payload["scene"].pop("coupling")
            frame.pop("q")
        return payload

    def render(self, payload, *, success=True):
        source, png, raw = [self.root / name for name in ("frame.json", "frame.png", "frame.rgba")]
        source.write_text(json.dumps(payload))
        result = subprocess.run([str(self.renderer), str(source), str(png), str(raw)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0 if success else 4, result.stderr)
        return (raw.read_bytes(), [float(v) for v in result.stdout.split()]) if success else (None, None)

    @staticmethod
    def pixel(data, x, y):
        offset = ((239-y)*320+x)*4
        return tuple(data[offset:offset+3])

    def changed_bounds(self, image, background):
        points = [(x, y) for y in range(40, 210) for x in range(30, 290)
                  if max(abs(a-b) for a, b in zip(self.pixel(image,x,y),self.pixel(background,x,y)))>30]
        return max(p[0] for p in points)-min(p[0] for p in points), max(p[1] for p in points)-min(p[1] for p in points)

    def test_quaternion_rotates_box_geometry_not_just_a_glyph(self):
        shape = {"type": "box", "size": [3, .5, .5]}
        background, _ = self.render(self.payload())
        horizontal, _ = self.render(self.payload(shape))
        vertical, _ = self.render(self.payload(shape, quat([0,0,1], math.pi/2)))
        width, height = self.changed_bounds(horizontal, background)
        turned_width, turned_height = self.changed_bounds(vertical, background)
        self.assertGreater(width, 5*height)
        self.assertGreater(turned_height, 5*turned_width)
        self.assertAlmostEqual(width, turned_height, delta=1)

    def test_cylinder_has_caps_and_spin_markings(self):
        shape = {"type": "cylinder", "radius": .8, "height": .3}
        face_on = quat([1,0,0], math.pi/2)
        background, _ = self.render(self.payload())
        edge, _ = self.render(self.payload(shape))
        face, _ = self.render(self.payload(shape, face_on))
        spun, _ = self.render(self.payload(shape, multiply(face_on, quat([0,1,0], math.pi/4))))
        width, height = self.changed_bounds(edge, background)
        face_width, face_height = self.changed_bounds(face, background)
        self.assertGreater(width, 4*height)
        self.assertAlmostEqual(face_width, face_height, delta=2)
        differences = sum(a != b for a,b in zip(face, spun))
        self.assertGreater(differences, 1500)
        self.assertEqual(self.changed_bounds(face, background), self.changed_bounds(spun, background))

    def test_sphere_marking_uses_orientation(self):
        shape = {"type": "sphere", "radius": .8}
        first, _ = self.render(self.payload(shape))
        second, _ = self.render(self.payload(shape, quat([0,0,1], math.pi/2)))
        self.assertGreater(sum(a != b for a,b in zip(first,second)), 1000)

    def test_pivot_annotation_is_fixed_and_axis_follows_body_center(self):
        payload = self.payload({"type":"sphere","radius":.25})
        baseline, _ = self.render(payload)
        payload["scene"]["entities"][0]["pivot"] = {"point":[-2,-1,0],"local_point":[-2,-1,0]}
        anchored, _ = self.render(payload)
        self.assertGreater(self.pixel(anchored,70,75)[0]-self.pixel(baseline,70,75)[0],150)
        self.assertGreater(self.pixel(anchored,110,95)[2]-self.pixel(baseline,110,95)[2],80)
        payload["trajectory"]["frames"][0].update(r=[[0,1,0]],q=[quat([0,0,1],.5)])
        moved, _ = self.render(payload)
        self.assertEqual(self.pixel(moved,70,75),self.pixel(anchored,70,75))
        self.assertLess(self.pixel(moved,110,95)[2],self.pixel(anchored,110,95)[2]-60)
        payload["scene"]["entities"][0]["pivot"]["point"] = [0,0]
        self.render(payload,success=False)

    def test_particles_and_mesh_share_pixel_depth(self):
        payload = self.payload()
        payload["scene"]["entities"] = [{"id":"wall","type":"mesh"}]
        payload["trajectory"]["mesh_objects"] = [{"id":"wall","vertex_start":0,"vertex_count":4,
            "triangles":[[0,1,2],[0,2,3]],"color":[1,0,0]}]
        frame = payload["trajectory"]["frames"][0]
        frame["m"] = [[-1,-1,0],[1,-1,0],[1,1,0],[-1,1,0]]
        frame["p"] = [[0,0,1]]
        front, _ = self.render(payload)
        frame["p"] = [[0,0,-1]]
        rear, _ = self.render(payload)
        self.assertGreater(self.pixel(front,160,120)[2], self.pixel(front,160,120)[0])
        self.assertGreater(self.pixel(rear,160,120)[0], 100)
        self.assertEqual(self.pixel(rear,160,120)[2], 0)

    def test_rotated_local_and_mesh_vertex_attachment_endpoints(self):
        payload = self.payload({"type":"box","size":[.4,.4,.4]},quat([0,0,1],math.pi/2))
        payload["scene"]["entities"].append({"id":"cloth","type":"mesh"})
        payload["trajectory"]["frames"][0]["m"] = [[-2,1,0],[-2,.5,0],[-2.5,.5,0]]
        payload["trajectory"]["mesh_objects"] = [{"id":"cloth","vertex_start":0,"vertex_count":3,
            "triangles":[[0,1,2]],"color":[.1,.3,.8]}]
        baseline, _ = self.render(payload)
        payload["scene"]["connections"] = [{"id":"link","type":"rod","rest_length":2,
            "endpoints":[{"entity":"body","local_point":[1,0,0]},{"entity":"cloth","vertex":0}]}]
        connected, _ = self.render(payload)
        self.assertGreater(self.pixel(connected,115,165)[0]-self.pixel(baseline,115,165)[0], 100)
        self.assertEqual(self.pixel(connected,130,140), self.pixel(baseline,130,140))
        payload["scene"]["connections"][0]["endpoints"][1]["vertex"] = 99
        self.render(payload,success=False)

    def test_solid_spring_is_straight_capsule_with_physical_radius(self):
        payload = self.payload()
        payload["scene"]["entities"] = [{"id":"a","type":"point_mass"},{"id":"b","type":"point_mass"}]
        payload["trajectory"]["gravity_body_ids"] = ["a","b"]
        payload["trajectory"]["frames"][0]["g"] = [[-2,0,0],[2,0,0]]
        payload["scene"]["connections"] = [{"id":"link","type":"spring","entities":["a","b"],
            "rest_length":4,"stiffness":10,"solid":{"radius":.2,"mass":0}}]
        solid, _ = self.render(payload)
        self.assertGreater(self.pixel(solid,160,120)[2], 100)
        self.assertGreater(self.pixel(solid,160,127)[2], 80)
        narrow = copy.deepcopy(payload)
        narrow["scene"]["connections"][0]["solid"]["radius"] = .04
        thinner, _ = self.render(narrow)
        self.assertGreater(self.pixel(solid,160,127)[2]-self.pixel(thinner,160,127)[2], 40)
        del payload["scene"]["connections"][0]["solid"]
        schematic, _ = self.render(payload)
        self.assertNotEqual(solid, schematic)

    def test_nonquaternion_legacy_entry_has_identical_pixels(self):
        payload = self.payload()
        del payload["scene"]["coupling"]
        del payload["trajectory"]["frames"][0]["q"]
        payload["trajectory"]["frames"][0]["p"] = [[-.3,0,0],[0,0,0],[.3,0,0]]
        original, _ = self.render(dict(payload,legacy_entry=True))
        wrapper, _ = self.render(payload)
        self.assertEqual(original,wrapper)

    def test_liquid_surface_is_continuous_order_independent_and_shared(self):
        background, _ = self.render(self.payload())
        payload = self.liquid_payload()
        mixed, _ = self.render(payload)
        legacy, _ = self.render(self.liquid_payload(mixed=False))
        self.assertEqual(mixed, legacy)
        for y in range(104, 137):
            for x in range(144, 177):
                if (x - 160) ** 2 + (y - 120) ** 2 <= 15 ** 2:
                    self.assertGreater(
                        max(abs(a - b) for a, b in zip(self.pixel(mixed, x, y), self.pixel(background, x, y))),
                        20,
                    )
        reversed_payload = copy.deepcopy(payload)
        reversed_payload["trajectory"]["frames"][0]["p"].reverse()
        reversed_payload["trajectory"]["particle_materials"].reverse()
        reordered, _ = self.render(reversed_payload)
        self.assertLess(sum(a != b for a, b in zip(mixed, reordered)), 32)

        separated = self.liquid_payload(centers=((-1.1, 0, 0), (1.1, 0, 0)))
        separated_image, _ = self.render(separated)
        self.assertEqual(self.pixel(separated_image, 160, 120), self.pixel(background, 160, 120))

    def test_liquid_presets_have_distinct_stable_palettes(self):
        images = {name: self.render(self.liquid_payload(name))[0]
                  for name in ("water", "honey", "glue", "molten_lead")}
        colors = {}
        for name, data in images.items():
            samples = [self.pixel(data, x, y) for y in range(114, 127) for x in range(154, 167)]
            colors[name] = tuple(sum(pixel[channel] for pixel in samples) / len(samples)
                                 for channel in range(3))
        water, honey, glue, lead = (colors[name] for name in ("water", "honey", "glue", "molten_lead"))
        self.assertGreater(water[2], water[0] + 35)
        self.assertGreater(honey[0], honey[1] + 25)
        self.assertGreater(honey[1], honey[2] + 20)
        self.assertLess(max(glue) - min(glue), 45)
        self.assertLess(max(lead) - min(lead), 45)
        self.assertGreater(sum(glue) / 3, sum(lead) / 3 + 20)
        for left, right in (("water", "honey"), ("water", "glue"),
                            ("water", "molten_lead"), ("honey", "glue"),
                            ("honey", "molten_lead"), ("glue", "molten_lead")):
            self.assertGreater(sum(a != b for a, b in zip(images[left], images[right])), 1000)

    def test_malformed_quaternions_and_shape_fail_cleanly(self):
        for orientation in ([0,0,0,0],[1,0,0],[2,0,0,0],["bad",0,0,0]):
            self.render(self.payload({"type":"sphere","radius":.5},orientation),success=False)
        self.render(self.payload({"type":"cylinder","radius":.5,"height":-1}),success=False)
        payload = self.payload({"type":"box","size":[1,1,1]})
        payload["trajectory"]["rigid_ids"] = []
        self.render(payload,success=False)

    def test_rotated_geometry_is_in_camera_fit(self):
        shape = {"type":"cylinder","radius":.2,"height":4}
        payload = self.payload(shape,quat([0,0,1],math.pi/2))
        payload["auto_camera"] = True
        _, first = self.render(payload)
        payload["trajectory"]["frames"].append({"p":[],"g":[],"r":[[40,0,0]],
            "q":[quat([0,0,1],math.pi/2)],"m":[]})
        _, whole = self.render(payload)
        self.assertGreater(whole[0],first[0]+10)
        self.assertLess(whole[2],first[2]/5)

    def test_auto_camera_resolves_millimetre_liquid_at_its_own_scale(self):
        payload = self.payload()
        payload["auto_camera"] = True
        payload["scene"]["world"]["bounds"] = {
            "min": [-0.04, 0, -0.04], "max": [0.04, 0.025, 0.04]
        }
        frame = payload["trajectory"]["frames"][0]
        frame["p"] = [[-0.003, 0.012, 0], [0, 0.015, 0], [0.003, 0.012, 0]]
        payload["trajectory"]["particle_materials"] = ["water"] * 3
        payload["trajectory"]["particle_radius"] = 0.000184
        background = copy.deepcopy(payload)
        background["trajectory"]["frames"][0]["p"] = []
        empty, _ = self.render(background)
        rendered, camera = self.render(payload)
        width, height = self.changed_bounds(rendered, empty)
        self.assertGreater(camera[2], 3_000)
        self.assertGreater(width, 70)
        self.assertGreater(height, 35)

    def test_real_mjpeg_mp4_has_all_rotated_frames(self):
        encoder = _compile_renderer(shutil.which("clang"),
            sources=[self.core/"video_mjpeg_renderer.m",self.core/"video_render_core.m",self.core/"video_render_core.h"],
            frameworks=("Foundation","CoreGraphics","ImageIO"),cache_name="coupled-mjpeg-test",timeout_seconds=30)
        payload = self.payload({"type":"cylinder","radius":.6,"height":.2},quat([1,0,0],.6))
        for i in range(1,5):
            frame=copy.deepcopy(payload["trajectory"]["frames"][0])
            frame.update(t=i/10,q=[multiply(quat([1,0,0],.6),quat([0,1,0],i*.3))])
            payload["trajectory"]["frames"].append(frame)
        source,output=self.root/"video.json",self.root/"rotation.mp4"
        source.write_text(json.dumps(payload))
        result=subprocess.run([str(encoder),str(source),str(output),"10"],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        metadata=probe_mp4(output)
        self.assertEqual(metadata["sample_count"],5)
        self.assertEqual(metadata["codec_tag"],"jpeg")


if __name__ == "__main__":
    unittest.main()
