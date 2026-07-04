"""Tests for index.probe: ffprobe-backed media inspection.

The parser is split from the ffprobe call so it can be unit-tested with canned JSON
modeled on real DJI output (HEVC 10-bit + an embedded mjpeg thumbnail + telemetry data
streams), without needing the file.
"""



from composerv.index.probe import (
    MediaInfo,
    bit_depth_from_pix_fmt,
    build_media_info,
    find_camera_proxy,
    is_hdr_transfer,
    parse_capture_time,
)


def test_parse_capture_time_dji_filename():
    assert parse_capture_time("DJI_20251130074637_0004_D.MP4") == "2025-11-30T07:46:37"


def test_parse_capture_time_none_when_no_timestamp():
    assert parse_capture_time("IMG_random.mov") is None
    # 14 digits that aren't a valid datetime -> None, not a crash
    assert parse_capture_time("X_99999999999999_.mp4") is None


def test_bit_depth_from_pix_fmt():
    assert bit_depth_from_pix_fmt("yuv420p") == 8
    assert bit_depth_from_pix_fmt("yuvj420p") == 8
    assert bit_depth_from_pix_fmt("yuv420p10le") == 10
    assert bit_depth_from_pix_fmt("yuv422p12le") == 12


def test_is_hdr_transfer():
    assert is_hdr_transfer("bt709") is False
    assert is_hdr_transfer(None) is False
    assert is_hdr_transfer("smpte2084") is True  # PQ / HDR10
    assert is_hdr_transfer("arib-std-b67") is True  # HLG


# canned JSON modeled on the real DJI MP4 (hevc 10-bit + mjpeg thumbnail + data streams)
DJI_JSON = {
    "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "3.584000", "bit_rate": "25482883"},
    "streams": [
        {
            "codec_type": "video", "codec_name": "hevc", "profile": "Main 10",
            "width": 1920, "height": 1080, "r_frame_rate": "30000/1001",
            "avg_frame_rate": "30000/1001", "pix_fmt": "yuv420p10le",
            "color_transfer": "bt709", "disposition": {"attached_pic": 0},
        },
        {"codec_type": "audio", "codec_name": "aac", "sample_rate": "48000", "channels": 2},
        {"codec_type": "data", "codec_name": None},
        {
            "codec_type": "video", "codec_name": "mjpeg", "width": 1280, "height": 720,
            "r_frame_rate": "90000/1", "avg_frame_rate": "0/0", "pix_fmt": "yuvj420p",
            "disposition": {"attached_pic": 1},  # embedded thumbnail, must be ignored
        },
    ],
}


def test_build_media_info_picks_primary_video_not_thumbnail():
    mi = build_media_info("/x/DJI_20251130074637_0004_D.MP4", DJI_JSON)
    assert isinstance(mi, MediaInfo)
    assert mi.kind == "video"
    assert mi.codec == "hevc"           # not the mjpeg thumbnail
    assert (mi.width, mi.height) == (1920, 1080)
    assert mi.bit_depth == 10
    assert mi.is_hdr is False
    assert (mi.fps_num, mi.fps_den) == (30000, 1001)
    assert mi.is_vfr is False           # r == avg
    assert mi.has_audio is True
    assert mi.audio_sample_rate == 48000
    assert abs(mi.duration_s - 3.584) < 1e-6
    assert mi.capture_time == "2025-11-30T07:46:37"


def test_build_media_info_detects_vfr():
    j = {
        "format": {"duration": "5.0"},
        "streams": [{
            "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
            "r_frame_rate": "30000/1001", "avg_frame_rate": "27000/1001", "pix_fmt": "yuv420p",
            "disposition": {"attached_pic": 0},
        }],
    }
    mi = build_media_info("/x/clip.mov", j)
    assert mi.is_vfr is True
    assert mi.has_audio is False


def test_find_camera_proxy(tmp_path):
    mp4 = tmp_path / "DJI_0004.MP4"
    mp4.write_bytes(b"x")
    assert find_camera_proxy(str(mp4)) is None
    lrf = tmp_path / "DJI_0004.LRF"
    lrf.write_bytes(b"y")
    assert find_camera_proxy(str(mp4)) == str(lrf)


def test_probe_media_on_synthetic_clip(synth_clips):
    from composerv.index.probe import probe_media

    mi = probe_media(synth_clips["A"])
    assert mi.kind == "video"
    assert mi.codec == "h264"
    assert (mi.width, mi.height) == (1280, 720)
    assert mi.bit_depth == 8
    assert mi.has_audio is True
    assert mi.is_vfr is False
