"""Tests for the edit-decision storyboard (which clips, which time ranges were used)."""

from composerv.index.probe import MediaInfo
from composerv.models import IntentionList, Segment
from composerv.render.storyboard import EditShot, build_edit_shots, render_edit_storyboard
from composerv.store.db import Store


def test_render_storyboard_shows_keyframe_bg_and_green_used_range():
    shots = [
        EditShot(order=0, clip_id="A.mp4", keyframe="/k/a.jpg", clip_duration=34.0,
                 in_sec=12.0, out_sec=18.0, label="opening"),
        EditShot(order=1, clip_id="B.mp4", keyframe="/k/b.jpg", clip_duration=10.0,
                 in_sec=0.0, out_sec=5.0),
    ]
    html = render_edit_storyboard(shots, title="The Cut")
    assert "The Cut" in html
    assert "A.mp4" in html and "B.mp4" in html
    assert "/k/a.jpg" in html and "/k/b.jpg" in html         # keyframe backgrounds
    assert "left:35.3%" in html and "width:17.6%" in html     # 12/34 .. 6/34 highlighted
    assert "12.0" in html and "18.0" in html and "34" in html  # used range of full duration
    assert "opening" in html


def test_render_storyboard_marks_gaps():
    html = render_edit_storyboard([EditShot(order=0, is_gap=True, in_sec=0.0, out_sec=3.0, label="hole")])
    assert "gap" in html.lower() or "hole" in html


def test_render_storyboard_shows_when_label_and_event():
    shots = [EditShot(order=0, clip_id="A.mp4", clip_duration=10.0, in_sec=0.0, out_sec=5.0,
                      when="2026年1月1日 下午")]
    html = render_edit_storyboard(shots, title="cut", event="元旦武夷山之旅")
    assert "2026年1月1日 下午" in html      # per-shot review stamp
    assert "元旦武夷山之旅" in html          # event subtitle


def test_build_edit_shots_fills_when_from_capture_time(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/A.mp4", kind="video", duration_s=10.0,
                             capture_time="2026-01-01T17:24:57"), proxy_path="/p/a.mp4")
    il = IntentionList(story_id="s", segments=[
        Segment(kind="clip", source_id="/m/A.mp4", in_sec=0.0, out_sec=5.0, duration_s=5.0)])
    shots = build_edit_shots(il, s)
    assert shots[0].when == "2026年1月1日 傍晚"


def test_build_edit_shots_pulls_duration_and_nearest_keyframe(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/A.mp4", kind="video", duration_s=34.0), proxy_path="/p/a.mp4")
    s.set_keyframes("/m/A.mp4", [(0.0, "/k0.jpg"), (16.0, "/k1.jpg"), (30.0, "/k2.jpg")])
    il = IntentionList(story_id="s", segments=[
        Segment(kind="clip", source_id="/m/A.mp4", in_sec=12.0, out_sec=18.0, duration_s=6.0, label="open"),
        Segment(kind="gap", duration_s=2.0, label="breath"),
    ])
    shots = build_edit_shots(il, s)
    assert len(shots) == 2
    assert shots[0].clip_id == "A.mp4" and shots[0].clip_duration == 34.0
    assert shots[0].in_sec == 12.0 and shots[0].out_sec == 18.0
    assert shots[0].keyframe == "/k1.jpg"   # 16.0 is nearest to in=12.0
    assert shots[1].is_gap is True
