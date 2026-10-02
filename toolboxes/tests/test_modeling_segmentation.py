"""Offline foreground pin, CPU-provider and image/mask contract tests."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


TOOLBOXES = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLBOXES))
from modeling_flow.contracts import FlowError
from modeling_flow import segmentation

try:
    import numpy as np
    from PIL import Image
except ImportError:
    np = Image = None


class ForegroundFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='foreground-pin-test-')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.path = self.directory / 'u2net.onnx'
        self.content = b'synthetic graph; never loaded by ORT'
        self.path.write_bytes(self.content)
        self.checksum = hashlib.sha256(self.content).hexdigest()


class ForegroundModelPinTests(ForegroundFixture, unittest.TestCase):
    def test_verified_graph_bytes_and_identity_match_pin(self):
        content, metadata = segmentation._read_model(self.path, self.checksum)
        self.assertEqual(content, self.content)
        self.assertEqual(metadata['sha256'], self.checksum)
        self.assertEqual(metadata['bytes'], len(self.content))
        self.assertEqual(metadata['model'], 'u2net')
        with patch.object(segmentation, '_load_dependencies') as load:
            self.assertEqual(segmentation.verify_foreground_model(self.path, self.checksum), metadata)
        load.assert_not_called()

    def test_mutated_graph_is_rejected(self):
        self.path.write_bytes(b'mutated graph')
        with self.assertRaises(FlowError) as raised:
            segmentation.verify_foreground_model(self.path, self.checksum)
        self.assertEqual(raised.exception.code, 'foreground_pin_mismatch')

    def test_invalid_digest_is_not_a_download_request(self):
        for checksum in ('', 'a' * 63, 'A' * 64, 'https://example.invalid/model'):
            with self.subTest(checksum=checksum), self.assertRaises(FlowError):
                segmentation.verify_foreground_model(self.path, checksum)

    def test_symlink_graph_is_rejected(self):
        linked = self.directory / 'linked.onnx'
        linked.symlink_to(self.path)
        with self.assertRaises(FlowError):
            segmentation.verify_foreground_model(linked, self.checksum)

    def test_nonregular_graph_cannot_block_the_host(self):
        fifo = self.directory / 'fifo.onnx'
        os.mkfifo(fifo)
        with self.assertRaises(FlowError) as raised:
            segmentation.verify_foreground_model(fifo, self.checksum)
        self.assertEqual(raised.exception.code, 'foreground_model_invalid')

    def test_oversized_graph_is_rejected_before_reading(self):
        with self.path.open('wb') as handle:
            handle.truncate(segmentation.MAX_MODEL_BYTES + 1)
        with self.assertRaises(FlowError) as raised:
            segmentation.verify_foreground_model(self.path, self.checksum)
        self.assertEqual(raised.exception.code, 'foreground_model_invalid')


@unittest.skipIf(np is None or Image is None, 'numpy/Pillow belong to the optional image runtime')
class ForegroundImageContractTests(ForegroundFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.image = Image.new('RGB', (16, 8), (50, 100, 200))
        saliency = np.tile(np.linspace(0.2, 0.8, 320, dtype=np.float32), (320, 1))[None, None]
        self.session = Mock()
        self.session.get_providers.return_value = ['CPUExecutionProvider']
        self.session.get_inputs.return_value = [SimpleNamespace(
            name='input.1', type='tensor(float)', shape=[1, 3, 320, 320])]
        self.session.get_outputs.return_value = [SimpleNamespace(
            name='d0', type='tensor(float)', shape=[1, 1, 320, 320])]
        self.session.run.return_value = [saliency]
        self.ort = SimpleNamespace(SessionOptions=lambda: SimpleNamespace(),
            ExecutionMode=SimpleNamespace(ORT_SEQUENTIAL='sequential'),
            InferenceSession=Mock(return_value=self.session))
        self.dependencies = patch.object(segmentation, '_load_dependencies',
                                          return_value=(np, Image, self.ort))
        self.dependencies.start()
        self.addCleanup(self.dependencies.stop)

    def test_opaque_image_uses_cpu_pinned_bytes_and_official_normalization(self):
        result, metadata = segmentation.remove_foreground(self.image, self.path, self.checksum)
        args, kwargs = self.ort.InferenceSession.call_args
        self.assertEqual(args[0], self.content)
        self.assertEqual(kwargs['providers'], ['CPUExecutionProvider'])
        names, feeds = self.session.run.call_args.args
        self.assertEqual(names, ['d0'])
        tensor = feeds['input.1']
        self.assertEqual(tensor.shape, (1, 3, 320, 320))
        self.assertEqual(tensor.dtype, np.float32)
        expected = (np.array([0.25, 0.5, 1], dtype=np.float32)
                    - [0.485, 0.456, 0.406]) / [0.229, 0.224, 0.225]
        np.testing.assert_allclose(tensor[0, :, 0, 0], expected, rtol=1e-6)
        self.assertEqual(result.mode, 'RGBA')
        self.assertEqual(result.size, self.image.size)
        np.testing.assert_array_equal(np.asarray(result)[..., :3], np.asarray(self.image))
        self.assertLess(result.getchannel('A').getextrema()[0], 255)
        self.assertEqual(metadata['background_removal'], 'u2net_cpu')
        self.assertFalse(metadata['foreground_mask_verified'])
        self.assertEqual(metadata['foreground_model']['sha256'], self.checksum)

    def test_existing_alpha_is_preserved_without_model_or_runtime(self):
        source = self.image.convert('RGBA')
        source.putalpha(128)
        missing = self.directory / 'not-installed.onnx'
        result, metadata = segmentation.remove_foreground(source, missing, 'invalid-unused-pin')
        np.testing.assert_array_equal(np.asarray(result), np.asarray(source))
        self.assertEqual(metadata['background_removal'], 'skipped_provided_alpha')
        self.ort.InferenceSession.assert_not_called()

    def test_fully_transparent_image_is_not_foreground(self):
        source = self.image.convert('RGBA')
        source.putalpha(0)
        with self.assertRaises(FlowError) as raised:
            segmentation.remove_foreground(source, self.path, self.checksum)
        self.assertEqual(raised.exception.code, 'foreground_mask_invalid')
        self.ort.InferenceSession.assert_not_called()

    def test_black_input_tensor_is_finite(self):
        black = Image.new('RGB', (8, 8))
        segmentation.remove_foreground(black, self.path, self.checksum)
        tensor = self.session.run.call_args.args[1]['input.1']
        self.assertTrue(np.isfinite(tensor).all())

    def test_unexpected_provider_is_rejected(self):
        self.session.get_providers.return_value = ['CoreMLExecutionProvider', 'CPUExecutionProvider']
        with self.assertRaises(FlowError) as raised:
            segmentation.remove_foreground(self.image, self.path, self.checksum)
        self.assertEqual(raised.exception.code, 'foreground_provider_mismatch')
        self.session.run.assert_not_called()

    def test_unexpected_graph_dimensions_are_rejected(self):
        self.session.get_inputs.return_value[0].shape = [1, 3, 1024, 1024]
        with self.assertRaises(FlowError) as raised:
            segmentation.remove_foreground(self.image, self.path, self.checksum)
        self.assertEqual(raised.exception.code, 'foreground_model_invalid')
        self.session.run.assert_not_called()

    def test_nonfinite_constant_and_out_of_range_masks_fail_without_fallback(self):
        for values in (np.full((1, 1, 320, 320), np.nan, dtype=np.float32),
                       np.full((1, 1, 320, 320), 0.5, dtype=np.float32),
                       np.tile(np.linspace(-1, 1, 320, dtype=np.float32), (320, 1))[None, None],
                       np.zeros((1, 1, 64, 64), dtype=np.float32)):
            with self.subTest(shape=values.shape, minimum=float(values.min())):
                self.session.run.return_value = [values]
                with self.assertRaises(FlowError) as raised:
                    segmentation.remove_foreground(self.image, self.path, self.checksum)
                self.assertEqual(raised.exception.code, 'foreground_mask_invalid')

    def test_onnx_failure_is_reported_without_returning_the_full_image(self):
        self.ort.InferenceSession.side_effect = RuntimeError('synthetic incompatible graph')
        with self.assertRaises(FlowError) as raised:
            segmentation.remove_foreground(self.image, self.path, self.checksum)
        self.assertEqual(raised.exception.code, 'foreground_inference_failed')


if __name__ == '__main__':
    unittest.main()
