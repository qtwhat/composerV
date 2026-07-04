"""Tests for audio feature extraction: energy curve computation and resampling."""

import os

import pytest

np = pytest.importorskip("numpy")


def test_resample_curve_produces_16_normalized_points():
    from composerv.music.features import _resample_curve

    # an up-then-down ramp; resampled curve must keep the rise-peak-fall shape
    raw = list(np.concatenate([np.linspace(0.0, 1.0, 50), np.linspace(1.0, 0.0, 50)]))
    out = _resample_curve(raw, n=16)
    assert len(out) == 16
    assert all(0.0 <= v <= 1.0 for v in out)
    peak = out.index(max(out))
    assert 6 <= peak <= 9            # peak near the middle


def test_resample_curve_empty_returns_empty():
    from composerv.music.features import _resample_curve

    assert _resample_curve([], n=16) == []


def test_resample_curve_flat_returns_empty():
    from composerv.music.features import _resample_curve

    # a genuinely flat/silent track is NOT a real arc; return [] so the scorer treats it
    # as a non-match instead of scoring its mid-range curve as a near-hit (spec D1).
    assert _resample_curve([0.3] * 40, n=16) == []


SLOW = pytest.mark.skipif(os.environ.get("CV_RUN_SLOW") != "1", reason="librosa warmup; set CV_RUN_SLOW=1")


@SLOW
def test_estimate_key_mode_on_synth_triads():
    pytest.importorskip("librosa")

    from composerv.music.features import _estimate_key_mode

    sr = 22050
    t = np.linspace(0, 3.0, int(sr * 3.0), endpoint=False)

    def triad(freqs):
        return sum(0.3 * np.sin(2 * np.pi * f * t) for f in freqs)

    c_major = triad([261.63, 329.63, 392.00])   # C E G
    a_minor = triad([220.00, 261.63, 329.63])   # A C E
    maj_mode, _, _ = _estimate_key_mode(c_major, sr)
    min_mode, _, _ = _estimate_key_mode(a_minor, sr)
    assert maj_mode == "major"
    assert min_mode == "minor"


@SLOW
def test_estimate_valence_orders_major_above_minor():
    pytest.importorskip("librosa")
    from composerv.music.features import _estimate_valence

    sr = 22050
    t = np.linspace(0, 2.0, int(sr * 2.0), endpoint=False)
    bright = 0.3 * np.sin(2 * np.pi * 1500 * t)
    v_major = _estimate_valence(bright, sr, "major", 130.0)
    v_minor = _estimate_valence(bright, sr, "minor", 80.0)
    assert 0.0 <= v_minor <= v_major <= 1.0


@pytest.mark.skipif(os.environ.get("CV_RUN_SLOW") != "1", reason="slow test (requires librosa)")
def test_energy_curve_raw_from_audio():
    from composerv.music.features import _energy_curve_raw

    librosa = pytest.importorskip("librosa")

    # Create a synthetic mono audio with varying loudness: quiet then loud then quiet
    sr = 22050
    duration = 2.0  # 2 seconds
    t = np.linspace(0, duration, int(sr * duration), False)

    # Three sections: quiet (0.1 amplitude), loud (0.8 amplitude), quiet again
    y = np.concatenate([
        0.1 * np.sin(2 * np.pi * 440 * t[:len(t) // 3]),
        0.8 * np.sin(2 * np.pi * 440 * t[len(t) // 3:2*len(t) // 3]),
        0.1 * np.sin(2 * np.pi * 440 * t[2*len(t) // 3:]),
    ])

    # Compute RMS energy curve
    energy = _energy_curve_raw(y, sr)

    # Should have many frames (one per STFT window)
    assert len(energy) > 1
    assert all(e >= 0 for e in energy)

    # Middle section should be louder on average than first and last thirds
    mid_start = len(energy) // 3
    mid_end = 2 * len(energy) // 3
    early_mean = energy[:mid_start].mean()
    mid_mean = energy[mid_start:mid_end].mean()
    late_mean = energy[mid_end:].mean()

    assert mid_mean > early_mean * 1.5
    assert mid_mean > late_mean * 1.5


# ---------------------------------------------------------------------------
# Task 4: sidecar I/O, index mtime logic, provenance survival
# ---------------------------------------------------------------------------

def test_sidecar_path_is_sibling_dot_features_json():
    from composerv.music.features import sidecar_path

    assert sidecar_path("/m/calm/satie.mp3") == "/m/calm/satie.features.json"


def test_write_then_read_sidecar_roundtrips(tmp_path):
    from composerv.models import TrackFeatures
    from composerv.music.features import read_sidecar, write_sidecar

    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"\x00")
    tf = TrackFeatures(path=str(audio), duration_s=90.0, tempo_bpm=120.0,
                       energy_curve=[0.5] * 16, mode="major", valence=0.7,
                       source="OGA", license="CC0")
    written = write_sidecar(tf)
    assert written.endswith("a.features.json")
    back = read_sidecar(str(audio))
    assert back is not None
    assert back.tempo_bpm == 120.0 and back.mode == "major" and back.license == "CC0"


def test_read_sidecar_missing_returns_none(tmp_path):
    from composerv.music.features import read_sidecar

    assert read_sidecar(str(tmp_path / "nope.mp3")) is None


def test_index_skips_fresh_recomputes_stale(tmp_path, monkeypatch):
    import composerv.music.features as feat
    from composerv.models import TrackFeatures

    calls = []

    def fake_compute(path, *, source="", license=""):
        calls.append(path)
        return TrackFeatures(path=path, duration_s=10.0, tempo_bpm=100.0)

    monkeypatch.setattr(feat, "compute_features", fake_compute)

    a = tmp_path / "a.mp3"
    a.write_bytes(b"\x00")
    n1 = feat.index_music_dir(str(tmp_path))
    assert n1 == 1 and calls == [str(a)]
    # second run: sidecar fresh -> skip
    n2 = feat.index_music_dir(str(tmp_path))
    assert n2 == 0
    # touch the source newer than the sidecar -> recompute
    sc = feat.sidecar_path(str(a))
    future = os.path.getmtime(sc) + 100
    os.utime(str(a), (future, future))
    n3 = feat.index_music_dir(str(tmp_path))
    assert n3 == 1


def test_index_preserves_source_license_across_recompute(tmp_path, monkeypatch):
    """Re-indexing a stale track must NOT wipe provenance (spec D7)."""
    import composerv.music.features as feat
    from composerv.models import TrackFeatures

    # compute_features always returns blank provenance (mirrors the real index call site)
    def blank_compute(path, *, source="", license=""):
        return TrackFeatures(path=path, duration_s=10.0, tempo_bpm=100.0,
                             source=source, license=license)

    monkeypatch.setattr(feat, "compute_features", blank_compute)

    a = tmp_path / "a.mp3"
    a.write_bytes(b"\x00")
    feat.index_music_dir(str(tmp_path))
    # backfill provenance into the sidecar
    tf = feat.read_sidecar(str(a))
    tf.source, tf.license = "OpenGameArt", "CC0"
    feat.write_sidecar(tf)
    # make the source newer -> force recompute
    sc = feat.sidecar_path(str(a))
    future = os.path.getmtime(sc) + 100
    os.utime(str(a), (future, future))
    n = feat.index_music_dir(str(tmp_path))
    assert n == 1
    back = feat.read_sidecar(str(a))
    assert back.source == "OpenGameArt" and back.license == "CC0"  # survived recompute


@SLOW
def test_compute_features_on_synth_ramp(tmp_path):
    sf = pytest.importorskip("soundfile")
    pytest.importorskip("librosa")
    from composerv.music.features import compute_features

    sr = 22050
    t = np.linspace(0, 8.0, int(sr * 8.0), endpoint=False)
    amp = np.concatenate([np.linspace(0, 1, t.size // 2), np.linspace(1, 0, t.size - t.size // 2)])
    y = amp * np.sin(2 * np.pi * 220 * t)
    path = tmp_path / "ramp.wav"
    sf.write(str(path), y, sr)
    tf = compute_features(str(path), source="synth", license="N/A")
    assert len(tf.energy_curve) == 16
    assert tf.duration_s > 7.5
    assert tf.source == "synth"
    # the rise-then-fall amplitude ramp peaks near the middle of the 16-point curve
    assert 6 <= tf.energy_curve.index(max(tf.energy_curve)) <= 9
