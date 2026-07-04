import json

from composerv.models import IntentionList, MusicIntent, TrackFeatures
from composerv.music.features import write_sidecar
from composerv.music.montage import MontagePlan
from composerv.music.rationale import write_rationale_sidecar


def test_writes_rationale_next_to_reel(tmp_path):
    reel = tmp_path / "day1.mp4"
    reel.write_bytes(b"\x00")
    plan = MontagePlan(
        feeling="calm", track="/m/rise.mp3", tempo=110.0, label="day1",
        intention=IntentionList(story_id="x"),
        intent=MusicIntent(energy_curve=[0.5] * 16, arc_text="quiet to peak"),
        match_score=0.82,
        match_breakdown={"shape": 0.9, "tempo": 1.0, "mode": 1.0, "valence": 0.7, "duration": 1.0},
        beat_snaps=[(3.0, 3.05, 6)], library_gap=False,
    )
    out = write_rationale_sidecar(plan, str(reel))
    assert out.endswith("day1.music-rationale.json")
    data = json.loads(open(out).read())
    assert data["track"] == "/m/rise.mp3"
    assert data["match_score"] == 0.82
    assert data["match_breakdown"]["shape"] == 0.9
    assert data["library_gap"] is False
    assert data["intent"]["arc_text"] == "quiet to peak"
    assert data["beat_snaps"][0] == [3.0, 3.05, 6]


def test_rationale_omits_track_curve_when_no_sidecar(tmp_path):
    reel = tmp_path / "day1.mp4"
    reel.write_bytes(b"\x00")
    plan = MontagePlan(feeling="calm", track="/m/missing.mp3", tempo=0.0, label="d",
                       intention=IntentionList(story_id="x"),
                       intent=MusicIntent(energy_curve=[0.5] * 16))
    out = write_rationale_sidecar(plan, str(reel))
    data = json.loads(open(out).read())
    assert data.get("track_energy_curve") in (None, [])
    # When no sidecar exists, license and source should be empty strings
    assert data.get("track_license") == ""
    assert data.get("track_source") == ""


def test_rationale_records_track_license_and_source(tmp_path):
    """Verify that CC-BY attribution (license + source) from the track sidecar
    travels into the rationale JSON."""
    # Create a mock track file with a sidecar containing CC-BY attribution
    track_path = tmp_path / "rise.mp3"
    track_path.write_bytes(b"\x00")

    cc_by_attribution = "CC BY 3.0 — Rise by Kevin MacLeod (incompetech.com), licensed under CC BY 3.0"
    track_source = "incompetech"

    features = TrackFeatures(
        path=str(track_path),
        duration_s=120.0,
        tempo_bpm=110.0,
        beat_times=[1.0, 2.0],
        energy_curve=[0.5] * 16,
        license=cc_by_attribution,
        source=track_source,
    )
    write_sidecar(features)

    # Create a reel and plan referencing that track
    reel = tmp_path / "day1.mp4"
    reel.write_bytes(b"\x00")
    plan = MontagePlan(
        feeling="calm",
        track=str(track_path),
        tempo=110.0,
        label="day1",
        intention=IntentionList(story_id="x"),
        intent=MusicIntent(energy_curve=[0.5] * 16),
        match_score=0.85,
        match_breakdown={"shape": 0.9, "tempo": 1.0, "mode": 1.0, "valence": 0.7, "duration": 1.0},
        beat_snaps=[(3.0, 3.05, 6)],
    )
    out = write_rationale_sidecar(plan, str(reel))
    data = json.loads(open(out).read())

    # Verify the license and source from the track sidecar are in the rationale
    assert data["track_license"] == cc_by_attribution
    assert data["track_source"] == track_source
    assert data["track"] == str(track_path)
