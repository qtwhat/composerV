"""Judge whether a clip's talk is worth remembering, and keep the worthy exchange WHOLE."""

from composerv.audio.keyline import (
    build_worth_prompt,
    parse_span,
    select_memorable_span,
)


def test_build_worth_prompt_lists_sentences_and_asks_for_a_range_or_none():
    prompt = build_worth_prompt(
        [(0.0, 1.5, "你在拍我吗"), (2.0, 5.0, "你回去会剪成一个 vlog 吗")]
    )
    assert "你在拍我吗" in prompt and "vlog" in prompt
    assert "none" in prompt.lower()          # the skip option is offered


def test_parse_span_range_and_single_and_none():
    assert parse_span("2-4", 5) == (1, 3)     # 1-based inclusive -> 0-based inclusive
    assert parse_span("3", 5) == (2, 2)        # a single line is a 1-long span
    assert parse_span("2 to 5", 5) == (1, 4)
    assert parse_span("none", 5) is None
    assert parse_span("nothing worth keeping", 5) is None


def test_parse_span_clamps_to_range():
    assert parse_span("0-9", 5) == (0, 4)


def test_select_keeps_the_whole_worthy_exchange():
    sents = [(0.0, 1.0, "um"), (2.0, 4.0, "你回去会剪成一个 vlog 吗"),
             (4.5, 6.0, "我不知道"), (10.0, 12.0, "拜拜")]
    # the LLM says lines 2-3 are the worthy exchange
    out = select_memorable_span(sents, run=lambda p: "2-3")
    assert out == [(2.0, 4.0, "你回去会剪成一个 vlog 吗"), (4.5, 6.0, "我不知道")]


def test_select_returns_empty_when_nothing_is_worth_keeping():
    sents = [(0.0, 1.0, "你在拍我吗"), (2.0, 3.0, "嗯")]
    assert select_memorable_span(sents, run=lambda p: "none") == []


def test_select_without_text_keeps_all_speech_and_never_calls_llm():
    # VAD-only windows (no transcript): can't judge worth, so keep the speech (don't truncate).
    def boom(_p):
        raise AssertionError("must not call the LLM without transcript text")

    sents = [(0.0, 1.0), (2.0, 6.0)]
    assert select_memorable_span(sents, run=boom) == [(0.0, 1.0), (2.0, 6.0)]


def test_select_empty_is_empty():
    assert select_memorable_span([], run=lambda p: "1-2") == []
