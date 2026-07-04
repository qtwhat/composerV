"""Tests for the pure compile(Story) -> IntentionList function.

compile_story turns the human/AI-authored Story (a spine + ordered beats, each beat
optionally filled with a chosen Moment) into the lower-level IntentionList: the ordered
edit-decision list that the live preview engine and the FCPXML emitter both consume.
It is a pure function: same inputs -> same output, no I/O.
"""

from composerv.models import Beat, ControllingIdea, Moment, Story, Structure
from composerv.story.compiler import compile_story


def make_story(beats):
    return Story(
        id="s1",
        name="test story",
        controlling_idea=ControllingIdea(one_line="earning the view", target_feeling="pride"),
        structure=Structure(type="story_circle"),
        target_duration_s=60.0,
        beats=beats,
    )


def test_empty_story_compiles_to_empty_intention_list():
    il = compile_story(make_story([]), {})
    assert il.story_id == "s1"
    assert il.segments == []


def test_single_filled_beat_compiles_to_one_clip_segment():
    m = Moment(id="m1", source_clip_id="clipA", in_sec=12.0, out_sec=18.0)
    beat = Beat(
        id="b1",
        order=0,
        function="establish_ordinary",
        intent="set the scene",
        target_duration_s=4.0,
        chosen_moment="m1",
        why_moment="best establishing shot",
    )
    il = compile_story(make_story([beat]), {"m1": m})

    assert len(il.segments) == 1
    seg = il.segments[0]
    assert seg.kind == "clip"
    assert seg.source_id == "clipA"
    assert seg.in_sec == 12.0
    # moment is 6s but the beat's budget is 4s -> trim the tail to hit the budget
    assert seg.out_sec == 16.0
    assert seg.duration_s == 4.0
    assert seg.label == "establish_ordinary"
    assert seg.note == "best establishing shot"


def test_moment_shorter_than_budget_keeps_natural_duration():
    # a 2s moment under a 5s budget cannot invent footage -> keep its natural length
    m = Moment(id="m1", source_clip_id="clipA", in_sec=10.0, out_sec=12.0)
    beat = Beat(id="b1", order=0, function="breath", target_duration_s=5.0, chosen_moment="m1")
    il = compile_story(make_story([beat]), {"m1": m})

    seg = il.segments[0]
    assert seg.in_sec == 10.0
    assert seg.out_sec == 12.0
    assert seg.duration_s == 2.0


def test_empty_beat_compiles_to_a_visible_gap():
    beat = Beat(id="b1", order=0, function="low_point", target_duration_s=3.0, chosen_moment=None)
    il = compile_story(make_story([beat]), {})

    assert len(il.segments) == 1
    seg = il.segments[0]
    assert seg.kind == "gap"
    assert seg.source_id is None
    assert seg.duration_s == 3.0
    assert seg.label == "low_point"


def test_beats_are_emitted_in_order_field_not_list_order():
    m1 = Moment(id="m1", source_clip_id="A", in_sec=0.0, out_sec=2.0)
    m2 = Moment(id="m2", source_clip_id="B", in_sec=0.0, out_sec=2.0)
    # list is out of order; `order` field is authoritative
    b_second = Beat(id="b2", order=1, function="turn", target_duration_s=2.0, chosen_moment="m2")
    b_first = Beat(id="b1", order=0, function="call_to_go", target_duration_s=2.0, chosen_moment="m1")
    il = compile_story(make_story([b_second, b_first]), {"m1": m1, "m2": m2})

    assert [s.source_id for s in il.segments] == ["A", "B"]


def test_total_duration_sums_enabled_segments():
    m1 = Moment(id="m1", source_clip_id="A", in_sec=0.0, out_sec=10.0)
    m2 = Moment(id="m2", source_clip_id="B", in_sec=0.0, out_sec=10.0)
    b1 = Beat(id="b1", order=0, function="a", target_duration_s=3.0, chosen_moment="m1")
    b2 = Beat(id="b2", order=1, function="b", target_duration_s=2.0, chosen_moment="m2")
    il = compile_story(make_story([b1, b2]), {"m1": m1, "m2": m2})

    assert il.total_duration_s == 5.0


def test_missing_chosen_moment_id_raises():
    # a beat references a moment id that isn't in the provided map -> hard error,
    # never silently drop a beat the author put in the story
    beat = Beat(id="b1", order=0, function="turn", target_duration_s=2.0, chosen_moment="ghost")
    try:
        compile_story(make_story([beat]), {})
    except KeyError:
        return
    raise AssertionError("expected KeyError for unknown moment id")
