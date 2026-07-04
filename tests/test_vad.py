"""Voice activity detection: pure segment logic (deterministic) + a model sanity check."""

import pytest

from composerv.audio.vad import segments_from_probs


def test_no_speech_when_all_below_threshold():
    assert segments_from_probs([0.1] * 10, chunk_s=0.1) == []


def test_single_run_becomes_one_segment():
    probs = [0.1, 0.1, 0.9, 0.9, 0.9, 0.1, 0.1]
    segs = segments_from_probs(probs, chunk_s=0.1, min_speech_s=0.2, min_silence_s=0.05)
    assert len(segs) == 1
    assert abs(segs[0][0] - 0.2) < 1e-9 and abs(segs[0][1] - 0.5) < 1e-9


def test_short_gap_merges_into_one_sentence():
    probs = [0.9, 0.9, 0.1, 0.9, 0.9]  # a 0.1s dip inside one sentence
    segs = segments_from_probs(probs, chunk_s=0.1, min_speech_s=0.1, min_silence_s=0.25)
    assert len(segs) == 1 and abs(segs[0][1] - 0.5) < 1e-9


def test_long_gap_splits_into_two():
    probs = [0.9, 0.9, 0.1, 0.1, 0.1, 0.9, 0.9]
    segs = segments_from_probs(probs, chunk_s=0.1, min_speech_s=0.1, min_silence_s=0.15)
    assert len(segs) == 2


def test_too_short_blip_is_dropped():
    probs = [0.1, 0.9, 0.1]  # a single 0.1s blip
    assert segments_from_probs(probs, chunk_s=0.1, min_speech_s=0.25) == []


def test_silence_yields_no_speech_through_the_real_model():
    pytest.importorskip("onnxruntime")
    import numpy as np

    from composerv.audio.vad import speech_probs

    probs = speech_probs(np.zeros(16000, dtype=np.float32))  # 1s of digital silence
    assert probs and all(p < 0.5 for p in probs)


def test_real_speech_is_detected(tmp_path):
    # positive control: a broken loop (e.g. missing the 64-sample context) passes the silence
    # test but FAILS here, so this guards the real model path against regression.
    import shutil
    import subprocess

    if shutil.which("say") is None or shutil.which("ffmpeg") is None:
        pytest.skip("needs macOS 'say' + ffmpeg")
    pytest.importorskip("onnxruntime")
    from composerv.audio.vad import detect_speech

    aiff = str(tmp_path / "s.aiff")
    subprocess.run(["say", "-o", aiff, "This is a complete spoken sentence for the detector."],
                   check=True)
    segs = detect_speech(aiff)
    assert segs and max(e - s for s, e in segs) > 1.0  # a real, substantial sentence
