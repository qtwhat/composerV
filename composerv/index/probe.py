"""Media inspection via ffprobe. STUB (TDD red).

The ffprobe call (`_run_ffprobe`) is separated from the pure parser (`build_media_info`)
so the parser can be unit-tested with canned JSON without the file present.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime

from pydantic import BaseModel

PHOTO_EXTS = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".tif", ".tiff", ".dng", ".raf", ".cr2", ".nef", ".arw"}
# camera-generated low-res proxies we can reuse instead of transcoding: DJI .LRF, GoPro .LRV
CAMERA_PROXY_EXTS = {".lrf", ".lrv"}
_HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}  # PQ (HDR10) and HLG


class MediaInfo(BaseModel):
    path: str
    kind: str  # "video" | "photo"
    codec: str = ""
    width: int = 0
    height: int = 0
    fps_num: int = 0
    fps_den: int = 1
    is_vfr: bool = False
    pix_fmt: str = ""
    bit_depth: int = 8
    is_hdr: bool = False
    has_audio: bool = False
    audio_sample_rate: int = 0
    audio_channels: int = 0
    duration_s: float = 0.0
    capture_time: str | None = None  # ISO8601, parsed from filename (EXIF later)
    camera_proxy: str | None = None  # sibling .LRF/.LRV if present
    proxy_path: str | None = None  # our generated uniform proxy, once made


def bit_depth_from_pix_fmt(pix_fmt: str | None) -> int:
    if not pix_fmt:
        return 8
    if "12" in pix_fmt:
        return 12
    if "10" in pix_fmt:
        return 10
    return 8


def is_hdr_transfer(transfer: str | None) -> bool:
    return transfer in _HDR_TRANSFERS


def parse_capture_time(filename: str) -> str | None:
    """Parse a YYYYMMDDHHMMSS run from the filename (DJI pattern). ISO8601 or None."""
    m = re.search(r"(\d{14})", os.path.basename(filename))
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d%H%M%S").isoformat()
    except ValueError:
        return None


def find_camera_proxy(path: str) -> str | None:
    """A sibling camera-generated proxy (DJI .LRF / GoPro .LRV) with the same stem.

    Scans the directory and returns the real filename (rather than constructing one),
    so it works on case-insensitive filesystems and preserves actual casing.
    """
    d = os.path.dirname(path) or "."
    stem = os.path.splitext(os.path.basename(path))[0]
    try:
        entries = os.listdir(d)
    except OSError:
        return None
    for name in entries:
        base, ext = os.path.splitext(name)
        if base == stem and ext.lower() in CAMERA_PROXY_EXTS:
            return os.path.join(d, name)
    return None


def _parse_rate(rate: str | None) -> tuple[int, int]:
    if not rate or "/" not in rate:
        return (0, 1)
    num, den = rate.split("/", 1)
    try:
        return (int(num), int(den))
    except ValueError:
        return (0, 1)


def _rate_value(rate: str | None) -> float:
    num, den = _parse_rate(rate)
    return num / den if den else 0.0


def build_media_info(path: str, probe_json: dict) -> MediaInfo:
    streams = probe_json.get("streams", [])
    fmt = probe_json.get("format", {})

    videos = [s for s in streams if s.get("codec_type") == "video"]
    # ignore embedded thumbnails / cover art (attached_pic); prefer a real, timed stream
    primary = next(
        (s for s in videos if not s.get("disposition", {}).get("attached_pic")),
        videos[0] if videos else None,
    )
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    audio = audios[0] if audios else None

    kind = "photo" if os.path.splitext(path)[1].lower() in PHOTO_EXTS else "video"

    info = MediaInfo(path=path, kind=kind, capture_time=parse_capture_time(path),
                     camera_proxy=find_camera_proxy(path))
    try:
        info.duration_s = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        info.duration_s = 0.0

    if primary is not None:
        info.codec = primary.get("codec_name") or ""
        info.width = int(primary.get("width") or 0)
        info.height = int(primary.get("height") or 0)
        info.pix_fmt = primary.get("pix_fmt") or ""
        info.bit_depth = bit_depth_from_pix_fmt(info.pix_fmt)
        info.is_hdr = is_hdr_transfer(primary.get("color_transfer"))
        avg = primary.get("avg_frame_rate")
        r = primary.get("r_frame_rate")
        # actual rate = avg if valid, else nominal r
        info.fps_num, info.fps_den = _parse_rate(avg) if _rate_value(avg) else _parse_rate(r)
        # VFR: nominal and actual rates disagree (both must be valid to judge)
        info.is_vfr = bool(_rate_value(avg) and _rate_value(r) and abs(_rate_value(avg) - _rate_value(r)) > 1e-6)

    if audio is not None:
        info.has_audio = True
        info.audio_sample_rate = int(audio.get("sample_rate") or 0)
        info.audio_channels = int(audio.get("channels") or 0)

    return info


def _run_ffprobe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)


def probe_media(path: str) -> MediaInfo:
    return build_media_info(path, _run_ffprobe(path))
