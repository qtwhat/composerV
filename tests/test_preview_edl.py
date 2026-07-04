"""Tests for the IntentionList -> preview-EDL adapter (pure)."""

import json

from composerv.models import AudioHighlight, IntentionList, MusicBed, Segment
from composerv.render.preview.edl import intention_to_edl, load_edl_file


def test_maps_clips_and_gaps_in_order():
    il = IntentionList(
        story_id="s",
        timeline_fps=30,
        segments=[
            Segment(kind="clip", source_id="c1", in_sec=1.0, out_sec=3.0, duration_s=2.0),
            Segment(kind="gap", duration_s=2.0, label="low_point"),
            Segment(kind="clip", source_id="c2", in_sec=0.0, out_sec=1.5, duration_s=1.5),
        ],
    )
    edl = intention_to_edl(il, {"c1": "/x/A.mp4", "c2": "/x/B.mp4"})
    assert edl["fps"] == 30
    assert edl["clips"] == [
        {"kind": "clip", "file": "/x/A.mp4", "in": 1.0, "out": 3.0},
        {"kind": "gap", "duration": 2.0},
        {"kind": "clip", "file": "/x/B.mp4", "in": 0.0, "out": 1.5},
    ]


def test_skips_disabled_segments():
    il = IntentionList(
        story_id="s",
        segments=[
            Segment(kind="clip", source_id="c1", in_sec=0.0, out_sec=1.0, duration_s=1.0, enabled=False),
            Segment(kind="clip", source_id="c2", in_sec=0.0, out_sec=1.0, duration_s=1.0),
        ],
    )
    edl = intention_to_edl(il, {"c1": "/x/A.mp4", "c2": "/x/B.mp4"})
    assert edl["clips"] == [{"kind": "clip", "file": "/x/B.mp4", "in": 0.0, "out": 1.0}]


def test_music_bed_flows_into_edl_with_defaults():
    il = IntentionList(
        story_id="s",
        segments=[Segment(kind="clip", source_id="c1", in_sec=0.0, out_sec=1.0, duration_s=1.0)],
        music=MusicBed(path="/m/song.mp3"),
    )
    edl = intention_to_edl(il, {"c1": "/x/A.mp4"})
    assert edl["music"] == {
        "file": "/m/song.mp3",
        "gain_db": 0.0,
        "duck_db": -15.0,
        "fade_out_s": 1.5,
    }


def test_no_music_means_no_music_key():
    il = IntentionList(
        story_id="s",
        segments=[Segment(kind="clip", source_id="c1", in_sec=0.0, out_sec=1.0, duration_s=1.0)],
    )
    assert "music" not in intention_to_edl(il, {"c1": "/x/A.mp4"})


def test_load_edl_file_round_trips_music(tmp_path):
    il = IntentionList(
        story_id="s",
        segments=[Segment(kind="clip", source_id="c1", in_sec=0.0, out_sec=1.0, duration_s=1.0)],
        music=MusicBed(path="/m/song.mp3", duck_db=-12.0),
    )
    p = tmp_path / "edl.json"
    p.write_text(json.dumps(intention_to_edl(il, {"c1": "/x/A.mp4"})))
    clips, fps, music, _title = load_edl_file(str(p))
    assert fps == 30 and len(clips) == 1
    assert music == {"file": "/m/song.mp3", "gain_db": 0.0, "duck_db": -12.0, "fade_out_s": 1.5}


def test_load_edl_file_without_music_returns_none(tmp_path):
    p = tmp_path / "edl.json"
    p.write_text(json.dumps({"fps": 24, "clips": []}))
    clips, fps, music, _title = load_edl_file(str(p))
    assert fps == 24 and clips == [] and music is None


def test_highlights_serialize_inside_the_music_block():
    il = IntentionList(
        story_id="s",
        segments=[Segment(kind="clip", source_id="c1", in_sec=0.0, out_sec=5.0, duration_s=5.0)],
        music=MusicBed(path="/m/song.mp3",
                       highlights=[AudioHighlight(start_s=1.0, end_s=2.0, ramp_s=0.2, label="child")]),
    )
    m = intention_to_edl(il, {"c1": "/x/A.mp4"})["music"]
    assert m["music_duck_db"] == -12.0 and m["highlight_db"] == 0.0
    assert m["highlights"] == [
        {"start": 1.0, "end": 2.0, "ramp": 0.2, "music_duck_db": None, "clip_db": None, "label": "child"}
    ]


def test_empty_highlights_keep_the_four_key_music_block():
    il = IntentionList(
        story_id="s",
        segments=[Segment(kind="clip", source_id="c1", in_sec=0.0, out_sec=1.0, duration_s=1.0)],
        music=MusicBed(path="/m/song.mp3"),
    )
    m = intention_to_edl(il, {"c1": "/x/A.mp4"})["music"]
    assert set(m.keys()) == {"file", "gain_db", "duck_db", "fade_out_s"}


def test_load_edl_round_trips_highlights(tmp_path):
    il = IntentionList(
        story_id="s",
        segments=[Segment(kind="clip", source_id="c1", in_sec=0.0, out_sec=5.0, duration_s=5.0)],
        music=MusicBed(path="/m/song.mp3", highlights=[AudioHighlight(start_s=1.0, end_s=2.0)]),
    )
    p = tmp_path / "edl.json"
    p.write_text(json.dumps(intention_to_edl(il, {"c1": "/x/A.mp4"})))
    _clips, _fps, music, _title = load_edl_file(str(p))
    assert music["highlights"][0]["start"] == 1.0 and music["highlights"][0]["end"] == 2.0


def test_unknown_source_id_raises():
    il = IntentionList(
        story_id="s",
        segments=[Segment(kind="clip", source_id="ghost", in_sec=0.0, out_sec=1.0, duration_s=1.0)],
    )
    try:
        intention_to_edl(il, {})
    except KeyError:
        return
    raise AssertionError("expected KeyError for unknown source id")
