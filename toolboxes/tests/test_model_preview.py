"""Shape delivery retains the actual mesh and never invokes a solver."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from pipeline_presentation.artifacts import PresentationError, canonical, digest
from pipeline_presentation import model_preview as preview


ENGINE_ROOT = Path(__file__).resolve().parents[1] / "physics"
NATIVE_AVAILABLE = (sys.platform == "darwin" and shutil.which("clang")
                    and (shutil.which("ffmpeg") or Path("/opt/homebrew/bin/ffmpeg").is_file()))


def disconnected_mesh() -> dict:
    # Two asymmetric tetrahedra, separated visibly; a collinear triangle is
    # also retained. The original geometry has nine triangles, not a proxy.
    vertices = [[-.8, -.5, -.5], [.3, -.5, -.5], [-.5, .8, -.3], [-.7, -.4, .9],
                [1.1, .1, -.3], [1.7, .1, -.3], [1.2, .7, -.2], [1.15, .1, .4],
                [-.7, -.5, -.5], [-.6, -.5, -.5]]
    tetra = [[0, 2, 1], [0, 1, 3], [1, 2, 3], [2, 0, 3]]
    return {"schema_version": "modeling-flow-display/1", "units": "model_unit",
            "vertices": vertices, "faces": tetra + [[index + 4 for index in face] for face in tetra]
            + [[0, 8, 9]], "physical_accuracy": "unverified"}


class ModelPreviewFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "source" / "mesh.json"
        self.source.parent.mkdir()
        self.payload = canonical(disconnected_mesh())
        self.source.write_bytes(self.payload)

    def render(self, **kwargs):
        return preview.render_model_preview(self.source, self.root / "presentation",
                                            engine_root=ENGINE_ROOT, **kwargs)


class ModelPreviewValidationTests(ModelPreviewFixture, unittest.TestCase):
    def test_normalization_retains_every_face_and_disconnected_part(self):
        document = disconnected_mesh()
        before = copy.deepcopy(document)
        vertices, faces, receipt = preview._mesh(document)
        self.assertEqual(document, before)
        self.assertEqual(faces, before["faces"])
        self.assertEqual(len(vertices), 10)
        self.assertEqual(len(faces), 9)
        self.assertFalse(receipt["changes_source_geometry"])
        self.assertFalse(receipt["physical_scale_inferred"])
        self.assertEqual(receipt["source_units"], "model_unit")
        # Every point receives the same affine viewing transform.
        center = receipt["center_in_source_coordinates"]
        scale = receipt["view_scale_per_source_unit"]
        for original, displayed in zip(before["vertices"], vertices):
            for axis in range(3):
                self.assertAlmostEqual(displayed[axis], (original[axis] - center[axis]) * scale)

    def test_invalid_mesh_rejected_before_any_native_process(self):
        invalid = []
        for replacement in ([True, 1, 2], [0, 1, 99], [0, 0, 1], [0, {"index": 1}, 2]):
            mesh = disconnected_mesh()
            mesh["faces"][0] = replacement
            invalid.append(mesh)
        for replacement in ([float("nan"), 0, 0], [float("inf"), 0, 0], [True, 0, 0], [1e13, 0, 0]):
            mesh = disconnected_mesh()
            mesh["vertices"][0] = replacement
            invalid.append(mesh)
        for document in invalid:
            with self.subTest(document=document), self.assertRaises(PresentationError):
                preview._mesh(document)
        with mock.patch.object(preview, "MAX_VERTICES", 4), self.assertRaises(PresentationError):
            preview._mesh(disconnected_mesh())

    def test_bad_parameters_do_not_run_subprocesses(self):
        invalid = ({"fps": True}, {"width": 255}, {"width": 641}, {"unknown": 1},
                   {"duration_seconds": float("nan")}, {"pitch_degrees": 90},
                   {"height": 960, "width": 960, "duration_seconds": 12, "fps": 30})
        with mock.patch.object(preview, "_run") as process:
            for parameters in invalid:
                with self.subTest(parameters=parameters), self.assertRaises(PresentationError):
                    self.render(parameters=parameters)
            process.assert_not_called()

    def test_expected_hash_blocks_tampered_source_before_render(self):
        with mock.patch.object(preview, "_run") as process:
            with self.assertRaises(PresentationError) as caught:
                self.render(expected_mesh_sha256="0" * 64)
            self.assertEqual(caught.exception.code, "source_changed")
            process.assert_not_called()

    def test_source_and_output_symlinks_are_rejected(self):
        link = self.root / "mesh-link.json"
        link.symlink_to(self.source)
        with self.assertRaises(PresentationError):
            preview.render_model_preview(link, self.root / "presentation", engine_root=ENGINE_ROOT)
        output = self.root / "output-link"
        output.symlink_to(self.source.parent, target_is_directory=True)
        with self.assertRaises(PresentationError):
            preview.render_model_preview(self.source, output, engine_root=ENGINE_ROOT)

    def test_engine_must_be_supplied_and_no_import_fallback(self):
        with self.assertRaises(PresentationError) as caught:
            preview._core_source(self.root / "missing-engine")
        self.assertEqual(caught.exception.code, "renderer_unavailable")
        link = self.root / "engine-link"
        link.symlink_to(ENGINE_ROOT, target_is_directory=True)
        with self.assertRaises(PresentationError):
            preview._core_source(link)

    def test_existing_output_is_never_overwritten(self):
        output = self.root / "presentation"
        output.mkdir()
        sentinel = output / "model-preview.mp4"
        sentinel.write_bytes(b"keep-existing-asset")
        with mock.patch.object(preview, "_run") as process, self.assertRaises(PresentationError):
            self.render()
        process.assert_not_called()
        self.assertEqual(sentinel.read_bytes(), b"keep-existing-asset")
        self.assertEqual(self.source.read_bytes(), self.payload)


@unittest.skipUnless(NATIVE_AVAILABLE, "native surface renderer requires macOS clang and FFmpeg")
class NativeModelPreviewTests(ModelPreviewFixture, unittest.TestCase):
    def test_actual_multicomponent_video_decodes_all_frames(self):
        report = self.render(parameters={"width": 256, "height": 256, "fps": 6, "duration_seconds": 1},
                             expected_mesh_sha256=digest(self.payload), budget_seconds=60)
        self.assertEqual(self.source.read_bytes(), self.payload)
        self.assertEqual(report["source_mesh_sha256"], digest(self.payload))
        self.assertEqual(report["original_vertices"], 10)
        self.assertEqual(report["original_triangles"], 9)
        self.assertTrue(report["full_geometry_retained"])
        self.assertFalse(report["simulation_performed"])
        self.assertFalse(report["numerical_usable"])
        self.assertFalse(report["measurement_source"])
        self.assertEqual(report["media"]["frame_count"], 6)
        self.assertTrue(report["media"]["all_frames_decoded"])
        self.assertEqual(report["frame_audit"]["frames_with_visible_surface"], 6)
        self.assertEqual(len(report["frame_audit"]["surface_pixels_per_frame"]), 6)
        self.assertGreater(report["frame_audit"]["minimum_surface_pixels"], 16)
        for asset in report["artifacts"]:
            payload = Path(asset["path"]).read_bytes()
            self.assertEqual(asset["sha256"], digest(payload))
            self.assertEqual(asset["bytes"], len(payload))
        manifest = json.loads(Path(report["manifest_path"]).read_bytes())
        self.assertEqual(manifest["source_mesh_sha256"], digest(self.payload))
        self.assertEqual(Path(report["poster"]["path"]).read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_source_change_during_render_cannot_publish_assets(self):
        decode = preview._decode_video
        def tamper(*args, **kwargs):
            result = decode(*args, **kwargs)
            self.source.write_bytes(self.payload + b"\n")
            return result
        with mock.patch.object(preview, "_decode_video", side_effect=tamper):
            with self.assertRaises(PresentationError) as caught:
                self.render(parameters={"width": 256, "height": 256, "fps": 6, "duration_seconds": 1},
                            budget_seconds=60)
        self.assertEqual(caught.exception.code, "source_changed")
        self.assertEqual(list((self.root / "presentation").iterdir()), [])

    def test_bounded_video_rejects_corrupt_decode_metadata(self):
        with mock.patch.object(preview, "_run", return_value=subprocess.CompletedProcess(
                args=[], returncode=0, stdout=b'{"streams":[{"codec_name":"h264","width":256,"height":256,"nb_read_frames":"5","duration":"1"}]}', stderr=b"")):
            with self.assertRaises(PresentationError) as caught:
                preview._decode_video(self.source, {"width": 256, "height": 256, "frame_count": 6,
                                                   "duration_seconds": 1, "fps": 6}, 0, None)
        self.assertEqual(caught.exception.code, "render_verification_failed")


if __name__ == "__main__":
    unittest.main()
