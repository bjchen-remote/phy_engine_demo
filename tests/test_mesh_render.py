"""Pixel-level checks for mesh visibility, independent of simulation dynamics."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from physics_demo.video import _compile_renderer


SNAPSHOT_SOURCE = r'''
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>
#import "video_render_core.h"

int main(int argc, const char *argv[]) {
  @autoreleasepool {
    if (argc != 4) return 2;
    NSDictionary *root = [NSJSONSerialization JSONObjectWithData:
        [NSData dataWithContentsOfFile:@(argv[1])] options:0 error:nil];
    NSDictionary *scene = root[@"scene"], *trajectory = root[@"trajectory"];
    NSArray *frames = trajectory[@"frames"], *minimum = scene[@"world"][@"bounds"][@"min"],
            *maximum = scene[@"world"][@"bounds"][@"max"];
    const size_t width = 320, height = 240;
    PhyVideoCamera camera = PhyVideoBuildCamera(frames, @[], @[], minimum, maximum,
                                                0.03, width, height);
    if ([root[@"identity_camera"] boolValue])
      camera = (PhyVideoCamera){1, 0, 1, 0, 0, 0, 30, 160, 120, -5, 5};
    printf("%.12g %.12g %.12g\n", camera.centerU, camera.centerV, camera.scale);
    CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
    CGContextRef context = CGBitmapContextCreate(NULL, width, height, 8, width * 4,
        space, (CGBitmapInfo)kCGBitmapByteOrder32Big |
                   (CGBitmapInfo)kCGImageAlphaPremultipliedLast);
    CGColorSpaceRelease(space);
    if (!context) return 3;
    BOOL rendered = PhyVideoDrawFrame(context, frames[0], frames[0], @[], @[], @[],
        trajectory[@"mesh_objects"] ?: @[], minimum, maximum, camera, 0.03, width, height);
    if (!rendered) { CGContextRelease(context); return 4; }
    NSData *pixels = [NSData dataWithBytes:CGBitmapContextGetData(context)
                                  length:width * height * 4];
    [pixels writeToFile:@(argv[3]) atomically:YES];
    CGImageRef image = CGBitmapContextCreateImage(context);
    CGImageDestinationRef output = CGImageDestinationCreateWithURL(
        (__bridge CFURLRef)[NSURL fileURLWithPath:@(argv[2])], CFSTR("public.png"), 1, NULL);
    CGImageDestinationAddImage(output, image, NULL);
    BOOL saved = CGImageDestinationFinalize(output);
    CFRelease(output);
    CGImageRelease(image);
    CGContextRelease(context);
    return saved ? 0 : 5;
  }
}
'''


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("clang"), "macOS renderer")
class MeshRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        cls.root = Path(cls.directory.name)
        core = Path(__file__).resolve().parents[1] / "physics_demo" / "io"
        source = cls.root / "snapshot.m"
        source.write_text(SNAPSHOT_SOURCE.replace(
            '"video_render_core.h"', json.dumps(str(core / "video_render_core.h"))))
        cls.renderer = _compile_renderer(
            shutil.which("clang"),
            sources=[source, core / "video_render_core.m", core / "video_render_core.h"],
            frameworks=("Foundation", "CoreGraphics", "ImageIO"),
            cache_name="mesh-render-test", timeout_seconds=20,
        )

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    @staticmethod
    def payload(vertices, objects, *, identity=True):
        return {
            "identity_camera": identity,
            "scene": {"world": {"bounds": {"min": [-4, -4, -4], "max": [4, 4, 4]}}},
            "trajectory": {"frames": [{"p": [], "g": [], "r": [], "m": vertices}],
                           "mesh_objects": objects},
        }

    @staticmethod
    def mesh(start, count, triangles, color):
        return {"id": f"mesh-{start}", "vertex_start": start, "vertex_count": count,
                "triangles": triangles, "color": color, "motion": "static"}

    def render(self, payload, *, success=True):
        source, png, raw = (self.root / name for name in ("frame.json", "frame.png", "frame.rgba"))
        source.write_text(json.dumps(payload))
        process = subprocess.run([str(self.renderer), str(source), str(png), str(raw)],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0 if success else 4, process.stderr)
        if not success:
            return None
        return raw.read_bytes(), [float(item) for item in process.stdout.split()]

    @staticmethod
    def pixel(data, x, y):
        # Bitmap memory starts at the image top, while the camera uses y-up.
        offset = ((239 - y) * 320 + x) * 4
        return tuple(data[offset:offset + 3])

    def test_intersecting_triangles_use_pixel_depth_and_ignore_object_order(self):
        vertices = [[-2, -1, -1], [2, -1, 1], [0, 2, 0],
                    [-2, -1, 1], [2, -1, -1], [0, 2, 0]]
        objects = [self.mesh(0, 3, [[0, 1, 2]], [1, 0, 0]),
                   self.mesh(3, 3, [[0, 1, 2]], [0, 0, 1])]
        image, _ = self.render(self.payload(vertices, objects))
        reversed_image, _ = self.render(self.payload(vertices, objects[::-1]))
        for x, expected_channel in ((137, 2), (182, 0)):
            color = self.pixel(image, x, 120)
            self.assertGreater(color[expected_channel], 80)
            self.assertEqual(color[2 - expected_channel], 0)
            self.assertEqual(color, self.pixel(reversed_image, x, 120))

    def test_ring_hole_exposes_rear_surface(self):
        vertices = [[-2, -2, 1], [2, -2, 1], [2, 2, 1], [-2, 2, 1],
                    [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1],
                    [-3, -3, 0], [3, -3, 0], [3, 3, 0], [-3, 3, 0]]
        triangles = []
        for index in range(4):
            following = (index + 1) % 4
            triangles.extend([[index, following, 4 + index],
                              [following, 4 + following, 4 + index]])
        objects = [self.mesh(0, 8, triangles, [1, 0, 0]),
                   self.mesh(8, 4, [[0, 1, 2], [0, 2, 3]], [0, 0, 1])]
        image, _ = self.render(self.payload(vertices, objects))
        self.assertGreater(self.pixel(image, 160, 120)[2], 100)
        self.assertEqual(self.pixel(image, 160, 120)[0], 0)
        self.assertGreater(self.pixel(image, 205, 120)[0], 100)
        self.assertEqual(self.pixel(image, 205, 120)[2], 0)

    def test_camera_includes_mesh_vertices_over_entire_trajectory(self):
        vertices = [[100, 0, 0], [102, 0, 0], [100, 2, 0]]
        objects = [self.mesh(0, 3, [[0, 1, 2]], [0, 1, 0])]
        scene = self.payload(vertices, objects, identity=False)
        _, first_camera = self.render(scene)
        scene["trajectory"]["frames"].append({"m": [[x + 80, y, z] for x, y, z in vertices]})
        _, whole_camera = self.render(scene)
        self.assertGreater(first_camera[0], 50)
        self.assertGreater(whole_camera[0], first_camera[0])
        self.assertLess(whole_camera[2], first_camera[2] / 10)

    def test_malformed_mesh_fails_without_crashing_or_rendering_blank_success(self):
        mesh = self.mesh(0, 3, [[0, 1, 2]], [1, 0, 0])
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        for field, value in (("triangles", [[0, 1, 99]]), ("triangles", [[0, -1, 2]]),
                             ("triangles", [[0, 0.5, 2]]), ("vertex_start", -1),
                             ("vertex_count", 10), ("color", "red")):
            with self.subTest(field=field, value=value):
                self.render(self.payload(vertices, [dict(mesh, **{field: value})]), success=False)
        self.render(self.payload([["bad", 0, 0], *vertices[1:]], [mesh]), success=False)


if __name__ == "__main__":
    unittest.main()
