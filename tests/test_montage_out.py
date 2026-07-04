"""Scope resolution + output writing for the `composerv montage` command."""

import json
import os

from typer.testing import CliRunner

from composerv.cli.main import app
from composerv.index.probe import MediaInfo
from composerv.models import IntentionList, MusicBed, Segment
from composerv.music.montage import MontagePlan
from composerv.render.montage_out import resolve_scope, write_montage_outputs
from composerv.store.db import Store

runner = CliRunner()


def _store(tmp_path):
    s = Store(str(tmp_path / "c.db"))
    s.upsert_asset(MediaInfo(path="/m/a.mp4", kind="video", duration_s=10.0,
                             capture_time="2026-01-01T10:00:00"), proxy_path="/p/a.mp4")
    s.upsert_asset(MediaInfo(path="/m/b.mp4", kind="video", duration_s=10.0,
                             capture_time="2026-01-02T10:00:00"), proxy_path="/p/b.mp4")
    s.upsert_asset(MediaInfo(path="/m/c.mp4", kind="video", duration_s=10.0,
                             capture_time="2026-01-01T12:00:00"))  # no proxy
    return s


def test_resolve_scope_selected(tmp_path):
    s = _store(tmp_path)
    s.set_selected("/m/b.mp4", True)
    assert resolve_scope(s, "selected") == ["/m/b.mp4"]


def test_resolve_scope_all_requires_proxy(tmp_path):
    s = _store(tmp_path)
    assert set(resolve_scope(s, "all")) == {"/m/a.mp4", "/m/b.mp4"}  # c has no proxy


def test_resolve_scope_by_date_prefix(tmp_path):
    s = _store(tmp_path)
    assert set(resolve_scope(s, "2026-01-01")) == {"/m/a.mp4", "/m/c.mp4"}


def test_write_montage_outputs(tmp_path):
    s = _store(tmp_path)
    il = IntentionList(story_id="m", timeline_fps=30, music=MusicBed(path="/music/t.mp3"), segments=[
        Segment(kind="clip", source_id="/m/a.mp4", in_sec=1.0, out_sec=3.0, duration_s=2.0),
    ])
    plan = MontagePlan(feeling="calm", track="/music/t.mp3", tempo=120.0, intention=il)
    out = write_montage_outputs(plan, s, str(tmp_path / "reel"))
    assert all(os.path.exists(p) for p in out.values())
    edl = json.load(open(out["edl"]))
    assert edl["clips"][0]["file"] == "/p/a.mp4"          # EDL references the PROXY
    assert "/m/a.mp4" in open(out["fcpxml"]).read()       # FCPXML references the ORIGINAL


def test_write_montage_outputs_routes_by_type_and_date(tmp_path):
    s = _store(tmp_path)
    il = IntentionList(story_id="m", timeline_fps=30, music=MusicBed(path="/music/t.mp3"), segments=[
        Segment(kind="clip", source_id="/m/a.mp4", in_sec=1.0, out_sec=3.0, duration_s=2.0),
    ])
    plan = MontagePlan(feeling="calm", track="/music/t.mp3", tempo=120.0, intention=il)
    out = write_montage_outputs(plan, s, "元旦", base=str(tmp_path / "out"), date="2026-06-22")
    assert out["edl"].endswith(os.path.join("out", "edl", "2026-06-22", "元旦.edl.json"))
    assert out["fcpxml"].endswith(os.path.join("out", "fcpxml", "2026-06-22", "元旦.fcpxml"))
    assert out["storyboard"].endswith(os.path.join("out", "storyboard", "2026-06-22", "元旦_storyboard.html"))
    assert all(os.path.exists(p) for p in out.values())


def test_write_montage_outputs_handles_photo_segment(tmp_path):
    s = _store(tmp_path)
    s.upsert_asset(MediaInfo(path="/m/p.jpg", kind="photo", duration_s=0.0, width=4000, height=3000,
                             capture_time="2026-01-01T10:00:00"), proxy_path="/p/p.jpg")
    il = IntentionList(story_id="m", timeline_fps=30, music=MusicBed(path="/music/t.mp3"), segments=[
        Segment(kind="clip", source_id="/m/a.mp4", in_sec=1.0, out_sec=3.0, duration_s=2.0),
        Segment(kind="photo", source_id="/m/p.jpg", in_sec=0.0, out_sec=4.0, duration_s=4.0, motion="in"),
    ])
    plan = MontagePlan(feeling="calm", track="/music/t.mp3", tempo=120.0, intention=il)
    out = write_montage_outputs(plan, s, str(tmp_path / "reel"))  # must not KeyError on the photo
    assert all(os.path.exists(p) for p in out.values())
    edl = json.load(open(out["edl"]))
    assert "/p/p.jpg" in [c.get("file") for c in edl["clips"]]   # photo proxy in the EDL
    assert "/m/p.jpg" in open(out["fcpxml"]).read()              # photo original in the FCPXML


def test_write_montage_outputs_writes_rationale_sidecar(tmp_path):
    """write_montage_outputs must produce a *.music-rationale.json next to the EDL (spec §13.3)."""
    from composerv.models import MusicIntent

    s = _store(tmp_path)
    il = IntentionList(story_id="m", timeline_fps=30, music=MusicBed(path="/music/t.mp3"), segments=[
        Segment(kind="clip", source_id="/m/a.mp4", in_sec=1.0, out_sec=3.0, duration_s=2.0),
    ])
    plan = MontagePlan(
        feeling="upbeat", track="/music/t.mp3", tempo=130.0, label="day1",
        intention=il,
        intent=MusicIntent(energy_curve=[0.6] * 16, arc_text="build to peak"),
        match_score=0.91,
        match_breakdown={"shape": 0.95, "tempo": 1.0, "mode": 1.0, "valence": 0.8, "duration": 1.0},
        beat_snaps=[(2.0, 2.05, 4)],
        library_gap=False,
    )
    out = write_montage_outputs(plan, s, str(tmp_path / "reel"))

    # The sidecar must be returned in the output dict and exist on disk.
    assert "rationale" in out, "write_montage_outputs did not return a 'rationale' key"
    sidecar_path = out["rationale"]
    assert os.path.exists(sidecar_path), f"rationale sidecar not found at {sidecar_path}"

    # The sidecar must sit next to the EDL with the clean stem (no .edl suffix).
    edl_path = out["edl"]
    expected_stem = edl_path[: -len(".edl.json")] if edl_path.endswith(".edl.json") else os.path.splitext(edl_path)[0]
    assert sidecar_path == expected_stem + ".music-rationale.json"

    # The sidecar must contain the chosen track and match_score.
    import json as _json
    data = _json.loads(open(sidecar_path).read())
    assert data["track"] == "/music/t.mp3"
    assert data["match_score"] == 0.91
    assert data["match_breakdown"]["shape"] == 0.95
    assert data["library_gap"] is False


def test_write_montage_outputs_rationale_sidecar_via_monkeypatch(tmp_path, monkeypatch):
    """write_rationale_sidecar is called with the derived reel path even when the function itself
    would fail (e.g. music sidecar missing).  Use monkeypatch to assert the call and its argument
    without depending on the music-features sidecar infrastructure."""
    s = _store(tmp_path)
    il = IntentionList(story_id="m", timeline_fps=30, music=MusicBed(path="/music/t.mp3"), segments=[
        Segment(kind="clip", source_id="/m/a.mp4", in_sec=1.0, out_sec=3.0, duration_s=2.0),
    ])
    plan = MontagePlan(feeling="calm", track="/music/t.mp3", tempo=120.0, intention=il)

    calls: list[tuple] = []

    def fake_write_rationale(p, reel_path: str) -> str:
        calls.append((p, reel_path))
        # Write a real file so paths["rationale"] is set.
        import json as _json
        out = reel_path.replace(".mp4", ".music-rationale.json")
        with open(out, "w") as fh:
            fh.write(_json.dumps({}))
        return out

    import composerv.render.montage_out as _mo
    monkeypatch.setattr(_mo, "_write_rationale_sidecar_fn", fake_write_rationale, raising=False)

    # Patch at the module level where it is imported inside the function.
    import composerv.music.rationale as _rat
    monkeypatch.setattr(_rat, "write_rationale_sidecar", fake_write_rationale)

    out = write_montage_outputs(plan, s, str(tmp_path / "reel2"))

    assert len(calls) == 1, "write_rationale_sidecar was not called exactly once"
    _, reel_path_arg = calls[0]
    assert reel_path_arg.endswith(".mp4"), f"expected a .mp4 reel path, got {reel_path_arg!r}"
    edl_path = out["edl"]
    expected_stem = edl_path[: -len(".edl.json")] if edl_path.endswith(".edl.json") else os.path.splitext(edl_path)[0]
    assert reel_path_arg == expected_stem + ".mp4"


def test_cli_montage_no_clips_is_friendly(tmp_path):
    Store(str(tmp_path / "c.db"))  # empty
    r = runner.invoke(app, ["montage", "selected", "--db", str(tmp_path / "c.db")])
    assert r.exit_code == 0, r.output
    assert "no clips" in r.output
