import pytest
from pydantic import ValidationError

from composerv.models import MusicIntent, TrackFeatures


def test_track_features_defaults_and_reserved_fields():
    tf = TrackFeatures(path="/m/a.mp3", duration_s=90.0, tempo_bpm=120.0)
    assert tf.beat_times == []
    assert tf.mode == "unknown"
    assert tf.energy_curve == []
    assert tf.valence == 0.5
    assert tf.source == "" and tf.license == ""
    # direction-3 reserved fields exist but stay empty this round
    assert tf.phrase_boundaries == []
    assert tf.climax_t is None


def test_track_features_carries_curve_and_provenance():
    tf = TrackFeatures(
        path="/m/a.mp3", duration_s=90.0, tempo_bpm=128.0,
        beat_times=[0.0, 0.5, 1.0], mode="major",
        energy_curve=[0.1] * 16, valence=0.7,
        source="OpenGameArt", license="CC0",
    )
    assert len(tf.energy_curve) == 16
    assert tf.source == "OpenGameArt" and tf.license == "CC0"


def test_music_intent_defaults():
    mi = MusicIntent()
    assert mi.energy_curve == []
    assert mi.tempo_lo == 0.0 and mi.tempo_hi == 0.0   # 0 = unconstrained
    assert mi.mode_pref == "any"
    assert mi.valence == 0.5
    assert mi.target_duration_s == 0.0
    assert mi.arc_text == ""


def test_music_intent_roundtrips_through_json():
    mi = MusicIntent(energy_curve=[0.2, 0.6, 0.8, 0.3] * 4, tempo_lo=100.0,
                     tempo_hi=160.0, mode_pref="major", valence=0.6,
                     target_duration_s=120.0, arc_text="quiet to peak to calm")
    again = MusicIntent.model_validate_json(mi.model_dump_json())
    assert again == mi


def test_track_features_energy_curve_17_points_raises():
    """energy_curve with wrong length (17 instead of 16) should raise ValidationError."""
    with pytest.raises(ValidationError):
        TrackFeatures(path="/m/a.mp3", duration_s=1.0, energy_curve=[0.5] * 17)


def test_track_features_energy_curve_out_of_bounds_raises():
    """energy_curve with value > 1.0 should raise ValidationError."""
    with pytest.raises(ValidationError):
        TrackFeatures(path="/m/a.mp3", duration_s=1.0, energy_curve=[1.5] * 16)


def test_track_features_energy_curve_negative_raises():
    """energy_curve with negative value should raise ValidationError."""
    with pytest.raises(ValidationError):
        TrackFeatures(path="/m/a.mp3", duration_s=1.0, energy_curve=[-0.1] + [0.5] * 15)


def test_track_features_energy_curve_valid_16_points():
    """Valid 16-point energy_curve should construct fine."""
    tf = TrackFeatures(path="/m/a.mp3", duration_s=1.0, energy_curve=[0.5] * 16)
    assert len(tf.energy_curve) == 16
    assert all(v == 0.5 for v in tf.energy_curve)


def test_track_features_energy_curve_empty_is_valid():
    """Empty energy_curve (the default) should construct fine."""
    tf = TrackFeatures(path="/m/a.mp3", duration_s=1.0)
    assert tf.energy_curve == []


def test_music_intent_energy_curve_15_points_raises():
    """energy_curve with wrong length (15 instead of 16) should raise ValidationError."""
    with pytest.raises(ValidationError):
        MusicIntent(energy_curve=[0.5] * 15)


def test_music_intent_energy_curve_out_of_bounds_raises():
    """energy_curve with value > 1.0 should raise ValidationError."""
    with pytest.raises(ValidationError):
        MusicIntent(energy_curve=[0.5] * 15 + [1.5])


def test_music_intent_energy_curve_valid_16_points():
    """Valid 16-point energy_curve should construct fine."""
    mi = MusicIntent(energy_curve=[0.3] * 16)
    assert len(mi.energy_curve) == 16
    assert all(v == 0.3 for v in mi.energy_curve)


def test_music_intent_energy_curve_empty_is_valid():
    """Empty energy_curve (the default) should construct fine."""
    mi = MusicIntent()
    assert mi.energy_curve == []
