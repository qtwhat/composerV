"""Tests for assembling a montage whose cuts land on musical beats."""

from composerv.index.probe import MediaInfo
from composerv.music.montage import (
    assemble_to_beats,
    beats_per_cut_for_feeling,
    build_montage,
    salient_in_point,
    target_shot_s_for_feeling,
)
from composerv.store.db import Store


def test_assemble_cuts_on_beats_and_cycles_fragments():
    beats = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]   # 120 bpm (0.5s beats)
    frags = [("A", 0.0, 10.0), ("B", 0.0, 10.0)]   # (source_id, in_sec, available_sec)
    il = assemble_to_beats(frags, beats, beats_per_cut=2)  # a cut every 2 beats = every 1.0s
    assert len(il.segments) == 3                    # cuts at 0,1,2,3 -> 3 shots
    assert all(abs(s.duration_s - 1.0) < 1e-6 for s in il.segments)
    assert [s.source_id for s in il.segments] == ["A", "B", "A"]   # cycles fragments in order
    assert il.segments[0].in_sec == 0.0 and il.segments[0].out_sec == 1.0


def test_assemble_caps_at_max_shots():
    beats = [i * 0.5 for i in range(20)]            # 20 beats
    frags = [("A", 0.0, 10.0), ("B", 0.0, 10.0)]
    il = assemble_to_beats(frags, beats, beats_per_cut=1, max_shots=3)  # would be 19 without the cap
    assert len(il.segments) == 3


def test_assemble_unbounded_by_default():
    beats = [i * 0.5 for i in range(8)]             # 7 cuts -> 7 shots, cycling
    frags = [("A", 0.0, 10.0), ("B", 0.0, 10.0)]
    il = assemble_to_beats(frags, beats, beats_per_cut=1)  # no max_shots -> current behavior
    assert len(il.segments) == 7


def test_assemble_respects_fragment_available_length():
    beats = [0.0, 1.0, 2.0, 3.0]
    frags = [("A", 5.0, 0.6)]                        # only 0.6s available from in=5.0
    il = assemble_to_beats(frags, beats, beats_per_cut=1)  # 1.0s shots, but clamp to 0.6
    assert il.segments[0].in_sec == 5.0
    assert abs(il.segments[0].out_sec - 5.6) < 1e-6


def test_salient_in_point_picks_the_active_window():
    motion = [(float(t), 0.1) for t in range(10)]
    motion[6] = (6.0, 5.0)
    motion[7] = (7.0, 5.0)                       # activity clustered around t=6..7
    inp = salient_in_point(motion, shot_len=2.0, duration=10.0)
    assert 5.0 <= inp <= 7.0                     # window covers the active region, not the head


def test_salient_in_point_stays_in_bounds():
    inp = salient_in_point([(9.5, 9.0)], shot_len=3.0, duration=10.0)
    assert inp <= 7.0 + 1e-9                     # in + shot_len must fit the clip


def test_salient_in_point_no_signal_falls_back_to_head():
    assert salient_in_point([], 2.0, 10.0) == 0.0
    assert salient_in_point([(t, 1.0) for t in range(10)], 2.0, 10.0) == 0.0  # flat -> head


def test_build_montage_uses_salient_in_points(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=10.0), proxy_path="/p/a.mp4")
    s.set_clip_summary("/m/a.mp4", "x")
    s.upsert_asset(MediaInfo(path="/m/b.mp4", kind="video", duration_s=10.0), proxy_path="/p/b.mp4")
    s.set_clip_summary("/m/b.mp4", "y")
    d = tmp_path / "music" / "calm"; d.mkdir(parents=True); (d / "t.mp3").write_bytes(b"x")

    def motion_fn(proxy, dur):                   # activity late in the clip
        prof = [(float(t), 0.1) for t in range(10)]
        prof[7] = (7.0, 9.0)
        return prof

    [plan] = build_montage(
        s, ["/m/a.mp4", "/m/b.mp4"], music_dir=str(tmp_path / "music"), feeling="calm",
        motion_fn=motion_fn, beat_fn=lambda p: (120.0, [i * 0.5 for i in range(20)]),
    )
    assert plan.intention.segments[0].in_sec > 0  # starts at the active window, not the head


def test_build_montage_lets_speech_finish(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=10.0), proxy_path="/p/a.mp4")
    s.set_clip_summary("/m/a.mp4", "someone talking")
    d = tmp_path / "music" / "calm"; d.mkdir(parents=True); (d / "t.mp3").write_bytes(b"x")
    [plan] = build_montage(
        s, ["/m/a.mp4"], music_dir=str(tmp_path / "music"), feeling="calm", repeat=3,
        motion_fn=lambda p, dur: [],            # no motion signal
        vad_fn=lambda path: [(0.5, 6.0)],       # a 5.5s sentence
        beat_fn=lambda p: (120.0, [i * 0.5 for i in range(40)]),
    )
    seg = plan.intention.segments[0]
    assert seg.in_sec == 0.5            # opens on the sentence
    assert seg.out_sec >= 6.0           # and runs to its end (not chopped at the ~3s beat shot)
    # the same sentence window also ducks the music + foregrounds the voice
    assert plan.intention.music is not None and plan.intention.music.highlights


def test_build_montage_caps_shot_at_part_budget(tmp_path):
    # Safety: a shot can never exceed max_part_s (so a mis-judged long span can't break the
    # 5-minute split). VAD-only (no text) keeps the speech, bounded by the part budget.
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/talk.mp4", kind="video", duration_s=200.0), proxy_path="/p/talk.mp4")
    s.set_clip_summary("/m/talk.mp4", "long monologue")
    d = tmp_path / "music" / "calm"; d.mkdir(parents=True); (d / "t.mp3").write_bytes(b"x")
    [plan] = build_montage(
        s, ["/m/talk.mp4"], music_dir=str(tmp_path / "music"), feeling="calm",
        motion_fn=lambda p, dur: [],
        vad_fn=lambda path: [(0.0, 180.0)],     # one long no-text window
        beat_fn=lambda p: (120.0, [i * 0.5 for i in range(40)]),
        max_shot_s=8.0, max_part_s=20.0,
    )
    assert all(seg.duration_s <= 20.0 + 1e-6 for seg in plan.intention.segments)


def test_build_montage_keeps_worthy_conversation_whole(tmp_path):
    # A worthwhile conversation is kept COMPLETE (the whole span), past the 8s visual cap, and
    # ducks the music for its length.
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/talk.mp4", kind="video", duration_s=200.0), proxy_path="/p/talk.mp4")
    s.set_clip_summary("/m/talk.mp4", "a family chatting")
    d = tmp_path / "music" / "calm"; d.mkdir(parents=True); (d / "t.mp3").write_bytes(b"x")
    sentences = [(10.0, 12.0, "你回去会剪成一个 vlog 吗"), (13.0, 14.0, "我不知道"),
                 (24.0, 25.0, "还在学")]
    [plan] = build_montage(
        s, ["/m/talk.mp4"], music_dir=str(tmp_path / "music"), feeling="calm",
        motion_fn=lambda p, dur: [],
        vad_fn=lambda path: sentences,
        select_fn=lambda sents, **kw: sents,        # the whole exchange is worth keeping
        beat_fn=lambda p: (120.0, [i * 0.5 for i in range(40)]),
        max_shot_s=8.0, max_part_s=300.0,
    )
    seg = plan.intention.segments[0]
    assert seg.in_sec == 10.0                        # opens at the start of the exchange
    assert seg.out_sec == 25.0                       # kept WHOLE to its end (15s, past the 8s cap)
    assert plan.intention.music.highlights           # and ducks for it


def test_build_montage_skips_unworthy_talk_as_visual(tmp_path):
    # Talk the LLM judges NOT worth keeping -> a visual shot, music in front (no duck).
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/talk.mp4", kind="video", duration_s=20.0), proxy_path="/p/talk.mp4")
    s.set_clip_summary("/m/talk.mp4", "ambient chatter")
    d = tmp_path / "music" / "calm"; d.mkdir(parents=True); (d / "t.mp3").write_bytes(b"x")
    [plan] = build_montage(
        s, ["/m/talk.mp4"], music_dir=str(tmp_path / "music"), feeling="calm",
        motion_fn=lambda p, dur: [],
        vad_fn=lambda path: [(2.0, 3.0, "你在拍我吗")],
        select_fn=lambda sents, **kw: [],            # nothing worth keeping
        beat_fn=lambda p: (120.0, [i * 0.5 for i in range(40)]),
    )
    assert plan.intention.music.highlights == []     # no ducking; music stays in front


def test_build_montage_splits_into_parts_by_day(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=10.0,
                             capture_time="2026-01-01T15:00:00"), proxy_path="/p/a.mp4")
    s.set_clip_summary("/m/a.mp4", "x")
    s.upsert_asset(MediaInfo(path="/m/b.mp4", kind="video", duration_s=10.0,
                             capture_time="2026-01-02T10:00:00"), proxy_path="/p/b.mp4")
    s.set_clip_summary("/m/b.mp4", "y")
    d = tmp_path / "music" / "calm"; d.mkdir(parents=True); (d / "t.mp3").write_bytes(b"x")
    plans = build_montage(
        s, ["/m/a.mp4", "/m/b.mp4"], music_dir=str(tmp_path / "music"), feeling="calm",
        motion_fn=lambda p, dur: [], vad_fn=lambda path: [],
        beat_fn=lambda p: (120.0, [i * 0.5 for i in range(20)]),
    )
    assert len(plans) == 2                            # one part per day
    assert plans[0].label == "2026年1月1日" and plans[1].label == "2026年1月2日"


def test_build_montage_no_motion_keeps_head_in_point(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=10.0), proxy_path="/p/a.mp4")
    s.set_clip_summary("/m/a.mp4", "x")
    d = tmp_path / "music" / "calm"; d.mkdir(parents=True); (d / "t.mp3").write_bytes(b"x")
    [plan] = build_montage(  # default motion fn on a non-existent proxy -> no signal -> in=0
        s, ["/m/a.mp4"], music_dir=str(tmp_path / "music"), feeling="calm",
        beat_fn=lambda p: (120.0, [i * 0.5 for i in range(20)]),
    )
    assert plan.intention.segments[0].in_sec == 0.0


def test_target_shot_s_maps_to_feeling():
    assert target_shot_s_for_feeling("upbeat") < target_shot_s_for_feeling("calm")
    assert target_shot_s_for_feeling("calm") < target_shot_s_for_feeling("sad")
    assert target_shot_s_for_feeling("anything-unknown") > 0


def test_build_montage_paces_by_time_not_beat_count(tmp_path):
    # a DENSE beat grid (0.25s ~ octave-doubled tempo): fixed beats_per_cut would cut every ~1s;
    # time-based pacing must keep calm shots near the ~3s target.
    s = Store(str(tmp_path / "c.db"))
    for name in ("a", "b"):
        s.upsert_asset(MediaInfo(path=f"/m/{name}.mp4", kind="video", duration_s=30.0),
                       proxy_path=f"/p/{name}.mp4")
        s.set_clip_summary(f"/m/{name}.mp4", name)
    d = tmp_path / "music" / "calm"; d.mkdir(parents=True); (d / "t.mp3").write_bytes(b"x")
    [plan] = build_montage(
        s, ["/m/a.mp4", "/m/b.mp4"], music_dir=str(tmp_path / "music"), feeling="calm",
        repeat=3, beat_fn=lambda p: (240.0, [i * 0.25 for i in range(400)]),
    )
    durs = [seg.duration_s for seg in plan.intention.segments]
    assert durs and all(d >= 2.0 for d in durs)  # ~3s calm shots, not the 1s a fixed count would give


def test_feeling_maps_to_pacing():
    # upbeat cuts fast (few beats/cut), sad holds long (many beats/cut)
    assert beats_per_cut_for_feeling("upbeat") < beats_per_cut_for_feeling("calm")
    assert beats_per_cut_for_feeling("calm") < beats_per_cut_for_feeling("sad")
    assert beats_per_cut_for_feeling("anything-unknown") >= 1


def test_build_montage_ties_mood_track_and_beats(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=20.0), proxy_path="/p/a.mp4")
    s.set_clip_summary("/m/a.mp4", "a calm walk in a tea field")
    s.upsert_asset(MediaInfo(path="/m/b.mp4", kind="video", duration_s=20.0), proxy_path="/p/b.mp4")
    s.set_clip_summary("/m/b.mp4", "a quiet lake")
    d = tmp_path / "music" / "calm"
    d.mkdir(parents=True)
    (d / "t.mp3").write_bytes(b"x")

    [plan] = build_montage(
        s, ["/m/a.mp4", "/m/b.mp4"], music_dir=str(tmp_path / "music"),
        run=lambda p: "calm",                                   # mood inference
        beat_fn=lambda path: (120.0, [i * 0.5 for i in range(20)]),  # 120bpm, 20 beats
    )
    assert plan.feeling == "calm"
    assert plan.track and plan.track.endswith("t.mp3")
    assert plan.tempo == 120.0
    assert len(plan.intention.segments) > 0
    assert plan.intention.segments[0].source_id in ("/m/a.mp4", "/m/b.mp4")
    # the suggested track rides on the shared contract so preview + FCPXML both mux it
    assert plan.intention.music is not None
    assert plan.intention.music.path == plan.track


def test_build_montage_bounds_length_to_footage(tmp_path):
    # 2 clips but a long (20-beat) track: the reel must NOT cycle to fill the whole track.
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=20.0), proxy_path="/p/a.mp4")
    s.set_clip_summary("/m/a.mp4", "a calm walk")
    s.upsert_asset(MediaInfo(path="/m/b.mp4", kind="video", duration_s=20.0), proxy_path="/p/b.mp4")
    s.set_clip_summary("/m/b.mp4", "a quiet lake")
    d = tmp_path / "music" / "calm"
    d.mkdir(parents=True)
    (d / "t.mp3").write_bytes(b"x")
    [plan] = build_montage(
        s, ["/m/a.mp4", "/m/b.mp4"], music_dir=str(tmp_path / "music"), feeling="calm",
        beat_fn=lambda p: (120.0, [i * 0.5 for i in range(20)]),  # 20 beats -> would be 4+ shots
    )
    assert len(plan.intention.segments) == 2  # each clip once, not cycled to fill the track


def test_build_montage_repeat_factor(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=20.0), proxy_path="/p/a.mp4")
    s.set_clip_summary("/m/a.mp4", "x")
    s.upsert_asset(MediaInfo(path="/m/b.mp4", kind="video", duration_s=20.0), proxy_path="/p/b.mp4")
    s.set_clip_summary("/m/b.mp4", "y")
    d = tmp_path / "music" / "calm"; d.mkdir(parents=True); (d / "t.mp3").write_bytes(b"x")
    [plan] = build_montage(
        s, ["/m/a.mp4", "/m/b.mp4"], music_dir=str(tmp_path / "music"), feeling="calm",
        repeat=2, beat_fn=lambda p: (120.0, [i * 0.5 for i in range(40)]),  # enough beats for 4 shots
    )
    assert len(plan.intention.segments) == 4  # 2 clips x repeat 2


def test_build_montage_without_a_track_leaves_music_unset(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=20.0), proxy_path="/p/a.mp4")
    s.set_clip_summary("/m/a.mp4", "a calm walk")
    # empty music library -> suggest_track returns None -> no bed on the intention
    (tmp_path / "music").mkdir()
    [plan] = build_montage(
        s, ["/m/a.mp4"], music_dir=str(tmp_path / "music"),
        run=lambda p: "calm",
        beat_fn=lambda path: (120.0, [i * 0.5 for i in range(20)]),
    )
    assert plan.track is None
    assert plan.intention.music is None
