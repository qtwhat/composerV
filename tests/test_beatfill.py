"""Tests for story.beatfill: bind a chosen story-line's beats to real clips+ranges, then
build a Story that compiles to an IntentionList. Pure prompt/parse + mapping tested here.
"""

from composerv.index.probe import MediaInfo
from composerv.story.beatfill import (
    BoundBeat,
    build_fill_prompt,
    fill_story,
    parse_beats,
)
from composerv.story.brief import build_archive_brief
from composerv.story.compiler import compile_story
from composerv.story.storylines import StorylineCandidate
from composerv.store.db import Store


def test_build_fill_prompt_includes_storyline_and_brief():
    sl = StorylineCandidate(title="Back on Her Feet", logline="from vulnerability to a run",
                            target_feeling="tender", structure="person_portrait")
    p = build_fill_prompt(sl, "BRIEF_TEXT")
    assert "Back on Her Feet" in p and "BRIEF_TEXT" in p
    assert "in" in p.lower() and "out" in p.lower()  # asks for in/out
    assert "json" in p.lower()


def test_parse_beats_array():
    text = ('[{"function":"establish_ordinary","intent":"calm","clip":"/m/a.mp4","in":1.0,"out":4.0,"why":"sets tone"},'
            '{"function":"low_point","intent":"the storm","clip":"/m/b.mp4","in":0.0,"out":3.0,"why":"turn"}]')
    beats = parse_beats(text)
    assert len(beats) == 2
    assert beats[0].function == "establish_ordinary"
    assert beats[0].clip == "/m/a.mp4"
    assert beats[0].in_sec == 1.0 and beats[0].out_sec == 4.0


def test_parse_beats_tolerates_garbage():
    assert parse_beats("nope") == []


def test_fill_story_builds_compilable_story(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=6.0), proxy_path="/px/a.mp4")
    s.upsert_asset(MediaInfo(path="/m/b.mp4", kind="video", duration_s=6.0), proxy_path="/px/b.mp4")
    sl = StorylineCandidate(title="T", logline="L", target_feeling="tender", structure="person_portrait")
    canned = ('[{"function":"establish_ordinary","intent":"calm","clip":"/m/a.mp4","in":1.0,"out":4.0,"why":"x"},'
              '{"function":"return_changed","intent":"freedom","clip":"/m/b.mp4","in":0.0,"out":2.0,"why":"y"}]')

    story, moments, source_paths = fill_story(sl, build_archive_brief(s), s, run=lambda p: canned)

    assert story.controlling_idea.one_line  # spine carried from the storyline
    assert story.structure.type == "person_portrait"
    assert len(story.beats) == 2
    # compiles to a 2-segment IntentionList referencing the right source ranges
    il = compile_story(story, moments)
    assert [seg.source_id for seg in il.segments] == ["/m/a.mp4", "/m/b.mp4"]
    assert il.segments[0].in_sec == 1.0 and il.segments[0].out_sec == 4.0
    # source_paths maps source -> proxy for the preview EDL
    assert source_paths["/m/a.mp4"] == "/px/a.mp4"
