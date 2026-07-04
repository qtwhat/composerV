"""Tests for projecting detected source-clip-second windows onto timeline highlights (pure)."""

from composerv.models import IntentionList, Segment
from composerv.music.highlights import extend_for_speech, project_highlights

ONE_FRAME = 1 / 30 + 1e-6


def approx(x, y, tol=ONE_FRAME):
    return abs(x - y) <= tol


def _il(segments, fps=30):
    return IntentionList(story_id="s", timeline_fps=fps, segments=segments)


def test_projects_source_window_across_a_gap():
    il = _il([
        Segment(kind="clip", source_id="c1", in_sec=0.0, out_sec=2.0, duration_s=2.0),
        Segment(kind="gap", duration_s=1.0),
        Segment(kind="clip", source_id="c2", in_sec=0.0, out_sec=2.0, duration_s=2.0),
    ])
    hs = project_highlights(il, {"c2": [(0.5, 1.0, "child")]})
    assert len(hs) == 1
    assert approx(hs[0].start_s, 3.5) and approx(hs[0].end_s, 4.0)  # c2 sits at timeline 3..5
    assert hs[0].label == "child"
    assert hs[0].ramp_s == 0.40  # gentle default


def test_clip_used_twice_highlights_at_both_occurrences():
    il = _il([
        Segment(kind="clip", source_id="c1", in_sec=0.0, out_sec=2.0, duration_s=2.0),
        Segment(kind="clip", source_id="c1", in_sec=0.0, out_sec=2.0, duration_s=2.0),
    ])
    hs = project_highlights(il, {"c1": [(0.5, 1.0)]})
    assert len(hs) == 2
    assert approx(hs[0].start_s, 0.5) and approx(hs[1].start_s, 2.5)


def test_window_is_clamped_to_the_segment_trim():
    il = _il([Segment(kind="clip", source_id="c1", in_sec=1.0, out_sec=3.0, duration_s=2.0)])
    hs = project_highlights(il, {"c1": [(0.5, 2.0)]})  # 0.5 is before in=1.0 -> clipped to [1,2]
    assert len(hs) == 1
    assert approx(hs[0].start_s, 0.0) and approx(hs[0].end_s, 1.0)


def test_skirt_overlapping_windows_merge():
    il = _il([Segment(kind="clip", source_id="c1", in_sec=0.0, out_sec=10.0, duration_s=10.0)])
    hs = project_highlights(il, {"c1": [(1.0, 2.0), (2.1, 3.0)]}, default_ramp=0.25)
    assert len(hs) == 1  # skirts [0.75,2.25] and [1.85,3.25] overlap
    assert approx(hs[0].start_s, 1.0) and approx(hs[0].end_s, 3.0)


def test_far_apart_windows_do_not_merge():
    il = _il([Segment(kind="clip", source_id="c1", in_sec=0.0, out_sec=10.0, duration_s=10.0)])
    hs = project_highlights(il, {"c1": [(1.0, 2.0), (5.0, 6.0)]}, default_ramp=0.25)
    assert len(hs) == 2


def test_sub_two_frame_window_is_dropped():
    il = _il([Segment(kind="clip", source_id="c1", in_sec=0.0, out_sec=5.0, duration_s=5.0)])
    hs = project_highlights(il, {"c1": [(1.0, 1.0 + 1 / 30)]})  # 1 frame < 2-frame floor
    assert hs == []


def _clip(src, in_s, out_s):
    return Segment(kind="clip", source_id=src, in_sec=in_s, out_sec=out_s, duration_s=out_s - in_s)


def test_extend_out_to_finish_a_sentence_cut_at_the_end():
    il = IntentionList(story_id="m", segments=[_clip("c1", 2.0, 5.0)])
    out = extend_for_speech(il, {"c1": [(3.0, 7.0)]})  # shot ends at 5, sentence runs to 7
    s = out.segments[0]
    assert s.in_sec == 2.0 and s.out_sec == 7.0 and s.duration_s == 5.0


def test_pull_in_back_when_shot_starts_mid_sentence():
    il = IntentionList(story_id="m", segments=[_clip("c1", 4.0, 6.0)])
    out = extend_for_speech(il, {"c1": [(3.0, 7.0)]})  # sentence 3..7 straddles the shot
    s = out.segments[0]
    assert s.in_sec == 3.0 and s.out_sec == 7.0  # whole sentence contained


def test_sentence_already_inside_shot_is_untouched():
    il = IntentionList(story_id="m", segments=[_clip("c1", 1.0, 9.0)])
    out = extend_for_speech(il, {"c1": [(3.0, 5.0)]})
    assert out.segments[0].in_sec == 1.0 and out.segments[0].out_sec == 9.0


def test_non_overlapping_speech_leaves_shot_alone():
    il = IntentionList(story_id="m", segments=[_clip("c1", 2.0, 5.0)])
    out = extend_for_speech(il, {"c1": [(6.0, 8.0)]})  # speech entirely after the shot
    assert out.segments[0].out_sec == 5.0


def test_extend_respects_max_shot_cap():
    il = IntentionList(story_id="m", segments=[_clip("c1", 0.0, 3.0)])
    out = extend_for_speech(il, {"c1": [(1.0, 30.0)]}, max_shot_s=10.0)
    assert out.segments[0].out_sec == 10.0  # capped, not 30


def test_gap_segments_pass_through():
    il = IntentionList(story_id="m", segments=[Segment(kind="gap", duration_s=2.0)])
    out = extend_for_speech(il, {})
    assert out.segments[0].kind == "gap" and out.segments[0].duration_s == 2.0


def test_cursor_accumulates_in_whole_frames():
    # d1 = 1.005s -> 30.15 frames -> rounds to 30 -> seg2 starts at 1.0s, not 1.005s
    il = _il([
        Segment(kind="clip", source_id="c1", in_sec=0.0, out_sec=1.005, duration_s=1.005),
        Segment(kind="clip", source_id="c2", in_sec=0.0, out_sec=2.0, duration_s=2.0),
    ])
    hs = project_highlights(il, {"c2": [(0.0, 0.5)]})
    assert len(hs) == 1
    assert approx(hs[0].start_s, 1.0)  # frame-aligned, not 1.005
