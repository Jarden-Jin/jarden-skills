"""Local Live Photo packaging; importing this module never loads Apple frameworks."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import os
import plistlib
import shutil
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


IDENTIFIER = "com.apple.quicktime.content.identifier"
MARKER = "com.apple.quicktime.still-image-time"
INT8 = "com.apple.metadata.datatype.int8"
INSTALL_HINT = (
    "Optional macOS setup, in your chosen environment: python3 -m pip install "
    "pyobjc-framework-AVFoundation pyobjc-framework-CoreMedia pyobjc-framework-Quartz pyobjc-framework-Photos"
)


def _native():
    if sys.platform != "darwin":
        raise RuntimeError("Live Photo native operations require macOS; module import is portable.")
    try:
        return SimpleNamespace(**{
            name: importlib.import_module(name)
            for name in ("AVFoundation", "CoreMedia", "Foundation", "Photos", "Quartz")
        })
    except (ImportError, OSError) as error:
        raise RuntimeError(f"Apple PyObjC frameworks unavailable. {INSTALL_HINT}") from error


def _finite(value, label, *, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite positive number")
    try:
        number = float(value)
    except OverflowError as error:
        raise ValueError(f"{label} must be a finite positive number") from error
    if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
        raise ValueError(f"{label} must be a finite positive number")
    return number


def _path(value):
    path = Path(os.path.abspath(Path(value).expanduser()))
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError(f"Symbolic links are not accepted: {component}")
    return path


def _input(value):
    path = _path(value)
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tool(name):
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"{name} is required; optionally install ffmpeg, e.g. brew install ffmpeg. Nothing was installed.")
    return executable


def _run(arguments, *, timeout=120):
    completed = subprocess.run(arguments, capture_output=True, text=True, timeout=timeout, check=False)
    if completed.returncode:
        raise RuntimeError(f"{Path(arguments[0]).name} failed ({completed.returncode}): {completed.stderr}")
    return completed.stdout


def _json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def _url(native, path):
    return native.Foundation.NSURL.fileURLWithPath_(str(path))


def _pump(native):
    native.Foundation.NSRunLoop.currentRunLoop().runUntilDate_(
        native.Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.02)
    )


def _error(label, error):
    return RuntimeError(f"{label}: {error if error is not None else 'unknown native error'}")


def _write_photo(native, source, destination, identifier):
    quartz = native.Quartz
    image = quartz.CGImageSourceCreateWithURL(_url(native, source), None)
    if image is None:
        raise RuntimeError("ImageIO could not read the extracted cover")
    properties = dict(quartz.CGImageSourceCopyPropertiesAtIndex(image, 0, None) or {})
    maker = dict(properties.get(quartz.kCGImagePropertyMakerAppleDictionary, {}))
    maker["17"] = identifier
    properties[quartz.kCGImagePropertyMakerAppleDictionary] = maker
    target = quartz.CGImageDestinationCreateWithURL(_url(native, destination), "public.jpeg", 1, None)
    if target is None:
        raise RuntimeError("ImageIO could not create the cover destination")
    quartz.CGImageDestinationAddImageFromSource(target, image, 0, properties)
    if not quartz.CGImageDestinationFinalize(target):
        raise RuntimeError("ImageIO could not finalize MakerApple17")


def _write_video(native, source, destination, identifier, anchor, duration):
    foundation = native.AVFoundation
    media = native.CoreMedia
    asset = foundation.AVURLAsset.URLAssetWithURL_options_(_url(native, source), None)
    tracks = asset.tracksWithMediaType_(foundation.AVMediaTypeVideo)
    if len(tracks) != 1:
        raise RuntimeError("Packaging requires exactly one normalized video track")
    track = tracks[0]
    reader, error = foundation.AVAssetReader.assetReaderWithAsset_error_(asset, None)
    if reader is None or error is not None:
        raise _error("AVAssetReader", error)
    output = foundation.AVAssetReaderTrackOutput.alloc().initWithTrack_outputSettings_(track, None)
    reader.addOutput_(output)
    writer, error = foundation.AVAssetWriter.assetWriterWithURL_fileType_error_(
        _url(native, destination), foundation.AVFileTypeQuickTimeMovie, None
    )
    if writer is None or error is not None:
        raise _error("AVAssetWriter", error)
    video_input = foundation.AVAssetWriterInput.assetWriterInputWithMediaType_outputSettings_sourceFormatHint_(
        foundation.AVMediaTypeVideo, None, track.formatDescriptions()[0]
    )
    video_input.setTransform_(track.preferredTransform())
    video_input.setExpectsMediaDataInRealTime_(False)
    status, description = media.CMMetadataFormatDescriptionCreateWithMetadataSpecifications(
        None, media.kCMMetadataFormatType_Boxed,
        [{media.kCMMetadataFormatDescriptionMetadataSpecificationKey_Identifier: "mdta/" + MARKER,
          media.kCMMetadataFormatDescriptionMetadataSpecificationKey_DataType: INT8}], None
    )
    if status:
        raise RuntimeError(f"Metadata format creation failed: {status}")
    metadata_input = foundation.AVAssetWriterInput.assetWriterInputWithMediaType_outputSettings_sourceFormatHint_(
        foundation.AVMediaTypeMetadata, None, description
    )
    metadata_input.setExpectsMediaDataInRealTime_(False)
    if not writer.canAddInput_(video_input) or not writer.canAddInput_(metadata_input):
        raise RuntimeError("AVAssetWriter cannot accept video and timed metadata")
    writer.addInput_(video_input)
    writer.addInput_(metadata_input)
    metadata_input.addTrackAssociationWithTrackOfInput_type_(
        video_input, foundation.AVTrackAssociationTypeMetadataReferent
    )
    adaptor = foundation.AVAssetWriterInputMetadataAdaptor.alloc().initWithAssetWriterInput_(metadata_input)
    content = foundation.AVMutableMetadataItem.metadataItem()
    content.setKey_(IDENTIFIER)
    content.setKeySpace_("mdta")
    content.setValue_(identifier)
    content.setDataType_("com.apple.metadata.datatype.UTF-8")
    writer.setMetadata_([content])
    try:
        if not writer.startWriting():
            raise _error("startWriting", writer.error())
        writer.startSessionAtSourceTime_(media.CMTimeMake(0, 600))
        if not reader.startReading():
            raise _error("startReading", reader.error())
        marker = foundation.AVMutableMetadataItem.metadataItem()
        marker.setKey_(MARKER)
        marker.setKeySpace_("mdta")
        marker.setValue_(0)
        marker.setDataType_(INT8)
        group = foundation.AVTimedMetadataGroup.alloc().initWithItems_timeRange_(
            [marker], media.CMTimeRangeMake(media.CMTimeMakeWithSeconds(anchor, 600), media.CMTimeMake(1, 30))
        )
        deadline = time.monotonic() + 120
        while not metadata_input.isReadyForMoreMediaData():
            if time.monotonic() >= deadline or writer.status() == foundation.AVAssetWriterStatusFailed:
                raise _error("Timed metadata input not ready", writer.error())
            time.sleep(0.005)
        if not adaptor.appendTimedMetadataGroup_(group):
            raise _error("appendTimedMetadataGroup", writer.error())
        metadata_input.markAsFinished()
        while True:
            if time.monotonic() >= deadline:
                raise RuntimeError("Native packaging timed out; intermediate files are retained")
            if writer.status() == foundation.AVAssetWriterStatusFailed:
                raise _error("write video", writer.error())
            if not video_input.isReadyForMoreMediaData():
                time.sleep(0.005)
                continue
            sample = output.copyNextSampleBuffer()
            if sample is None:
                break
            if not video_input.appendSampleBuffer_(sample):
                raise _error("appendSampleBuffer", writer.error())
        if reader.status() == foundation.AVAssetReaderStatusFailed:
            raise _error("read video", reader.error())
        video_input.markAsFinished()
        writer.endSessionAtSourceTime_(media.CMTimeMakeWithSeconds(duration, 600))
        completed = threading.Event()
        writer.finishWritingWithCompletionHandler_(completed.set)
        while not completed.is_set() and time.monotonic() < deadline:
            _pump(native)
        if not completed.is_set() or writer.status() != foundation.AVAssetWriterStatusCompleted:
            raise _error("finishWriting", writer.error())
    except BaseException:
        reader.cancelReading()
        writer.cancelWriting()
        raise


def _metadata(native, asset):
    foundation = native.AVFoundation
    media = native.CoreMedia
    video_ids = {int(track.trackID()) for track in asset.tracksWithMediaType_(foundation.AVMediaTypeVideo)}
    result = []
    for track in asset.tracksWithMediaType_(foundation.AVMediaTypeMetadata):
        referents = [int(item.trackID()) for item in track.associatedTracksOfType_(foundation.AVTrackAssociationTypeMetadataReferent)]
        reader, error = foundation.AVAssetReader.assetReaderWithAsset_error_(asset, None)
        if reader is None or error is not None:
            raise _error("metadata reader", error)
        output = foundation.AVAssetReaderTrackOutput.alloc().initWithTrack_outputSettings_(track, None)
        adaptor = foundation.AVAssetReaderOutputMetadataAdaptor.alloc().initWithAssetReaderTrackOutput_(output)
        reader.addOutput_(output)
        if not reader.startReading():
            raise _error("start metadata reader", reader.error())
        while True:
            group = adaptor.nextTimedMetadataGroup()
            if group is None:
                break
            for item in group.items():
                value = item.value()
                result.append({
                    "identifier": str(item.identifier()), "key": str(item.key()),
                    "data_type": str(item.dataType()),
                    "value": int(value) if isinstance(value, int) else str(value),
                    "time_seconds": media.CMTimeGetSeconds(group.timeRange().start),
                    "duration_seconds": media.CMTimeGetSeconds(group.timeRange().duration),
                    "track_id": int(track.trackID()), "referent_track_ids": referents,
                    "metadata_referent_valid": bool(referents) and set(referents).issubset(video_ids),
                })
        if reader.status() == foundation.AVAssetReaderStatusFailed:
            raise _error("read metadata", reader.error())
    return result


def _decode(native, photo, video, timeout):
    photos = native.Photos
    callbacks = []
    finished = threading.Event()

    def callback(live_photo, information):
        information = information or {}
        entry = {
            "live_photo_loaded": live_photo is not None,
            "degraded": bool(information.get(photos.PHLivePhotoInfoIsDegradedKey, False)),
            "cancelled": bool(information.get(photos.PHLivePhotoInfoCancelledKey, False)),
            "error": str(information[photos.PHLivePhotoInfoErrorKey]) if information.get(photos.PHLivePhotoInfoErrorKey) is not None else None,
            "information": {str(key): str(value) for key, value in information.items()},
        }
        callbacks.append(entry)
        if not entry["degraded"]:
            finished.set()

    request = photos.PHLivePhoto.requestLivePhotoWithResourceFileURLs_placeholderImage_targetSize_contentMode_resultHandler_(
        [_url(native, photo), _url(native, video)], None, (0, 0), photos.PHImageContentModeAspectFit, callback
    )
    deadline = time.monotonic() + timeout
    while not finished.is_set() and time.monotonic() < deadline:
        _pump(native)
    timed_out = not finished.is_set()
    if timed_out:
        photos.PHLivePhoto.cancelLivePhotoRequestWithRequestID_(request)
    entries = list(callbacks)
    decoded = not timed_out and any(
        item["live_photo_loaded"] and not item["degraded"] and not item["cancelled"] and item["error"] is None
        for item in entries
    )
    return decoded, entries, timed_out


def _pixels(native, image):
    quartz = native.Quartz
    width = int(quartz.CGImageGetWidth(image))
    height = int(quartz.CGImageGetHeight(image))
    context = quartz.CGBitmapContextCreate(None, width, height, 8, width, quartz.CGColorSpaceCreateDeviceGray(), quartz.kCGImageAlphaNone)
    if context is None:
        raise RuntimeError("Could not create the cover comparison context")
    quartz.CGContextDrawImage(context, ((0, 0), (width, height)), image)
    pixels = quartz.CGBitmapContextGetData(context).as_buffer(width * height)
    return bytes(pixels)


def _anchor_frame(native, asset, anchor, timeout):
    generator = native.AVFoundation.AVAssetImageGenerator.assetImageGeneratorWithAsset_(asset)
    generator.setAppliesPreferredTrackTransform_(True)
    generator.setRequestedTimeToleranceBefore_(native.CoreMedia.kCMTimeZero)
    generator.setRequestedTimeToleranceAfter_(native.CoreMedia.kCMTimeZero)
    generated = []
    completed = threading.Event()

    def callback(image, actual, error):
        generated.append((image, actual, error))
        completed.set()

    generator.generateCGImageAsynchronouslyForTime_completionHandler_(
        native.CoreMedia.CMTimeMakeWithSeconds(anchor, 600), callback
    )
    deadline = time.monotonic() + timeout
    while not completed.is_set() and time.monotonic() < deadline:
        _pump(native)
    if not completed.is_set():
        generator.cancelAllCGImageGeneration()
        raise RuntimeError("Anchor frame decode timed out")
    frame, actual, error = generated[0]
    if frame is None or error is not None:
        raise _error("Read anchor frame", error)
    return frame, native.CoreMedia.CMTimeGetSeconds(actual)


def _cover(native, source, asset, anchor, timeout):
    frame, actual_time = _anchor_frame(native, asset, anchor, timeout)
    cover = native.Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    frame_size = [int(native.Quartz.CGImageGetWidth(frame)), int(native.Quartz.CGImageGetHeight(frame))]
    cover_size = [int(native.Quartz.CGImageGetWidth(cover)), int(native.Quartz.CGImageGetHeight(cover))]
    if frame_size != cover_size:
        return {"matches": False, "actual_time_seconds": actual_time, "frame_size": frame_size}
    photo_pixels = _pixels(native, cover)
    frame_pixels = _pixels(native, frame)
    mean_squared_error = sum((left - right) ** 2 for left, right in zip(photo_pixels, frame_pixels)) / len(photo_pixels)
    psnr = 10 * math.log10(255 ** 2 / mean_squared_error) if mean_squared_error else None
    return {
        "matches": abs(actual_time - anchor) <= 1 / 600 and (psnr is None or psnr >= 30),
        "actual_time_seconds": actual_time, "frame_size": frame_size,
        "grayscale_psnr_db": psnr, "minimum_psnr_db": 30,
        "comparison": "full_resolution_grayscale_pixels_at_exact_anchor",
        "photo_pixels_sha256": hashlib.sha256(photo_pixels).hexdigest(),
        "anchor_pixels_sha256": hashlib.sha256(frame_pixels).hexdigest(),
    }


def verify(photo: Path, video: Path, *, timeout=25) -> dict:
    """Inspect resources and request an in-memory PHLivePhoto, without library access."""
    timeout = _finite(timeout, "timeout")
    photo = _input(photo)
    video = _input(video)
    if photo.samefile(video):
        raise ValueError("Photo and video must be different files")
    native = _native()
    result = {
        "source": {"photo": str(photo), "video": str(video)},
        "photo": str(photo), "video": str(video),
        "photo_sha256": _hash(photo), "video_sha256": _hash(video),
        "matching_identifiers": False, "marker_time": None,
        "matching_dimensions": False, "phlivephoto_decoded": False,
        "passed_local_validation": False, "photo_library_modified": False,
        "iphone_verified": False, "publishing_app_verified": False, "errors": [],
    }
    try:
        source = native.Quartz.CGImageSourceCreateWithURL(_url(native, photo), None)
        if source is None:
            raise RuntimeError("ImageIO could not read the photo")
        properties = native.Quartz.CGImageSourceCopyPropertiesAtIndex(source, 0, None)
        maker = properties.get(native.Quartz.kCGImagePropertyMakerAppleDictionary, {})
        photo_id = maker.get("17")
        asset = native.AVFoundation.AVURLAsset.URLAssetWithURL_options_(_url(native, video), None)
        identifiers = [str(item.value()) for item in asset.metadata() if str(item.identifier()) == "mdta/" + IDENTIFIER]
        video_id = identifiers[0] if len(identifiers) == 1 else None
        duration = native.CoreMedia.CMTimeGetSeconds(asset.duration())
        tracks = asset.tracksWithMediaType_(native.AVFoundation.AVMediaTypeVideo)
        video_size = None
        if len(tracks) == 1:
            size = tracks[0].naturalSize()
            transform = tracks[0].preferredTransform()
            video_size = [round(abs(size.width * transform.a) + abs(size.height * transform.c)),
                          round(abs(size.width * transform.b) + abs(size.height * transform.d))]
        photo_size = [int(properties[native.Quartz.kCGImagePropertyPixelWidth]), int(properties[native.Quartz.kCGImagePropertyPixelHeight])]
        metadata = _metadata(native, asset)
        markers = [item for item in metadata if item["identifier"] == "mdta/" + MARKER]
        valid_marker = len(markers) == 1 and all((
            markers[0]["data_type"] == INT8,
            type(markers[0]["value"]) is int and markers[0]["value"] == 0,
            math.isfinite(duration) and duration > 0,
            math.isfinite(markers[0]["time_seconds"]) and 0 <= markers[0]["time_seconds"] < duration,
            math.isfinite(markers[0]["duration_seconds"]) and markers[0]["duration_seconds"] > 0,
            markers[0]["time_seconds"] + markers[0]["duration_seconds"] <= duration + 1 / 600,
            markers[0]["metadata_referent_valid"],
        ))
        result.update({
            "photo_content_identifier": str(photo_id) if photo_id else None,
            "video_content_identifier": video_id,
            "matching_identifiers": bool(photo_id and str(photo_id) == video_id),
            "timed_metadata": metadata, "still_image_marker_in_video": bool(valid_marker),
            "marker_time": markers[0]["time_seconds"] if len(markers) == 1 else None,
            "duration_seconds": duration if math.isfinite(duration) else None,
            "photo_size": photo_size, "video_size": video_size,
            "matching_dimensions": photo_size == video_size,
        })
        if valid_marker and result["matching_dimensions"]:
            result["cover_anchor"] = _cover(native, source, asset, result["marker_time"], timeout)
        else:
            result["cover_anchor"] = {"matches": False}
        decoded, callbacks, timed_out = _decode(native, photo, video, timeout)
        result.update({"phlivephoto_decoded": decoded, "phlivephoto_callbacks": callbacks, "phlivephoto_timed_out": timed_out})
        result["passed_local_validation"] = all((
            result["matching_identifiers"], valid_marker, result["matching_dimensions"],
            result["cover_anchor"]["matches"], decoded,
        ))
    except Exception as error:
        result["errors"].append(f"{type(error).__name__}: {error}")
    if result["photo_sha256"] != _hash(photo) or result["video_sha256"] != _hash(video):
        result["passed_local_validation"] = False
        result["errors"].append("Input bytes changed during verification")
    return result


def package(video_path: Path, output_path: Path, *, name="LIVE", still_time: float | None = None,
            duration: float | None = 3.0) -> dict:
    """Create a new self-contained .pvt and receipt; never import into Photos."""
    if not isinstance(name, str) or not name.strip() or name in (".", "..") or any(
        character in name for character in ("/", "\\", ":", "\0", "\n", "\r")
    ):
        raise ValueError("name must be a filename without path separators")
    if name.casefold() in ("cover-frame", "normalized-source"):
        raise ValueError("name collides with an intermediate filename")
    if duration is not None:
        duration = _finite(duration, "duration")
    if still_time is not None:
        still_time = _finite(still_time, "still_time")
    source = _input(video_path)
    output = _path(output_path)
    if output.exists():
        raise FileExistsError(f"Output already exists; refusing to overwrite: {output}")
    if output in source.parents:
        raise ValueError("Output cannot contain the source video")
    native = _native()
    ffmpeg = _tool("ffmpeg")
    ffprobe = _tool("ffprobe")
    probe = json.loads(_run([ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(source)]))
    tracks = [track for track in probe.get("streams", []) if track.get("codec_type") == "video" and not track.get("disposition", {}).get("attached_pic")]
    if len(tracks) != 1:
        raise ValueError("Source must contain exactly one video stream")
    source_duration = _finite(float(tracks[0].get("duration", probe.get("format", {}).get("duration", 0))), "source duration")
    clip_duration = duration if duration is not None else source_duration
    if clip_duration > source_duration:
        raise ValueError("duration cannot exceed source video duration")
    frame_count = math.floor(clip_duration * 30 + 1e-6)
    if frame_count < 2:
        raise ValueError("duration must contain at least two 30 fps frames")
    actual_duration = frame_count / 30
    requested_anchor = still_time if still_time is not None else actual_duration / 2
    if requested_anchor >= actual_duration - 1 / 30:
        raise ValueError("still_time must be inside the clip, with one following frame")
    anchor = math.floor(requested_anchor * 30 + 1e-6) / 30
    source_hash = _hash(source)
    output.mkdir(parents=True, exist_ok=False)
    receipt = {
        "source_video": str(source), "source_video_sha256": source_hash,
        "source_video_duration_seconds": source_duration,
        "packaged_at": datetime.now(timezone.utc).isoformat(),
        "requested_duration_seconds": duration, "requested_still_time_seconds": still_time,
        "cover_policy": "frame_extracted_from_normalized_video", "cover_time_seconds": anchor,
        "trimmed": actual_duration < source_duration, "resolution_upscaled": False,
        "watermark_policy": "preserved_no_crop_no_removal",
        "source_metadata": {key: value for key, value in probe.get("format", {}).get("tags", {}).items()
                            if key in ("encoder", "creation_time")},
        "audio_policy": "silent_no_audio_track", "loop_continuity_verified": False,
        "photo_library_modified": False, "iphone_verified": False, "publishing_app_verified": False,
        "passed_local_validation": False, "pvt_package": None,
    }
    try:
        normalized = output / "normalized-source.mov"
        extracted = output / "cover-frame.jpg"
        photo = output / f"{name}.jpg"
        movie = output / f"{name}.mov"
        _run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n", "-i", str(source),
              "-map", f"0:{tracks[0]['index']}", "-frames:v", str(frame_count), "-an", "-vf", "fps=30,format=yuv420p",
              "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-movflags", "+faststart", str(normalized)])
        asset = native.AVFoundation.AVURLAsset.URLAssetWithURL_options_(_url(native, normalized), None)
        frame, actual_anchor = _anchor_frame(native, asset, anchor, 25)
        if abs(actual_anchor - anchor) > 1 / 600:
            raise RuntimeError("Extracted cover frame does not match the requested anchor")
        destination = native.Quartz.CGImageDestinationCreateWithURL(_url(native, extracted), "public.jpeg", 1, None)
        if destination is None:
            raise RuntimeError("ImageIO could not create the extracted cover")
        native.Quartz.CGImageDestinationAddImage(destination, frame, {native.Quartz.kCGImageDestinationLossyCompressionQuality: 0.95})
        if not native.Quartz.CGImageDestinationFinalize(destination):
            raise RuntimeError("ImageIO could not save the extracted video frame")
        identifier = str(uuid.uuid4()).upper()
        _write_photo(native, extracted, photo, identifier)
        _write_video(native, normalized, movie, identifier, anchor, actual_duration)
        receipt.update(verify(photo, movie))
        receipt["source_video_unchanged"] = _hash(source) == source_hash
        if not receipt["source_video_unchanged"]:
            raise RuntimeError("Source video changed during packaging")
        if receipt["passed_local_validation"]:
            bundle = output / f"{name}.pvt"
            bundle.mkdir(exist_ok=False)
            for resource in (photo, movie):
                with resource.open("rb") as original, (bundle / resource.name).open("xb") as copy:
                    shutil.copyfileobj(original, copy)
                if _hash(resource) != _hash(bundle / resource.name):
                    raise RuntimeError("PVT resource copy does not match its verified hash")
            with (bundle / "metadata.plist").open("xb") as stream:
                plistlib.dump({"PFVideoComplementMetadataVersionKey": "1"}, stream, fmt=plistlib.FMT_XML)
            receipt["pvt_package"] = str(bundle)
        _json(output / "verification.json", receipt)
        return receipt
    except BaseException as error:
        receipt["passed_local_validation"] = False
        receipt["error"] = f"{type(error).__name__}: {error}"
        receipt["source_video_unchanged"] = source.is_file() and _hash(source) == source_hash
        _json(output / "failure.json", receipt)
        raise
