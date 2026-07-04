"""Tests for feeling inference + the music library/suggestion."""

from composerv.music.library import load_library, suggest_track
from composerv.music.mood import build_feeling_prompt, infer_feeling, parse_feeling


def test_build_feeling_prompt_lists_options_and_summaries():
    p = build_feeling_prompt(["kids walk a tea field", "family bbq by the lake"])
    low = p.lower()
    assert all(f in low for f in ["upbeat", "calm", "nostalgic", "sad"])
    assert "kids walk a tea field" in p


def test_parse_feeling_valid_or_default():
    assert parse_feeling("I'd call it nostalgic overall") == "nostalgic"
    assert parse_feeling("UPBEAT!") == "upbeat"
    assert parse_feeling("no idea") == "calm"   # default


def test_infer_feeling_with_injected_run():
    seen = {}

    def fake(prompt):
        seen["prompt"] = prompt
        return "the overall feeling is calm"

    assert infer_feeling(["a quiet evening walk"], run=fake) == "calm"
    assert "a quiet evening walk" in seen["prompt"]


def test_load_library_and_suggest(tmp_path):
    for feel, fn in [("calm", "a.mp3"), ("calm", "b.mp3"), ("sad", "c.wav")]:
        d = tmp_path / "music" / feel
        d.mkdir(parents=True, exist_ok=True)
        (d / fn).write_bytes(b"x")
    lib = load_library(str(tmp_path / "music"))
    assert set(lib) == {"calm", "sad"} and len(lib["calm"]) == 2

    t = suggest_track("calm", lib)
    assert t and "/calm/" in t and t.endswith(".mp3")
    assert suggest_track("upbeat", lib) is not None   # falls back to any available track
    assert suggest_track("upbeat", {}) is None         # nothing to suggest
