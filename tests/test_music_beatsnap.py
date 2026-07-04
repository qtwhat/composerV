from composerv.models import IntentionList, MusicBed, Segment
from composerv.music.beatsnap import beat_snap_segments


def _clip(src, dur, in_s=0.0, duck=False):
    return Segment(kind="clip", source_id=src, in_sec=in_s, out_sec=in_s + dur,
                   duration_s=dur, duck=duck)


def test_no_beats_leaves_timeline_untouched():
    il = IntentionList(story_id="x", segments=[_clip("a", 2.0), _clip("b", 2.0)])
    out, snaps = beat_snap_segments(il, [], fps=30)
    assert [s.duration_s for s in out.segments] == [2.0, 2.0]
    assert [s.out_sec for s in out.segments] == [2.0, 2.0]
    assert snaps == []


def test_snaps_cut_to_nearest_beat_by_editing_out_sec():
    # beats every 0.5s; a first shot of 1.9s should snap its cut (t=1.9) to beat at 2.0
    beats = [i * 0.5 for i in range(12)]
    il = IntentionList(story_id="x", segments=[_clip("a", 1.9), _clip("b", 2.0)])
    out, snaps = beat_snap_segments(il, beats, fps=30)
    # the field the renderer reads is out_sec: in_sec(0.0) + new_dur(~2.0)
    assert abs(out.segments[0].out_sec - 2.0) < 1e-6
    assert abs(out.segments[0].duration_s - 2.0) < 1e-6
    assert snaps and snaps[0][1] == 2.0


def test_snap_clamps_out_sec_to_source_duration():
    # the source clip is only 2.0s long; a snap target past 2.0 must NOT overrun the source
    beats = [0.0, 2.4, 5.0]
    il = IntentionList(story_id="x", segments=[_clip("a", 1.9, in_s=0.0)])
    out, snaps = beat_snap_segments(il, beats, fps=30, max_drift_s=0.6,
                                    asset_durations={"a": 2.0})
    assert out.segments[0].out_sec <= 2.0 + 1e-6  # clamped to source length
    # since the beat at 2.4 would require out_sec=2.4 > source 2.0, the snap is rejected
    assert out.segments[0].out_sec == 1.9
    assert snaps == []


def test_photo_and_gap_segments_are_skipped():
    photo = Segment(kind="photo", source_id="p", in_sec=0.0, out_sec=3.0, duration_s=3.0)
    gap = Segment(kind="gap", duration_s=1.0)
    beats = [i * 0.5 for i in range(12)]
    il = IntentionList(story_id="x", segments=[photo, gap, _clip("a", 1.9)])
    out, snaps = beat_snap_segments(il, beats, fps=30)
    assert out.segments[0].out_sec == 3.0  # photo untouched
    assert out.segments[1].duration_s == 1.0  # gap untouched


def test_drift_cap_prevents_large_jumps():
    beats = [0.0, 5.0, 10.0]
    il = IntentionList(story_id="x", segments=[_clip("a", 2.0), _clip("b", 2.0)])
    out, snaps = beat_snap_segments(il, beats, fps=30, max_drift_s=0.5)
    assert out.segments[0].out_sec == 2.0
    assert snaps == []


def test_duck_window_realigns_to_segment_after_upstream_snap():
    # seg0 (1.9s, snaps to 2.0) then seg1 (2.0s, ducked). After snapping, the duck window must
    # cover seg1's NEW timeline span [2.0, 4.0], not the stale pre-snap [1.9, 3.9].
    beats = [i * 0.5 for i in range(12)]
    il = IntentionList(story_id="x",
                       segments=[_clip("a", 1.9), _clip("b", 2.0, duck=True)])
    il.music = MusicBed(path="/m/x.mp3")
    out, snaps = beat_snap_segments(il, beats, fps=30)
    assert out.music is not None and out.music.highlights
    h = out.music.highlights[0]
    assert abs(h.start_s - 2.0) < 1e-6 and abs(h.end_s - 4.0) < 1e-6
