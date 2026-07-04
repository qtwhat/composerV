"""Sentence-level transcription: pure sentence splitting + a real multi-sentence check."""

import shutil
import subprocess

import pytest

from composerv.audio.transcribe import sentences_from_words


def _w(word, s, e):
    return {"word": word, "start": s, "end": e}


def test_splits_two_sentences_on_terminal_punctuation():
    words = [_w(" Hello", 0.0, 0.5), _w(" there.", 0.5, 1.0),
             _w(" How", 1.2, 1.5), _w(" are", 1.5, 1.7), _w(" you?", 1.7, 2.1)]
    sents = sentences_from_words(words)
    assert len(sents) == 2
    assert abs(sents[0][0] - 0.0) < 1e-9 and abs(sents[0][1] - 1.0) < 1e-9 and sents[0][2] == "Hello there."
    assert abs(sents[1][0] - 1.2) < 1e-9 and abs(sents[1][1] - 2.1) < 1e-9 and sents[1][2] == "How are you?"


def test_no_terminal_punctuation_is_one_sentence():
    sents = sentences_from_words([_w(" um", 0.0, 0.3), _w(" yeah", 0.3, 0.6)])
    assert len(sents) == 1 and abs(sents[0][1] - 0.6) < 1e-9


def test_splits_on_long_pause_without_terminal_punctuation():
    # Whisper's Chinese output often carries NO terminal punctuation; a clear pause between
    # words is the sentence boundary. Without this, a whole monologue is one giant "sentence".
    words = [_w("你在", 0.0, 1.06), _w("拍我吗", 1.06, 1.64),    # 1.26s pause follows
             _w("我在", 2.90, 3.32), _w("录像", 3.32, 3.94)]
    sents = sentences_from_words(words)
    assert len(sents) == 2
    assert sents[0] == (0.0, 1.64, "你在拍我吗")
    assert sents[1] == (2.90, 3.94, "我在录像")


def test_short_gap_does_not_split():
    # a sub-threshold gap (0.3s) inside one breath stays a single sentence
    words = [_w("我", 0.0, 0.3), _w("买了", 0.6, 1.0), _w("这个", 1.0, 1.4)]
    sents = sentences_from_words(words)
    assert len(sents) == 1 and sents[0][2] == "我买了这个"


def test_chinese_terminal_punctuation():
    words = [_w("你好", 0.0, 0.5), _w("。", 0.5, 0.6), _w("再见", 0.8, 1.2), _w("！", 1.2, 1.3)]
    sents = sentences_from_words(words)
    assert len(sents) == 2 and sents[0][2] == "你好。" and sents[1][2] == "再见！"


def test_empty_is_no_sentences():
    assert sentences_from_words([]) == []


def test_transcribe_two_real_sentences(tmp_path):
    if shutil.which("say") is None or shutil.which("ffmpeg") is None:
        pytest.skip("needs macOS 'say' + ffmpeg")
    pytest.importorskip("mlx_whisper")
    from composerv.audio.transcribe import transcribe_sentences

    aiff = str(tmp_path / "two.aiff")
    subprocess.run(["say", "-o", aiff, "The sky is blue today. I am going to the park later."], check=True)
    sents = transcribe_sentences(aiff)
    assert len(sents) >= 2  # two distinct sentences, each with its own [start,end]
    assert all(e > s for s, e, _t in sents)
