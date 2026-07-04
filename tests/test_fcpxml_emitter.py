"""Tests for the FCPXML 1.13 emitter (IntentionList -> FCPXML string).

Structural + timecode correctness only. Real Final Cut Pro import compliance needs a
golden file exported from the user's FCP (tracked separately); these guard the shape.
"""

import os
import xml.etree.ElementTree as ET

import pytest

from composerv.models import AudioHighlight, IntentionList, MusicBed, Segment
from composerv.render.fcpxml.emitter import intention_to_fcpxml

_DTD = ("/Applications/Final Cut Pro.app/Contents/Frameworks/Interchange.framework/"
        "Versions/A/Resources/FCPXMLv1_13.dtd")


def _il():
    return IntentionList(story_id="s1", timeline_fps=30, segments=[
        Segment(kind="clip", source_id="A", in_sec=2.0, out_sec=5.0, duration_s=3.0,
                label="establish", note="why A"),
        Segment(kind="gap", duration_s=2.0, label="missing"),
        Segment(kind="clip", source_id="B", in_sec=0.0, out_sec=4.0, duration_s=4.0, label="peak"),
        Segment(kind="clip", source_id="A", in_sec=10.0, out_sec=12.0, duration_s=2.0, label="return"),
    ])


_PATHS = {"A": "/m/A.mov", "B": "/m/B.mov"}


def test_fcpxml_emits_photo_segment_as_video_only_asset():
    il = IntentionList(story_id="p", timeline_fps=30, segments=[
        Segment(kind="clip", source_id="A", in_sec=0.0, out_sec=3.0, duration_s=3.0),
        Segment(kind="photo", source_id="/m/p.jpg", in_sec=0.0, out_sec=4.0, duration_s=4.0, motion="in"),
    ])
    xml = intention_to_fcpxml(il, {"A": "/m/A.mov", "/m/p.jpg": "/m/p.jpg"})
    root = ET.fromstring(xml)                              # must be well-formed (no KeyError)
    srcs = [mr.get("src") for a in root.findall(".//asset") for mr in a.findall("media-rep")]
    assert "file:///m/p.jpg" in srcs                       # photo registered as an asset
    photo_asset = next(a for a in root.findall(".//asset")
                       if a.find("media-rep").get("src") == "file:///m/p.jpg")
    assert photo_asset.get("hasAudio") == "0"              # a still has no audio
    assert len(root.findall(".//spine/asset-clip")) == 2   # both clip and photo on the spine


def test_fcpxml_is_well_formed_and_versioned():
    xml = intention_to_fcpxml(_il(), _PATHS, project_name="P")
    assert xml.startswith("<?xml")
    assert 'version="1.13"' in xml
    root = ET.fromstring(xml)
    assert root.tag == "fcpxml"


def test_fcpxml_dedupes_assets_and_references_originals():
    root = ET.fromstring(intention_to_fcpxml(_il(), _PATHS))
    assets = root.findall(".//asset")
    assert len(assets) == 2  # source A is reused, not duplicated
    srcs = sorted(mr.get("src") for a in assets for mr in a.findall("media-rep"))
    assert srcs == ["file:///m/A.mov", "file:///m/B.mov"]


def test_fcpxml_spine_order_offsets_and_gap():
    root = ET.fromstring(intention_to_fcpxml(_il(), _PATHS))
    spine = root.find(".//spine")
    kids = list(spine)
    assert [k.tag for k in kids] == ["asset-clip", "gap", "asset-clip", "asset-clip"]
    # cumulative offsets along the spine: 0, 3, 5, 9 s at 30fps
    assert [k.get("offset") for k in kids] == ["0s", "90/30s", "150/30s", "270/30s"]
    # first clip: start=in (2s), duration=3s
    assert kids[0].get("start") == "60/30s"
    assert kids[0].get("duration") == "90/30s"
    # total sequence = 11s
    assert root.find(".//sequence").get("duration") == "330/30s"


def test_fcpxml_carries_beat_label_as_marker():
    root = ET.fromstring(intention_to_fcpxml(_il(), _PATHS))
    clip0 = root.find(".//spine/asset-clip")
    marker = clip0.find("marker")
    assert marker is not None and marker.get("value") == "establish"


def _il_music():
    il = _il()
    il.music = MusicBed(path="/m/song.mp3", duck_db=-15.0)
    return il


def test_music_bed_adds_an_audio_only_asset_referencing_the_track():
    root = ET.fromstring(intention_to_fcpxml(_il_music(), _PATHS))
    music = [a for a in root.findall(".//asset")
             if a.get("hasAudio") == "1" and a.get("hasVideo") == "0"]
    assert len(music) == 1
    assert music[0].find("media-rep").get("src") == "file:///m/song.mp3"


def test_music_bed_is_a_connected_clip_on_a_negative_lane():
    root = ET.fromstring(intention_to_fcpxml(_il_music(), _PATHS))
    music_id = next(a.get("id") for a in root.findall(".//asset")
                    if a.get("hasVideo") == "0")
    first = root.find(".//spine/asset-clip")
    lane = [c for c in root.findall(".//asset-clip")
            if c.get("lane") == "-1" and c.get("ref") == music_id]
    assert len(lane) == 1
    # anchored at the head of the first clip, spanning the whole 11s timeline
    assert lane[0].get("offset") == first.get("start")
    assert lane[0].get("duration") == "330/30s"


def test_music_bed_ducks_every_primary_video_clip():
    root = ET.fromstring(intention_to_fcpxml(_il_music(), _PATHS))
    primary = root.find(".//spine").findall("asset-clip")  # direct children only
    assert len(primary) == 3
    for c in primary:
        av = c.find("adjust-volume")
        assert av is not None and av.get("amount") == "-15dB"


def test_no_music_means_no_volume_adjust_or_audio_asset():
    root = ET.fromstring(intention_to_fcpxml(_il(), _PATHS))
    assert root.findall(".//adjust-volume") == []
    assert all(a.get("hasVideo") == "1" for a in root.findall(".//asset"))


def _il_dynamic():
    il = _il()  # spine timeline: A[0,3], gap[3,5], B[5,9], A[9,11]
    il.music = MusicBed(path="/m/song.mp3", duck_db=-15.0, music_duck_db=-18.0, highlight_db=0.0,
                        highlights=[AudioHighlight(start_s=5.5, end_s=6.5, label="child")])  # inside B
    return il


def test_static_fcpxml_foregrounds_the_overlapping_clip_audio():
    clips = ET.fromstring(intention_to_fcpxml(_il_dynamic(), _PATHS)).find(".//spine").findall("asset-clip")
    assert clips[1].find("adjust-volume").get("amount") == "0dB"    # B overlaps the window
    assert clips[0].find("adjust-volume").get("amount") == "-15dB"  # the others stay ducked
    assert clips[2].find("adjust-volume").get("amount") == "-15dB"


def test_static_fcpxml_ducks_the_music_deeper_when_highlights_present():
    root = ET.fromstring(intention_to_fcpxml(_il_dynamic(), _PATHS))
    music_clip = next(c for c in root.findall(".//asset-clip") if c.get("lane") == "-1")
    assert music_clip.find("adjust-volume").get("amount") == "-18dB"


@pytest.mark.skipif(not os.path.exists(_DTD), reason="FCPXML 1.13 DTD not on this machine")
def test_fcpxml_validates_against_the_fcp_dtd_with_music_and_labels():
    from lxml import etree
    il = _il()  # clips carry labels (markers)
    il.music = MusicBed(path="/m/song.mp3")
    doc = etree.fromstring(intention_to_fcpxml(il, _PATHS).encode())
    dtd = etree.DTD(_DTD)
    assert dtd.validate(doc), [e.message for e in dtd.error_log][:5]


def test_fcpxml_skips_disabled_segments():
    il = _il()
    il.segments[2].enabled = False  # disable the only B segment
    root = ET.fromstring(intention_to_fcpxml(il, _PATHS))
    assert root.find(".//asset[@id]") is not None
    assert "/m/B.mov" not in ET.tostring(root, encoding="unicode")
    assert [k.tag for k in root.find(".//spine")] == ["asset-clip", "gap", "asset-clip"]
