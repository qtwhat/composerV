"""Tests for the AVComposition builder: the headless-verifiable core of the live preview.

We can't drive the GUI in CI, but we CAN assert the composition is structurally correct:
its total duration, and that each clip segment references the right SOURCE time range
(the frame-accurate edit). Pixel-level "does the right frame show" is what the user
verifies visually with the burned-in counter clips.
"""

import pytest

avf = pytest.importorskip("AVFoundation")  # skip on non-macOS / no pyobjc

from composerv.render.preview.composition import (  # noqa: E402
    _db_to_linear,
    build_audio_mix,
    build_composition,
    composition_seconds,
    video_source_ranges,
)
from AVFoundation import AVMediaTypeAudio  # noqa: E402
from CoreMedia import CMTimeMakeWithSeconds  # noqa: E402

DUCK = _db_to_linear(-15.0)
HI = _db_to_linear(0.0)
GAIN = _db_to_linear(0.0)
MUS_LO = _db_to_linear(-18.0)


def _params_by_track(comp, mix):
    at = comp.tracksWithMediaType_(AVMediaTypeAudio)
    by_id = {p.trackID(): p for p in mix.inputParameters()}
    return by_id[at[0].trackID()], by_id[at[1].trackID()]  # clip, music


def _ramp_at(params, t):
    ok, sv, ev, _tr = params.getVolumeRampForTime_startVolume_endVolume_timeRange_(
        CMTimeMakeWithSeconds(t, 600), None, None, None)
    return ok, sv, ev

ONE_FRAME = 1 / 30 + 1e-3


def approx(x, y, tol=ONE_FRAME):
    return abs(x - y) <= tol


def test_two_clips_compose_to_summed_duration(synth_clips):
    edl = [
        {"kind": "clip", "file": synth_clips["A"], "in": 1.0, "out": 3.0},
        {"kind": "clip", "file": synth_clips["B"], "in": 0.0, "out": 1.5},
    ]
    comp = build_composition(edl, fps=30)
    assert approx(composition_seconds(comp), 3.5)


def test_clip_segments_reference_correct_source_ranges(synth_clips):
    edl = [
        {"kind": "clip", "file": synth_clips["A"], "in": 1.0, "out": 3.0},
        {"kind": "clip", "file": synth_clips["B"], "in": 0.5, "out": 2.0},
    ]
    comp = build_composition(edl, fps=30)
    ranges = video_source_ranges(comp)  # [(source_start_s, source_dur_s), ...]
    assert len(ranges) == 2
    assert approx(ranges[0][0], 1.0) and approx(ranges[0][1], 2.0)
    assert approx(ranges[1][0], 0.5) and approx(ranges[1][1], 1.5)


def test_db_to_linear():
    assert abs(_db_to_linear(0.0) - 1.0) < 1e-9
    assert abs(_db_to_linear(-6.0) - 0.501) < 1e-2  # ~half amplitude
    assert _db_to_linear(-15.0) < _db_to_linear(-6.0) < _db_to_linear(0.0)


def _music(file, **kw):
    return {"file": file, "gain_db": 0.0, "duck_db": -15.0, "fade_out_s": 1.0, **kw}


def test_music_adds_a_second_audio_track_without_extending_the_timeline(synth_clips):
    edl = [{"kind": "clip", "file": synth_clips["A"], "in": 0.0, "out": 2.0}]
    comp = build_composition(edl, fps=30, music=_music(synth_clips["B"]))  # B is 5s, trimmed to 2s
    assert len(comp.tracksWithMediaType_(AVMediaTypeAudio)) == 2  # clip audio + music bed
    assert approx(composition_seconds(comp), 2.0)  # music doesn't lengthen the video timeline


def test_no_music_keeps_one_audio_track(synth_clips):
    edl = [{"kind": "clip", "file": synth_clips["A"], "in": 0.0, "out": 2.0}]
    comp = build_composition(edl, fps=30)
    assert len(comp.tracksWithMediaType_(AVMediaTypeAudio)) == 1


def test_audio_mix_sets_params_for_clip_and_music_tracks(synth_clips):
    edl = [{"kind": "clip", "file": synth_clips["A"], "in": 0.0, "out": 2.0}]
    music = _music(synth_clips["B"])
    comp = build_composition(edl, fps=30, music=music)
    mix = build_audio_mix(comp, music, fps=30)
    assert mix is not None
    assert len(mix.inputParameters()) == 2


def test_no_music_means_no_mix(synth_clips):
    edl = [{"kind": "clip", "file": synth_clips["A"], "in": 0.0, "out": 2.0}]
    comp = build_composition(edl, fps=30)
    assert build_audio_mix(comp, None, fps=30) is None


def test_highlight_lifts_clip_and_ducks_music_with_edge_ramps(synth_clips):
    edl = [{"kind": "clip", "file": synth_clips["A"], "in": 0.0, "out": 6.0}]
    music = _music(synth_clips["B"], fade_out_s=1.0, music_duck_db=-18.0, highlight_db=0.0,
                   highlights=[{"start": 2.0, "end": 4.0, "ramp": 0.25,
                                "music_duck_db": None, "clip_db": None, "label": "child"}])
    comp = build_composition(edl, fps=30, music=music)
    clip_p, music_p = _params_by_track(comp, build_audio_mix(comp, music, fps=30))

    ok, sv, ev = _ramp_at(clip_p, 1.85)   # entry ramp [1.75, 2.0]: clip duck -> highlight
    assert ok and abs(sv - DUCK) < 1e-3 and abs(ev - HI) < 1e-3
    ok, sv, ev = _ramp_at(music_p, 1.85)  # music gain -> deep duck over the same edge
    assert ok and abs(sv - GAIN) < 1e-3 and abs(ev - MUS_LO) < 1e-3
    ok, sv, ev = _ramp_at(clip_p, 4.1)    # exit ramp [4.0, 4.25]: clip highlight -> duck
    assert ok and abs(sv - HI) < 1e-3 and abs(ev - DUCK) < 1e-3


def test_colliding_windows_do_not_crash_and_merge(synth_clips):
    edl = [{"kind": "clip", "file": synth_clips["A"], "in": 0.0, "out": 8.0}]
    music = _music(synth_clips["B"], fade_out_s=0.0, music_duck_db=-18.0, highlight_db=0.0,
                   highlights=[{"start": 2.0, "end": 3.0, "ramp": 0.25},
                               {"start": 3.1, "end": 4.0, "ramp": 0.25}])  # skirts collide
    comp = build_composition(edl, fps=30, music=music)
    mix = build_audio_mix(comp, music, fps=30)  # must NOT raise NSInvalidArgumentException
    assert mix is not None
    clip_p, _music_p = _params_by_track(comp, mix)
    _ok, sv, _ev = _ramp_at(clip_p, 3.0)   # mid merged window [2,4]: clip held at highlight
    assert abs(sv - HI) < 1e-3
    _ok, _sv, ev = _ramp_at(clip_p, 4.05)  # exit ramp ends back at duck
    assert abs(ev - DUCK) < 1e-3


def test_no_highlight_leaves_clip_track_a_flat_duck(synth_clips):
    edl = [{"kind": "clip", "file": synth_clips["A"], "in": 0.0, "out": 4.0}]
    music = _music(synth_clips["B"], fade_out_s=1.0)  # no highlights
    comp = build_composition(edl, fps=30, music=music)
    clip_p, _music_p = _params_by_track(comp, build_audio_mix(comp, music, fps=30))
    _ok, sv, ev = _ramp_at(clip_p, 2.0)  # constant duck, never lifted
    assert abs(sv - DUCK) < 1e-3 and abs(ev - DUCK) < 1e-3


def test_reel_ending_on_a_conversation_still_fades_out(synth_clips):
    # a worthy conversation runs to the very end: music AND voice must still fade to silence,
    # not cut dead (the "戛然而止" fix).
    edl = [{"kind": "clip", "file": synth_clips["A"], "in": 0.0, "out": 6.0}]
    music = _music(synth_clips["B"], fade_out_s=1.0, music_duck_db=-18.0, highlight_db=0.0,
                   highlights=[{"start": 2.0, "end": 6.0, "ramp": 0.25}])  # to the end
    comp = build_composition(edl, fps=30, music=music)
    clip_p, music_p = _params_by_track(comp, build_audio_mix(comp, music, fps=30))
    ok, _sv, ev = _ramp_at(music_p, 5.5)        # final fade [5,6] -> silence
    assert ok and abs(ev) < 1e-3
    ok, _sv, ev = _ramp_at(clip_p, 5.5)         # the voice fades too
    assert ok and abs(ev) < 1e-3


def test_clip_track_also_fades_at_the_very_end(synth_clips):
    edl = [{"kind": "clip", "file": synth_clips["A"], "in": 0.0, "out": 4.0}]
    music = _music(synth_clips["B"], fade_out_s=1.0)  # no highlights
    comp = build_composition(edl, fps=30, music=music)
    clip_p, music_p = _params_by_track(comp, build_audio_mix(comp, music, fps=30))
    ok, sv, ev = _ramp_at(clip_p, 3.5)          # [3,4] duck -> 0
    assert ok and abs(sv - DUCK) < 1e-3 and abs(ev) < 1e-3
    ok, sv, ev = _ramp_at(music_p, 3.5)         # [3,4] gain -> 0
    assert ok and abs(sv - GAIN) < 1e-3 and abs(ev) < 1e-3


def test_video_composition_fades_to_black_at_the_end(synth_clips):
    from composerv.render.preview.composition import build_video_composition
    from CoreMedia import CMTimeMakeWithSeconds

    edl = [{"kind": "clip", "file": synth_clips["A"], "in": 0.0, "out": 4.0}]
    comp = build_composition(edl, fps=30)
    vc = build_video_composition(comp, fps=30, tail_s=1.5)
    assert vc is not None
    assert vc.renderSize().width > 0 and vc.renderSize().height > 0
    insts = vc.instructions()
    assert len(insts) == 1
    layer = insts[0].layerInstructions()[0]
    ok, so, eo, _tr = layer.getOpacityRampForTime_startOpacity_endOpacity_timeRange_(
        CMTimeMakeWithSeconds(3.5, 600), None, None, None)   # in the fade [2.5, 4.0]
    assert ok and abs(so - 1.0) < 1e-3 and abs(eo) < 1e-3


def test_video_composition_burns_a_date_title_when_given(synth_clips):
    from composerv.render.preview.composition import build_video_composition
    edl = [{"kind": "clip", "file": synth_clips["A"], "in": 0.0, "out": 2.0}]
    comp = build_composition(edl, fps=30)
    assert build_video_composition(comp, fps=30).animationTool() is None        # no title -> no overlay
    vc = build_video_composition(comp, fps=30, title="2026年1月1日 下午")
    assert vc.animationTool() is not None                                        # title -> overlay tool


def test_gap_contributes_its_duration(synth_clips):
    edl = [
        {"kind": "clip", "file": synth_clips["A"], "in": 0.0, "out": 1.0},
        {"kind": "gap", "duration": 2.0},
        {"kind": "clip", "file": synth_clips["B"], "in": 0.0, "out": 1.0},
    ]
    comp = build_composition(edl, fps=30)
    assert approx(composition_seconds(comp), 4.0)
