"""Tests for story.brief: build an Archive Brief from the indexed store.

The brief is the compact, text-summarizable representation an LLM reasons over to propose
story angles (it never sees raw video). Pure over store data, so it's unit-testable.
"""

from composerv.analyze.base import CaptionResult
from composerv.index.frames import FrameRef
from composerv.index.probe import MediaInfo
from composerv.story.brief import build_archive_brief
from composerv.store.db import Store


def _seed(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/sunset.mp4", kind="video", duration_s=6.0,
                             capture_time="2025-11-30T18:00:00"))
    s.upsert_asset(MediaInfo(path="/m/kids.mp4", kind="video", duration_s=10.0,
                             capture_time="2025-12-24T09:30:00"))
    s.replace_captions("/m/sunset.mp4", "fake", [
        (FrameRef(video_path="p", index=0, src_pts_s=0.0, image_path="0.jpg"),
         CaptionResult(caption="wide beach at dusk", shot_type="wide", objects=["beach", "sky"], salience=0.4)),
        (FrameRef(video_path="p", index=1, src_pts_s=1.0, image_path="1.jpg"),
         CaptionResult(caption="the sun dips into the sea", shot_type="wide", objects=["sun", "sea"], salience=0.9)),
    ])
    s.replace_captions("/m/kids.mp4", "fake", [
        (FrameRef(video_path="p", index=0, src_pts_s=0.0, image_path="0.jpg"),
         CaptionResult(caption="two kids open presents", shot_type="medium", objects=["kids", "presents"], salience=0.8)),
    ])
    return s


def test_build_archive_brief_aggregates_clips(tmp_path):
    brief = build_archive_brief(_seed(tmp_path))
    assert brief.n_clips == 2
    assert brief.date_start == "2025-11-30T18:00:00"
    assert brief.date_end == "2025-12-24T09:30:00"

    by_name = {c.name: c for c in brief.clips}
    sunset = by_name["sunset.mp4"]
    assert sunset.duration_s == 6.0
    assert set(sunset.objects) == {"beach", "sky", "sun", "sea"}  # union across frames
    assert sunset.key_caption == "the sun dips into the sea"  # highest salience
    assert sunset.captions == ["wide beach at dusk", "the sun dips into the sea"]


def test_brief_includes_clip_summary(tmp_path):
    s = _seed(tmp_path)
    s.set_clip_summary("/m/sunset.mp4", "the sun sets over the sea in one continuous shot")
    brief = build_archive_brief(s)
    sunset = {c.name: c for c in brief.clips}["sunset.mp4"]
    assert sunset.summary == "the sun sets over the sea in one continuous shot"
    assert "the sun sets over the sea" in brief.to_prompt_text()


def test_brief_to_prompt_text_lists_clips(tmp_path):
    text = build_archive_brief(_seed(tmp_path)).to_prompt_text()
    assert "sunset.mp4" in text and "kids.mp4" in text
    assert "the sun dips into the sea" in text
    assert "2 clip" in text  # a global header mentioning the count


def test_brief_includes_named_people(tmp_path):
    from composerv.index.probe import MediaInfo
    from composerv.store.db import Store
    from composerv.story.brief import build_archive_brief

    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/DJI_A.MP4", kind="video", duration_s=10.0,
                             capture_time="2025-12-14T17:00:00"))
    s.set_clip_summary("/m/DJI_A.MP4", "kids walk a path")
    s.replace_faces("/m/DJI_A.MP4", [(1.0, [0, 0, 1, 1], [1, 0])])
    f = s.get_faces("/m/DJI_A.MP4")[0]
    s.upsert_person(0)
    s.set_face_person(f.face_id, 0)
    s.set_person_name(0, "哥哥")

    brief = build_archive_brief(s)
    assert brief.clips[0].people == ["哥哥"]
    assert "people: 哥哥" in brief.to_prompt_text()
