from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import live_photo_native as adapter


def export_without_marker(native, source, destination, identifier):
    foundation = native.AVFoundation
    asset = foundation.AVURLAsset.URLAssetWithURL_options_(adapter._url(native, source), None)
    exporter = foundation.AVAssetExportSession.alloc().initWithAsset_presetName_(asset, foundation.AVAssetExportPresetPassthrough)
    exporter.setOutputURL_(adapter._url(native, destination))
    exporter.setOutputFileType_(foundation.AVFileTypeQuickTimeMovie)
    content = foundation.AVMutableMetadataItem.metadataItem()
    content.setKey_(adapter.IDENTIFIER)
    content.setKeySpace_("mdta")
    content.setValue_(identifier)
    content.setDataType_("com.apple.metadata.datatype.UTF-8")
    exporter.setMetadata_([content])
    completed = threading.Event()
    exporter.exportAsynchronouslyWithCompletionHandler_(completed.set)
    deadline = time.monotonic() + 20
    while not completed.is_set() and time.monotonic() < deadline:
        adapter._pump(native)
    if not completed.is_set() or exporter.status() != foundation.AVAssetExportSessionStatusCompleted:
        exporter.cancelExport()
        raise RuntimeError(f"No-marker fixture export failed: {exporter.error()}")


class PortableImportTests(unittest.TestCase):
    def test_import_does_not_load_native_frameworks(self):
        specification = importlib.util.spec_from_file_location("isolated_live_photo_native", SCRIPTS / "live_photo_native.py")
        module = importlib.util.module_from_spec(specification)
        with mock.patch.object(sys, "platform", "linux"), mock.patch.object(importlib, "import_module") as loader:
            specification.loader.exec_module(module)
            loader.assert_not_called()
            with self.assertRaisesRegex(RuntimeError, "require macOS"):
                module._native()
            loader.assert_not_called()

    def test_missing_pyobjc_is_only_reported_on_call(self):
        with mock.patch.object(sys, "platform", "darwin"), mock.patch.object(importlib, "import_module", side_effect=ImportError("missing")):
            with self.assertRaisesRegex(RuntimeError, "Optional macOS setup") as caught:
                adapter._native()
        for framework in ("AVFoundation", "CoreMedia", "Quartz", "Photos"):
            self.assertIn("pyobjc-framework-" + framework, str(caught.exception))

    def test_missing_ffmpeg_suggests_but_does_not_install(self):
        with mock.patch.object(shutil, "which", return_value=None), mock.patch.object(adapter.subprocess, "run") as runner:
            with self.assertRaisesRegex(RuntimeError, "optionally install ffmpeg"):
                adapter._tool("ffmpeg")
            runner.assert_not_called()


class TemporaryFiles(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / "source.mp4"
        self.source.write_bytes(b"read-only-source-fixture")
        self.output = self.root / "package"

    def patch(self, name, **options):
        patcher = mock.patch.object(adapter, name, **options)
        self.addCleanup(patcher.stop)
        return patcher.start()


class PackagingTests(TemporaryFiles):
    def setUp(self):
        super().setUp()
        quartz = SimpleNamespace(
            kCGImageDestinationLossyCompressionQuality="quality",
            CGImageDestinationCreateWithURL=lambda url, *unused: Path(url),
            CGImageDestinationAddImage=lambda *unused: None,
            CGImageDestinationFinalize=lambda path: bool(path.write_bytes(b"real-video-frame-fixture")),
        )
        native = SimpleNamespace(
            Quartz=quartz,
            Foundation=SimpleNamespace(NSURL=SimpleNamespace(fileURLWithPath_=lambda path: path)),
            AVFoundation=SimpleNamespace(AVURLAsset=SimpleNamespace(URLAssetWithURL_options_=lambda *unused: object())),
        )
        self.native = self.patch("_native", return_value=native)
        self.patch("_tool", side_effect=lambda name: name)
        self.probe = {
            "streams": [{"codec_type": "video", "index": 0, "duration": "4", "width": 160, "height": 120}],
            "format": {"duration": "4", "tags": {"encoder": "fixture", "creation_time": "2026-11-15T00:00:00Z",
                                                     "download_url": "https://invalid.test/?signature=never-retain", "comment": "private"}},
        }

        def command(arguments, **unused):
            if arguments[0] == "ffprobe":
                return json.dumps(self.probe)
            Path(arguments[-1]).write_bytes(b"normalized-video-fixture")
            return ""

        self.runner = self.patch("_run", side_effect=command)
        self.anchor = self.patch("_anchor_frame", return_value=(object(), 1.5))
        self.patch("_write_photo", side_effect=lambda native, source, target, identifier: target.write_bytes(source.read_bytes()))
        self.writer = self.patch("_write_video", side_effect=lambda native, source, target, *unused: target.write_bytes(b"paired-video-fixture"))
        self.verifier = self.patch("verify", return_value={
            "passed_local_validation": True, "matching_identifiers": True, "marker_time": 1.5,
            "photo_library_modified": False, "iphone_verified": False, "publishing_app_verified": False,
        })

    def test_package_copies_self_contained_resources_and_valid_plist(self):
        original = self.source.read_bytes()
        result = adapter.package(self.source, self.output)
        bundle = Path(result["pvt_package"])
        self.assertTrue(bundle.is_absolute())
        self.assertEqual(bundle.name, "LIVE.pvt")
        for name in ("LIVE.jpg", "LIVE.mov"):
            self.assertEqual((self.output / name).read_bytes(), (bundle / name).read_bytes())
            self.assertFalse((bundle / name).is_symlink())
        self.assertEqual(plistlib.loads((bundle / "metadata.plist").read_bytes()), {"PFVideoComplementMetadataVersionKey": "1"})
        self.assertEqual(self.source.read_bytes(), original)
        self.assertTrue(result["source_video_unchanged"])
        self.assertFalse(result["photo_library_modified"])
        self.assertFalse(result["resolution_upscaled"])
        self.assertEqual(json.loads((self.output / "verification.json").read_text()), result)
        arguments = self.runner.call_args_list[1].args[0]
        self.assertIn("-n", arguments)
        self.assertEqual(arguments[arguments.index("-vf") + 1], "fps=30,format=yuv420p")

    def test_receipt_metadata_is_whitelisted(self):
        result = adapter.package(self.source, self.output)
        self.assertEqual(set(result["source_metadata"]), {"encoder", "creation_time"})
        self.assertNotIn("signature", (self.output / "verification.json").read_text())

    def test_finite_positive_arguments_checked_before_native_calls(self):
        for argument in ("duration", "still_time"):
            for value in (0, -1, float("nan"), float("inf"), float("-inf"), True, "3", 10 ** 1000):
                with self.subTest(argument=argument, value_type=type(value).__name__):
                    with self.assertRaises(ValueError):
                        adapter.package(self.source, self.output, **{argument: value})
        self.native.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_invalid_names_cannot_escape_output_or_clobber_intermediates(self):
        for name in ("", " ", ".", "..", "a/b", "a\\b", "a:b", "a\0b", "a\nb", "cover-frame", "NORMALIZED-SOURCE"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    adapter.package(self.source, self.output, name=name)
        self.native.assert_not_called()

    def test_existing_directory_and_source_target_are_never_overwritten(self):
        self.output.mkdir()
        marker = self.output / "preserve.txt"
        marker.write_bytes(b"preserve")
        for target in (self.output, self.source):
            with self.assertRaises(FileExistsError):
                adapter.package(self.source, target)
        self.assertEqual(marker.read_bytes(), b"preserve")
        self.native.assert_not_called()

    @unittest.skipUnless(hasattr(os, "symlink"), "Symbolic links are unavailable")
    def test_source_output_and_parent_symbolic_links_are_rejected(self):
        source_link = self.root / "linked.mp4"
        source_link.symlink_to(self.source)
        directory_link = self.root / "linked-directory"
        directory_link.symlink_to(self.root, target_is_directory=True)
        dangling = self.root / "dangling"
        dangling.symlink_to(self.root / "missing")
        for source, output in ((source_link, self.output), (self.source, directory_link / "new"), (self.source, dangling)):
            with self.subTest(source=source, output=output):
                with self.assertRaisesRegex(ValueError, "Symbolic links"):
                    adapter.package(source, output)
        self.native.assert_not_called()

    def test_clip_bounds_rejected_without_output(self):
        for arguments in ({"duration": 5}, {"duration": 0.01}, {"still_time": 3}, {"still_time": 2.99}):
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    adapter.package(self.source, self.output, **arguments)
        self.assertFalse(self.output.exists())

    def test_none_duration_uses_source_and_anchor_is_frame_aligned(self):
        self.anchor.return_value = (object(), 1.5)
        result = adapter.package(self.source, self.output, duration=None, still_time=1.51)
        self.assertEqual(result["cover_time_seconds"], 1.5)
        self.assertFalse(result["trimmed"])
        self.assertEqual(self.writer.call_args.args[-1], 4)

    def test_failed_validation_retains_diagnostics_without_pvt(self):
        self.verifier.return_value = {"passed_local_validation": False, "errors": ["fixture validation failure"]}
        result = adapter.package(self.source, self.output)
        self.assertFalse(result["passed_local_validation"])
        self.assertIsNone(result["pvt_package"])
        self.assertFalse((self.output / "LIVE.pvt").exists())
        self.assertTrue((self.output / "normalized-source.mov").is_file())
        self.assertTrue((self.output / "verification.json").is_file())

    def test_native_failure_retains_inputs_intermediates_and_failure_receipt(self):
        self.writer.side_effect = RuntimeError("fixture writer failed")
        with self.assertRaisesRegex(RuntimeError, "fixture writer failed"):
            adapter.package(self.source, self.output)
        failure = json.loads((self.output / "failure.json").read_text())
        self.assertFalse(failure["passed_local_validation"])
        self.assertTrue(failure["source_video_unchanged"])
        self.assertTrue((self.output / "normalized-source.mov").is_file())
        self.assertTrue((self.output / "cover-frame.jpg").is_file())
        self.assertTrue((self.output / "LIVE.jpg").is_file())
        self.assertEqual(self.source.read_bytes(), b"read-only-source-fixture")
        with self.assertRaises(FileExistsError):
            adapter.package(self.source, self.output)


class VerificationTests(TemporaryFiles):
    def setUp(self):
        super().setUp()
        self.photo = self.root / "photo.jpg"
        self.photo.write_bytes(b"photo-fixture")
        properties = {"maker": {"17": "fixture-id"}, "width": 160, "height": 120}
        identifier = SimpleNamespace(identifier=lambda: "mdta/" + adapter.IDENTIFIER, value=lambda: "fixture-id")
        track = SimpleNamespace(naturalSize=lambda: SimpleNamespace(width=160, height=120),
                                preferredTransform=lambda: SimpleNamespace(a=1, b=0, c=0, d=1))
        asset = SimpleNamespace(metadata=lambda: [identifier], duration=lambda: 3, tracksWithMediaType_=lambda kind: [track])
        native = SimpleNamespace(
            Foundation=SimpleNamespace(NSURL=SimpleNamespace(fileURLWithPath_=lambda path: path)),
            Quartz=SimpleNamespace(CGImageSourceCreateWithURL=lambda *unused: object(),
                                   CGImageSourceCopyPropertiesAtIndex=lambda *unused: properties,
                                   kCGImagePropertyMakerAppleDictionary="maker", kCGImagePropertyPixelWidth="width",
                                   kCGImagePropertyPixelHeight="height"),
            AVFoundation=SimpleNamespace(AVURLAsset=SimpleNamespace(URLAssetWithURL_options_=lambda *unused: asset), AVMediaTypeVideo="video"),
            CoreMedia=SimpleNamespace(CMTimeGetSeconds=lambda value: value),
        )
        self.properties = properties
        self.patch("_native", return_value=native)
        self.markers = [{"identifier": "mdta/" + adapter.MARKER, "key": adapter.MARKER, "data_type": adapter.INT8,
                         "value": 0, "time_seconds": 1.5, "duration_seconds": 1 / 30, "metadata_referent_valid": True}]
        self.metadata = self.patch("_metadata", return_value=self.markers)
        self.cover = self.patch("_cover", return_value={"matches": True, "grayscale_psnr_db": 43})
        self.decode = self.patch("_decode", return_value=(True, [{"degraded": False}], False))

    def test_positive_report_contains_hashes_sources_and_honest_boundaries(self):
        result = adapter.verify(self.photo, self.source)
        self.assertTrue(result["passed_local_validation"])
        self.assertEqual(result["source"], {"photo": str(self.photo), "video": str(self.source)})
        self.assertEqual(result["photo_sha256"], adapter._hash(self.photo))
        self.assertEqual(result["video_sha256"], adapter._hash(self.source))
        self.assertEqual(result["marker_time"], 1.5)
        for key in ("photo_library_modified", "iphone_verified", "publishing_app_verified"):
            self.assertIs(result[key], False)

    def test_identifier_mismatch_fails_even_when_photos_decodes(self):
        self.properties["maker"]["17"] = "different-id"
        result = adapter.verify(self.photo, self.source)
        self.assertFalse(result["matching_identifiers"])
        self.assertTrue(result["phlivephoto_decoded"])
        self.assertFalse(result["passed_local_validation"])

    def test_marker_requires_int8_zero_in_range_and_valid_referent(self):
        for field, value in (("data_type", "int16"), ("value", 1), ("value", "0"), ("value", 0.0),
                             ("time_seconds", -1), ("time_seconds", 3), ("time_seconds", float("nan")),
                             ("duration_seconds", 0), ("duration_seconds", 2), ("metadata_referent_valid", False)):
            with self.subTest(field=field, value=value):
                self.metadata.return_value = [dict(self.markers[0], **{field: value})]
                result = adapter.verify(self.photo, self.source)
                self.assertFalse(result["still_image_marker_in_video"])
                self.assertFalse(result["passed_local_validation"])

    def test_missing_or_duplicate_marker_does_not_pass(self):
        for markers in ([], self.markers * 2):
            self.metadata.return_value = markers
            result = adapter.verify(self.photo, self.source)
            self.assertFalse(result["passed_local_validation"])
            self.assertIsNone(result["marker_time"])

    def test_dimension_cover_and_decode_mismatches_fail(self):
        self.properties["width"] = 200
        self.assertFalse(adapter.verify(self.photo, self.source)["passed_local_validation"])
        self.properties["width"] = 160
        self.cover.return_value = {"matches": False, "grayscale_psnr_db": 29}
        self.assertFalse(adapter.verify(self.photo, self.source)["passed_local_validation"])
        self.cover.return_value = {"matches": True}
        self.decode.return_value = (False, [{"degraded": True}], True)
        self.assertFalse(adapter.verify(self.photo, self.source)["passed_local_validation"])

    def test_bad_media_returns_failure_diagnostics(self):
        self.metadata.side_effect = RuntimeError("fixture invalid media")
        result = adapter.verify(self.photo, self.source)
        self.assertFalse(result["passed_local_validation"])
        self.assertIn("fixture invalid media", result["errors"][0])

    def test_timeout_must_be_finite_and_positive(self):
        for timeout in (0, -1, float("nan"), float("inf"), True):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    adapter.verify(self.photo, self.source, timeout=timeout)

    def test_same_file_cannot_be_both_resources(self):
        with self.assertRaises(ValueError):
            adapter.verify(self.source, self.source)


class CallbackTests(unittest.TestCase):
    def decode(self, entries):
        photos = SimpleNamespace(
            PHLivePhotoInfoIsDegradedKey="degraded", PHLivePhotoInfoCancelledKey="cancelled",
            PHLivePhotoInfoErrorKey="error", PHImageContentModeAspectFit=0,
        )

        def request(*arguments):
            callback = arguments[-1]
            for loaded, information in entries:
                callback(object() if loaded else None, information)
            return 42

        photos.PHLivePhoto = SimpleNamespace(
            requestLivePhotoWithResourceFileURLs_placeholderImage_targetSize_contentMode_resultHandler_=request,
            cancelLivePhotoRequestWithRequestID_=mock.Mock(),
        )
        native = SimpleNamespace(Photos=photos)
        with mock.patch.object(adapter, "_url", side_effect=lambda native, path: str(path)), mock.patch.object(adapter, "_pump"):
            result = adapter._decode(native, Path("fixture.jpg"), Path("fixture.mov"), 0.002)
        return result, photos.PHLivePhoto.cancelLivePhotoRequestWithRequestID_

    def test_only_complete_non_degraded_callback_passes(self):
        result, cancel = self.decode([(True, {"degraded": True}), (True, {"degraded": False})])
        self.assertTrue(result[0])
        self.assertFalse(result[2])
        cancel.assert_not_called()

    def test_degraded_only_is_cancelled_and_does_not_pass(self):
        result, cancel = self.decode([(True, {"degraded": True})])
        self.assertFalse(result[0])
        self.assertTrue(result[2])
        cancel.assert_called_once_with(42)

    def test_failed_cancelled_or_error_callbacks_do_not_pass(self):
        for entries in ([(False, {})], [(True, {"cancelled": True})], [(True, {"error": "fixture error"})],
                        [(True, {"degraded": True}), (False, {"degraded": False})]):
            with self.subTest(entries=entries):
                self.assertFalse(self.decode(entries)[0][0])


@unittest.skipUnless(sys.platform == "darwin" and os.environ.get("JARDEN_LIVE_PHOTO_NATIVE_TESTS") == "1",
                     "Set JARDEN_LIVE_PHOTO_NATIVE_TESTS=1 on macOS for local native fixtures; no network or Photos import")
class NativeIntegrationTests(TemporaryFiles):
    def test_native_positive_and_isolated_negative_pairs(self):
        try:
            native = adapter._native()
            ffmpeg = adapter._tool("ffmpeg")
            adapter._tool("ffprobe")
        except RuntimeError as error:
            self.skipTest(str(error))
        self.source.unlink()
        adapter._run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-f", "lavfi", "-i",
                      "testsrc2=size=160x120:rate=30", "-t", "3", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(self.source)])
        original_hash = adapter._hash(self.source)
        result = adapter.package(self.source, self.output)
        self.assertTrue(result["passed_local_validation"], result)
        self.assertFalse(result["photo_library_modified"])
        bundle = Path(result["pvt_package"])
        self.assertTrue(adapter.verify(bundle / "LIVE.jpg", bundle / "LIVE.mov")["passed_local_validation"])
        mismatch = self.root / "mismatched.jpg"
        adapter._write_photo(native, Path(result["photo"]), mismatch, "isolated-mismatch-fixture")
        negative = adapter.verify(mismatch, Path(result["video"]), timeout=2)
        self.assertFalse(negative["matching_identifiers"])
        self.assertFalse(negative["passed_local_validation"])
        no_marker = self.root / "no-marker.mov"
        export_without_marker(native, self.output / "normalized-source.mov", no_marker, result["photo_content_identifier"])
        negative = adapter.verify(Path(result["photo"]), no_marker, timeout=2)
        self.assertTrue(negative["matching_identifiers"], negative)
        self.assertFalse(negative["still_image_marker_in_video"])
        self.assertFalse(negative["passed_local_validation"])
        self.assertEqual(adapter._hash(self.source), original_hash)


if __name__ == "__main__":
    unittest.main()
