"""Perception ingest: run VLM + Whisper once per clip and cache into the store."""

from composerv.clarity.analyze import analyze_clip, analyze_scope
from composerv.index.probe import MediaInfo
from composerv.store.db import Store


def test_analyze_clip_caches_visual_and_transcript(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=20.0), proxy_path="/p/a.mp4")
    nv, ns = analyze_clip(
        s, "/m/a.mp4",
        visual_fn=lambda p, d: [(0.0, "围着炉子"), (13.2, "特写食物")],
        speech_fn=lambda p: [(11.3, 14.1, "这是芋头")])
    assert (nv, ns) == (2, 1)
    assert s.get_clip_moments("/m/a.mp4") == [(0.0, "围着炉子"), (13.2, "特写食物")]
    assert s.get_transcript("/m/a.mp4") == [(11.3, 14.1, "这是芋头")]


def test_analyze_clip_stores_vad_only_speech_without_text(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=10.0), proxy_path="/p/a.mp4")
    analyze_clip(s, "/m/a.mp4", visual_fn=lambda p, d: [], speech_fn=lambda p: [(1.0, 2.0)])
    assert s.get_transcript("/m/a.mp4") == [(1.0, 2.0, "")]


def test_analyze_clip_caches_grounding_and_ocr(tmp_path):
    from composerv.analyze.clip_video import GroundedObject

    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=20.0), proxy_path="/p/a.mp4")
    nv, ns = analyze_clip(
        s, "/m/a.mp4",
        visual_fn=lambda p, d: [
            (0.0, "入画", "武夷山", [GroundedObject(label="person", box=[0.1, 0.2, 0.3, 0.9])]),
            (8.0, "比耶"),  # ungrounded frame: stored with empty ocr/objects
        ],
        speech_fn=lambda p: [])
    assert nv == 2
    rich = s.get_clip_moments_rich("/m/a.mp4")
    assert rich[0].ocr == "武夷山" and rich[0].objects[0].label == "person"
    assert rich[1].ocr == "" and rich[1].objects == []
    # the plain (t, text) getter still works for callers that don't need boxes
    assert s.get_clip_moments("/m/a.mp4") == [(0.0, "入画"), (8.0, "比耶")]


def test_analyze_clip_photo_uses_photo_visual_no_transcript(tmp_path):
    from composerv.analyze.clip_video import GroundedObject

    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/p.jpg", kind="photo", duration_s=0.0, width=400, height=300),
                   proxy_path="/p/p.jpg")
    seen = {}

    def photo_fn(p, d):
        seen["called"] = (p, d)
        return [(0.0, "全家合影", "武夷山", [GroundedObject(label="person", box=[0.1, 0.2, 0.3, 0.9])])]

    def speech_fn(p):
        raise AssertionError("a photo has no audio — speech_fn must not be called")

    nv, ns = analyze_clip(s, "/m/p.jpg", visual_fn=photo_fn, speech_fn=speech_fn)
    assert (nv, ns) == (1, 0)                          # one moment, no transcript
    rich = s.get_clip_moments_rich("/m/p.jpg")
    assert rich[0].text == "全家合影" and rich[0].ocr == "武夷山"
    assert rich[0].objects[0].label == "person"
    assert s.get_transcript("/m/p.jpg") == []


def test_default_visual_logs_and_returns_empty_on_failure(tmp_path, capsys, monkeypatch):
    from composerv.clarity import analyze as A

    clip = tmp_path / "real.mp4"
    clip.write_bytes(b"x")  # exists, so we reach the model call (which we make throw)

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    monkeypatch.setattr("composerv.clarity.understand.understand_clip_perframe", boom)
    assert A._default_visual(str(clip), 4.0) == []          # degrades, no crash
    err = capsys.readouterr().err
    assert "kaboom" in err and str(clip) in err             # but the failure is logged, not silent


def test_analyze_scope_reports_each_clip(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    for n in ("a", "b"):
        s.upsert_asset(MediaInfo(path=f"/m/{n}.mp4", kind="video", duration_s=10.0),
                       proxy_path=f"/p/{n}.mp4")
    res = analyze_scope(s, ["/m/a.mp4", "/m/b.mp4"],
                        visual_fn=lambda p, d: [(0.0, "x")], speech_fn=lambda p: [])
    assert res == [("/m/a.mp4", 1, 0), ("/m/b.mp4", 1, 0)]


def test_analyze_clip_stores_aesthetics_from_injected_scores(tmp_path):
    from composerv.clarity.analyze import analyze_clip
    from composerv.store.db import Store
    from composerv.index.probe import MediaInfo

    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=10.0), proxy_path="/p/a.mp4")

    def fake_aes(proxy, dur, *, aes_fps=2.0):
        assert dur == 10.0
        return (3.0, [(0.0, -0.4, True), (3.0, 0.9, False)])

    analyze_clip(s, "/m/a.mp4",
                 visual_fn=lambda p, d: [(0.0, "x"), (3.0, "y")],
                 speech_fn=lambda p: [],
                 aesthetics_fn=fake_aes)
    got = s.get_clip_aesthetics("/m/a.mp4")
    assert got is not None and got.best_t == 3.0
    assert got.curve == [(0.0, -0.4, True), (3.0, 0.9, False)]


def test_analyze_clip_skips_aesthetics_when_disabled(tmp_path):
    from composerv.clarity.analyze import analyze_clip
    from composerv.store.db import Store
    from composerv.index.probe import MediaInfo

    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=10.0), proxy_path="/p/a.mp4")
    analyze_clip(s, "/m/a.mp4", visual_fn=lambda p, d: [(0.0, "x")], speech_fn=lambda p: [],
                 enable_aesthetics=False)
    assert s.get_clip_aesthetics("/m/a.mp4") is None


def test_analyze_aesthetics_builds_curve_and_cleans_temp_dir(tmp_path, monkeypatch):
    import os
    from composerv.analyze import aesthetics as aes
    from composerv.index.frames import FrameRef

    proxy = tmp_path / "p.mp4"
    proxy.write_bytes(b"x")  # must exist to pass the guard

    seen = {}

    def fake_sample(path, out_dir, *, fps):
        seen["dir"] = out_dir  # capture the temp dir analyze_aesthetics created
        return [FrameRef(video_path=path, index=0, src_pts_s=0.0, image_path=f"{out_dir}/f0.jpg"),
                FrameRef(video_path=path, index=1, src_pts_s=1.0, image_path=f"{out_dir}/f1.jpg")]

    monkeypatch.setattr("composerv.index.frames.sample_frames", fake_sample)

    def fake_score(paths):
        return {p: (0.5 if i else -0.5, False) for i, p in enumerate(paths)}

    best_t, curve = aes.analyze_aesthetics(str(proxy), 5.0, score_fn=fake_score)
    assert [c[0] for c in curve] == [0.0, 1.0]      # sorted by time
    assert best_t == 1.0                             # the 0.5-scored frame at t=1.0
    assert not os.path.exists(seen["dir"])           # the auto temp dir was cleaned up


def test_analyze_scope_threads_aesthetics_flags(tmp_path):
    from composerv.clarity.analyze import analyze_scope
    from composerv.store.db import Store
    from composerv.index.probe import MediaInfo

    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=10.0), proxy_path="/p/a.mp4")
    seen = {}

    def fake_aes(proxy, dur, *, aes_fps=2.0):
        seen["fps"] = aes_fps
        return (1.0, [(1.0, 0.5, False)])

    analyze_scope(s, ["/m/a.mp4"], visual_fn=lambda p, d: [(0.0, "x")], speech_fn=lambda p: [],
                  aesthetics_fn=fake_aes, aes_fps=4.0)
    assert seen["fps"] == 4.0
    assert s.get_clip_aesthetics("/m/a.mp4").best_t == 1.0
