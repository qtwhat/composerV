"""Tests for the audio-highlight contract additions (dynamic ducking)."""

import pytest
from pydantic import ValidationError

from composerv.models import AudioHighlight, MusicBed


def test_audio_highlight_defaults():
    h = AudioHighlight(start_s=1.0, end_s=2.0)
    assert h.ramp_s == 0.40  # gentle default (user pick)
    assert h.music_duck_db is None and h.clip_db is None
    assert h.label == ""


def test_audio_highlight_rejects_non_positive_window():
    with pytest.raises(ValidationError):
        AudioHighlight(start_s=2.0, end_s=1.0)
    with pytest.raises(ValidationError):
        AudioHighlight(start_s=1.0, end_s=1.0)


def test_musicbed_new_fields_default_and_old_ctor_unchanged():
    m = MusicBed(path="/m/song.mp3")
    assert m.gain_db == 0.0 and m.duck_db == -15.0 and m.fade_out_s == 1.5  # unchanged
    assert m.highlights == []
    assert m.music_duck_db == -12.0  # gentle default (user pick)
    assert m.highlight_db == 0.0


def test_musicbed_carries_highlights():
    m = MusicBed(path="/m/s.mp3", highlights=[AudioHighlight(start_s=3.0, end_s=4.5, label="child speaks")])
    assert len(m.highlights) == 1 and m.highlights[0].label == "child speaks"
