"""Read-only shape presentation, independent of simulation and measurements.

The native surface drawer receives the original triangle set, including every
disconnected component. A camera turntable is a display operation, not a saved
physics trajectory. No Python imaging/ML package is imported by this module.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from .artifacts import PresentationError, atomic_write, canonical, digest, read_document


MAX_VERTICES = 1_000_000
MAX_TRIANGLES = 2_000_000
MAX_RAW_BYTES = 512 * 1024 * 1024
DEFAULT_PARAMETERS = {"width": 640, "height": 640, "fps": 15,
                      "duration_seconds": 6, "pitch_degrees": 12,
                      "start_yaw_degrees": 0}


# Only this fixed driver is compiled. Mesh content remains data in a bounded
# JSON document and never contributes to compiler flags or executable source.
_NATIVE_DRIVER = r'''
#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <CoreText/CoreText.h>
#import "video_render_core.h"
#include <math.h>
#include <stdio.h>

static void Label(CGContextRef context, NSString *text, double x, double y,
                  double size) {
  CTFontRef font=CTFontCreateWithName(CFSTR("Helvetica"),size,NULL);
  CGColorSpaceRef space=CGColorSpaceCreateDeviceRGB();
  const CGFloat rgba[]={.86,.91,.97,1};
  CGColorRef color=CGColorCreate(space,rgba);
  NSAttributedString *label=[[NSAttributedString alloc] initWithString:text
    attributes:@{(id)kCTFontAttributeName:(__bridge id)font,
                 (id)kCTForegroundColorAttributeName:(__bridge id)color}];
  CTLineRef line=CTLineCreateWithAttributedString((__bridge CFAttributedStringRef)label);
  CGContextSetTextMatrix(context,CGAffineTransformIdentity);
  CGContextSetTextPosition(context,x,y);CTLineDraw(line,context);
  CFRelease(line);CGColorRelease(color);CGColorSpaceRelease(space);CFRelease(font);
}

int main(int argc,const char *argv[]) {
  @autoreleasepool {
    if(argc!=4) return 2;
    NSData *data=[NSData dataWithContentsOfFile:@(argv[1])];
    NSDictionary *input=[NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    NSDictionary *parameters=input[@"parameters"];
    NSArray *points=input[@"vertices"],*faces=input[@"faces"];
    if(!points || !faces || !parameters) return 3;
    size_t width=[parameters[@"width"] unsignedIntegerValue],
           height=[parameters[@"height"] unsignedIntegerValue],
           frames=[parameters[@"frame_count"] unsignedIntegerValue];
    double pitch=[parameters[@"pitch_degrees"] doubleValue]*M_PI/180,
           initialYaw=[parameters[@"start_yaw_degrees"] doubleValue]*M_PI/180;
    size_t byteCount=width*height*4;
    unsigned char *pixels=malloc(byteCount),*background=malloc(byteCount);
    if(!pixels || !background) {free(pixels);free(background);return 4;}
    CGColorSpaceRef space=CGColorSpaceCreateDeviceRGB();
    CGContextRef context=CGBitmapContextCreate(pixels,width,height,8,width*4,space,
      kCGBitmapByteOrder32Big|kCGImageAlphaPremultipliedLast);
    CGColorSpaceRelease(space);
    if(!context) {free(pixels);free(background);return 4;}
    NSFileHandle *output=[NSFileHandle fileHandleForWritingAtPath:@(argv[2])];
    if(!output) {CGContextRelease(context);free(pixels);free(background);return 4;}
    NSDictionary *object=@{@"vertex_start":@0,@"vertex_count":@(points.count),
      @"triangles":faces,@"color":@[@.52,@.72,@.88],@"smooth_edges":@YES};
    // q chooses the shared triangle depth buffer; there are no rigid objects,
    // colliders, particles, links, timestamps, integrator or solver results.
    NSDictionary *frame=@{@"m":points,@"q":@[],@"r":@[],@"p":@[],@"g":@[]};
    NSDictionary *empty=@{@"m":@[],@"q":@[],@"r":@[],@"p":@[],@"g":@[]};
    NSMutableArray *visible=[NSMutableArray arrayWithCapacity:frames];
    for(size_t index=0;index<frames;index++) {
      @autoreleasepool {
        double yaw=initialYaw+2*M_PI*(double)index/(double)frames;
        PhyVideoCamera camera={cos(yaw),sin(yaw),cos(pitch),sin(pitch),
          0,0,.39*fmin((double)width,(double)height),.5*(double)width,
          .46*(double)height,-1.05,1.05};
        BOOL backgroundOk=PhyVideoDrawFrame(context,empty,empty,@[],@[],@[],@[],
          @[@-1,@-1,@-1],@[@1,@1,@1],camera,0,width,height);
        if(!backgroundOk) {CGContextRelease(context);free(pixels);free(background);return 5;}
        memcpy(background,pixels,byteCount);
        BOOL ok=PhyVideoDrawFrame(context,frame,frame,@[],@[],@[],@[object],
          @[@-1,@-1,@-1],@[@1,@1,@1],camera,0,width,height);
        if(!ok) {CGContextRelease(context);free(pixels);free(background);return 5;}
        NSUInteger surfacePixels=0;
        for(size_t pixel=0;pixel<width*height;pixel++) {
          size_t offset=pixel*4;
          if(abs((int)pixels[offset]-(int)background[offset])>3 ||
             abs((int)pixels[offset+1]-(int)background[offset+1])>3 ||
             abs((int)pixels[offset+2]-(int)background[offset+2])>3) surfacePixels++;
        }
        [visible addObject:@(surfacePixels)];
        Label(context,@"AI MODEL PREVIEW  |  SHAPE ONLY",20,(double)height-35,14);
        Label(context,@"Camera rotation; no simulation or measurements",20,23,12);
        [output writeData:[NSData dataWithBytes:pixels length:byteCount]];
      }
    }
    NSData *audit=[NSJSONSerialization dataWithJSONObject:@{@"surface_pixels_per_frame":visible}
      options:0 error:nil];
    // Foundation's atomic writer may choose the global per-user temp folder.
    // This unsealed audit stays inside the dedicated job temp directory.
    FILE *auditOutput=fopen(argv[3],"wb");
    BOOL saved=auditOutput && fwrite(audit.bytes,1,audit.length,auditOutput)==audit.length;
    if(auditOutput && fclose(auditOutput)!=0) saved=NO;
    [output closeFile];CGContextRelease(context);free(pixels);free(background);
    if(!saved) return 6;
  }
  return 0;
}
'''


def _parameters(value: dict | None) -> dict:
    if value is not None and (not isinstance(value, dict)
                             or set(value) - set(DEFAULT_PARAMETERS)):
        raise PresentationError("unknown model-preview parameters")
    result = {**DEFAULT_PARAMETERS, **(value or {})}
    for key in ("width", "height"):
        if type(result[key]) is not int or not 256 <= result[key] <= 960 or result[key] % 2:
            raise PresentationError("preview dimensions must be even integers from 256 to 960")
    if type(result["fps"]) is not int or not 6 <= result["fps"] <= 30:
        raise PresentationError("preview fps must be an integer from 6 to 30")
    for key, lower, upper in (("duration_seconds", 1, 12), ("pitch_degrees", -30, 45),
                              ("start_yaw_degrees", -360, 360)):
        part = result[key]
        if type(part) not in (int, float) or not math.isfinite(part) or not lower <= part <= upper:
            raise PresentationError("invalid preview " + key)
    frames = round(result["duration_seconds"] * result["fps"])
    if not 6 <= frames <= 360 or frames * result["width"] * result["height"] * 4 > MAX_RAW_BYTES:
        raise PresentationError("model-preview frame buffer exceeds its independent budget")
    result["frame_count"] = frames
    result["duration_seconds"] = frames / result["fps"]
    return result


def _mesh(document: dict) -> tuple[list, list, dict]:
    vertices, faces = document.get("vertices"), document.get("faces")
    if (not isinstance(vertices, list) or not 3 <= len(vertices) <= MAX_VERTICES
            or not isinstance(faces, list) or not 1 <= len(faces) <= MAX_TRIANGLES):
        raise PresentationError("model-preview mesh exceeds its vertex/triangle budget")
    minimum, maximum = [math.inf] * 3, [-math.inf] * 3
    for point in vertices:
        if (not isinstance(point, list) or len(point) != 3
                or any(type(value) not in (int, float) or not math.isfinite(value)
                       or abs(value) > 1e12 for value in point)):
            raise PresentationError("preview vertices must have three bounded finite coordinates")
        for axis, value in enumerate(point):
            minimum[axis], maximum[axis] = min(minimum[axis], value), max(maximum[axis], value)
    for face in faces:
        if (not isinstance(face, list) or len(face) != 3
                or any(type(index) is not int or not 0 <= index < len(vertices) for index in face)
                or len(set(face)) != 3):
            raise PresentationError("preview faces must contain three distinct in-range indices")
    center = [(lower + upper) * .5 for lower, upper in zip(minimum, maximum)]
    radius = math.sqrt(max(sum((point[axis] - center[axis]) ** 2 for axis in range(3))
                           for point in vertices))
    if not math.isfinite(radius) or radius <= 1e-15:
        raise PresentationError("preview mesh has no visible spatial extent")
    normalized = [[(point[axis] - center[axis]) / radius for axis in range(3)] for point in vertices]
    return normalized, faces, {"method": "bounding-box center and uniform enclosing-sphere fit",
                               "center_in_source_coordinates": center,
                               "view_scale_per_source_unit": 1 / radius,
                               "source_bounds": {"min": minimum, "max": maximum},
                               "changes_source_geometry": False,
                               "source_units": document.get("units", "model_units"),
                               "physical_scale_inferred": False}


def _core_source(engine_root: str | Path) -> Path:
    root = Path(engine_root)
    if root.is_symlink() or not root.is_dir():
        raise PresentationError("pinned engine root is unavailable", "renderer_unavailable")
    root = root.resolve(strict=True)
    candidates = [root / "physics_release" / "physics_demo" / "io" / "video_render_core.m"]
    for candidate in candidates:
        if (candidate.is_file() and not candidate.is_symlink()
                and candidate.with_suffix(".h").is_file() and not candidate.with_suffix(".h").is_symlink()
                and candidate.resolve().is_relative_to(root)
                and candidate.with_suffix(".h").resolve().is_relative_to(root)):
            return candidate
    raise PresentationError("pinned native surface drawing source is unavailable", "renderer_unavailable")


def _tool(name: str) -> str:
    candidate = shutil.which(name)
    if not candidate and Path("/opt/homebrew/bin", name).is_file():
        candidate = str(Path("/opt/homebrew/bin", name))
    if not candidate:
        raise PresentationError(name + " is required for local model presentation", "renderer_unavailable")
    return candidate


def _run(command: list[str], started: float, budget: float | None,
         *, stdout=None) -> subprocess.CompletedProcess:
    remaining = None if budget is None else budget - (time.monotonic() - started)
    if remaining is not None and remaining <= 0:
        raise PresentationError("model presentation exceeded its independent deadline", "render_timeout")
    try:
        # An empty pipe supplies EOF without opening /dev/null as O_RDWR; a
        # rendering-only sandbox does not need a device-write allowance.
        process = subprocess.run(command, input=b"",
                                 stdout=subprocess.PIPE if stdout is None else stdout,
                                 stderr=subprocess.PIPE, timeout=remaining, check=False)
    except subprocess.TimeoutExpired as exc:
        raise PresentationError("model presentation exceeded its independent deadline", "render_timeout") from exc
    except OSError as exc:
        raise PresentationError("local model presentation process could not run", "render_failed") from exc
    if process.returncode:
        raise PresentationError(f"local model presentation failed (exit {process.returncode}): " +
                                process.stderr[-1200:].decode("utf-8", "replace"), "render_failed")
    return process


def _decode_video(path: Path, expected: dict, started: float, budget: float | None) -> dict:
    probe = _run([_tool("ffprobe"), "-v", "error", "-count_frames", "-select_streams", "v:0",
                  "-show_entries", "stream=codec_name,width,height,nb_read_frames,duration",
                  "-of", "json", str(path)], started, budget)
    try:
        streams = json.loads(probe.stdout)["streams"]
        stream = streams[0]
        frame_count, duration = int(stream["nb_read_frames"]), float(stream["duration"])
        if (len(streams) != 1 or stream["codec_name"] != "h264"
                or stream["width"] != expected["width"] or stream["height"] != expected["height"]
                or frame_count != expected["frame_count"]
                or abs(duration - expected["duration_seconds"]) > 1 / expected["fps"] + .002):
            raise ValueError("inconsistent frame metadata")
    except (KeyError, IndexError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise PresentationError("model-preview video failed its frame and duration checks",
                                "render_verification_failed") from exc
    # -xerror makes corrupt frames fatal; decoding covers the complete stream.
    _run([_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-nostdin", "-xerror",
          "-i", str(path), "-map", "0:v:0", "-f", "null", "-"], started, budget)
    return {"codec": "h264", "all_frames_decoded": True, "decode_verified": True,
            "frame_count": frame_count, "duration_s": duration,
            "width": stream["width"], "height": stream["height"]}


def render_model_preview(mesh_path: str | Path, output_dir: str | Path,
                         parameters: dict | None = None, *, engine_root: str | Path,
                         expected_mesh_sha256: str | None = None,
                         budget_seconds: float | None = None,
                         max_output_bytes: int = 16 * 1024 * 1024) -> dict:
    """Publish verified shape-only MP4, PNG and a source-bound render receipt.

    Parameters affect the camera, frame dimensions and playback only. Geometry
    is neither decimated nor cleaned. Disconnected and non-solid surfaces are
    valid display inputs; this function never certifies simulation eligibility.
    """
    if (budget_seconds is not None and (type(budget_seconds) not in (int, float)
            or not math.isfinite(budget_seconds) or budget_seconds <= 0)):
        raise PresentationError("invalid model-preview time budget")
    if type(max_output_bytes) is not int or not 1024 <= max_output_bytes <= 128 * 1024 * 1024:
        raise PresentationError("invalid model-preview output byte budget")
    started = time.monotonic()
    settings = _parameters(parameters)
    source, document, source_sha = read_document(mesh_path)
    if expected_mesh_sha256 is not None and (not isinstance(expected_mesh_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", expected_mesh_sha256) is None
            or expected_mesh_sha256 != source_sha):
        raise PresentationError("model-preview source hash does not match its receipt", "source_changed")
    vertices, faces, normalization = _mesh(document)
    output = Path(output_dir)
    for candidate in (output, *output.parents):
        if candidate.is_symlink():
            raise PresentationError("model-preview output cannot use a symlink")
    output.mkdir(parents=True, exist_ok=True)
    output = output.resolve(strict=True)
    if output == source.parent or not output.is_dir():
        raise PresentationError("model-preview output must be separate from source geometry")
    names = ("model-preview.mp4", "model-preview.png", "render-manifest.json")
    if any((output / name).exists() or (output / name).is_symlink() for name in names):
        raise PresentationError("model-preview artifacts already exist")
    core, clang, ffmpeg = _core_source(engine_root), _tool("clang"), _tool("ffmpeg")
    core_sha, header_sha = digest(core.read_bytes()), digest(core.with_suffix(".h").read_bytes())
    compiler_version = _run([clang, "--version"], started, budget_seconds).stdout.decode("utf-8", "replace")
    with tempfile.TemporaryDirectory(prefix=".model-preview-", dir=output) as temporary:
        folder = Path(temporary)
        driver, executable, staged = folder / "preview.m", folder / "preview", folder / "mesh.json"
        driver.write_text(_NATIVE_DRIVER, encoding="utf-8")
        staged.write_bytes(canonical({"vertices": vertices, "faces": faces, "parameters": settings}))
        _run([clang, "-fobjc-arc", "-fblocks", "-O2", "-I", str(core.parent),
              "-fmodules-cache-path=" + str(folder / "clang-cache"),
              "-framework", "Foundation", "-framework", "CoreGraphics", "-framework", "CoreText",
              str(driver), str(core), "-o", str(executable)], started, budget_seconds)
        raw = folder / "frames.rgba"
        raw.touch()
        pixel_audit = folder / "pixel-audit.json"
        _run([str(executable), str(staged), str(raw), str(pixel_audit)], started, budget_seconds)
        expected_raw_bytes = settings["width"] * settings["height"] * 4 * settings["frame_count"]
        if raw.stat().st_size != expected_raw_bytes:
            raise PresentationError("native preview produced an incomplete frame buffer", "render_verification_failed")
        try:
            counts = json.loads(pixel_audit.read_bytes())["surface_pixels_per_frame"]
            if (not isinstance(counts, list) or len(counts) != settings["frame_count"]
                    or any(type(count) is not int or not 0 <= count <= settings["width"] * settings["height"]
                           for count in counts)
                    or sum(count >= 16 for count in counts) < max(1, settings["frame_count"] // 2)):
                raise ValueError("geometry was not visible in the turntable")
        except (KeyError, ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
            raise PresentationError("native preview failed actual surface-pixel checks",
                                    "render_verification_failed") from exc
        frame_audit = {"method": "surface pixels versus the same camera/background before labels",
                       "surface_pixels_per_frame": counts,
                       "frames_with_visible_surface": sum(count >= 16 for count in counts),
                       "minimum_surface_pixels": min(counts), "maximum_surface_pixels": max(counts),
                       "original_triangle_set_retained": True}
        candidate, poster = folder / names[0], folder / names[1]
        for crf in (20, 27, 34):
            _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                  "-f", "rawvideo", "-pix_fmt", "rgba", "-video_size",
                  f'{settings["width"]}x{settings["height"]}', "-framerate", str(settings["fps"]),
                  "-i", str(raw), "-an", "-c:v", "libx264", "-preset", "medium",
                  "-crf", str(crf), "-pix_fmt", "yuv420p", "-threads", "1",
                  "-movflags", "+faststart", "-map_metadata", "-1", str(candidate)], started, budget_seconds)
            if candidate.stat().st_size <= max_output_bytes:
                break
        else:
            raise PresentationError("model-preview video exceeds its output byte budget", "render_too_large")
        media = _decode_video(candidate, settings, started, budget_seconds)
        _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(candidate),
              "-frames:v", "1", "-update", "1", str(poster)], started, budget_seconds)
        if not poster.is_file() or poster.stat().st_size > 8 * 1024 * 1024:
            raise PresentationError("model-preview poster is missing or exceeds its budget", "render_verification_failed")
        if read_document(source)[2] != source_sha:
            raise PresentationError("model-preview source changed during rendering", "source_changed")
        if digest(core.read_bytes()) != core_sha or digest(core.with_suffix(".h").read_bytes()) != header_sha:
            raise PresentationError("native drawing source changed during rendering", "source_changed")
        assets = [{"path": str(output / path.name), "media_type": mime, "role": role,
                   "sha256": digest(path.read_bytes()), "bytes": path.stat().st_size}
                  for path, mime, role in ((candidate, "video/mp4", "model_preview_video"),
                                           (poster, "image/png", "model_preview_poster"))]
        manifest = {"schema": "model-preview-render/1", "result_kind": "model-preview",
                    "source_mesh_path": str(source), "source_mesh_sha256": source_sha,
                    "source_schema": document.get("schema_version"),
                    "original_vertices": len(vertices), "original_triangles": len(faces),
                    "source_triangle_indices_sha256": digest(canonical(faces)),
                    "rendered_triangle_indices_sha256": digest(canonical(faces)),
                    "full_geometry_retained": True, "disconnected_components_allowed": True,
                    "geometry_modified": False, "normalization": normalization,
                    "provider": "local-native-triangle-surface", "generative_rendering": False,
                    "surface_style": "neutral untextured shading; colors are presentation only",
                    "physics_simulated": False, "simulation_performed": False,
                    "measurement_source": False, "numerical_usable": False,
                    "physical_accuracy": "unverified single-image approximation",
                    "presentation": {"kind": "360-degree camera turntable", "parameters": settings,
                                     "physical_time_axis": False, "solver_rerun": False},
                    "warnings": ["Unseen geometry is inferred by the upstream image model.",
                                 "This video presents shape, not motion predicted by a physics solver.",
                                 "Uniform viewing normalization does not establish real-world dimensions."],
                    "native_drawer_sha256": core_sha, "native_header_sha256": header_sha,
                    "driver_sha256": digest(_NATIVE_DRIVER.encode()),
                    "compiler": {"path": clang, "version": compiler_version.strip(),
                                 "version_sha256": digest(compiler_version.encode())},
                    "video": assets[0], "poster": assets[1], "artifacts": assets,
                    "media": {**media, "crf": crf}, "frame_audit": frame_audit}
        os.replace(candidate, output / names[0])
        os.replace(poster, output / names[1])
        atomic_write(output / names[2], canonical(manifest))
    return {**manifest, "manifest_path": str(output / names[2])}


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a saved mesh without running a simulator")
    parser.add_argument("--mesh", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--engine-root", required=True)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--budget-seconds", type=float)
    parser.add_argument("--parameters-json", default="{}")
    args = parser.parse_args()
    try:
        result = render_model_preview(args.mesh, args.output, json.loads(args.parameters_json),
                                      engine_root=args.engine_root,
                                      expected_mesh_sha256=args.expected_sha256,
                                      budget_seconds=args.budget_seconds)
        print(canonical({"ok": True, **result}).decode(), end="")
        return 0
    except (PresentationError, json.JSONDecodeError) as exc:
        print(canonical({"ok": False, "code": getattr(exc, "code", "invalid_presentation_input"),
                         "message": str(exc)}).decode(), end="")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
