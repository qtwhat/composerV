"""Tests for the claude-cli backend's pure parts (prompt builder + response parser).

The live `claude -p` call is validated manually, not in CI (slow / non-deterministic).
"""

from composerv.analyze.backends.claude_cli import build_prompt, parse_response


def test_build_prompt_lists_images_and_enum():
    p = build_prompt(["/a/0.jpg", "/a/1.jpg"])
    assert "/a/0.jpg" in p and "/a/1.jpg" in p
    assert "wide" in p and "aerial" in p  # the shot_type enum is offered
    assert "JSON" in p or "json" in p


def test_parse_response_plain_json_array():
    text = (
        '[{"caption":"a dog runs","shot_type":"wide","objects":["dog"],"salience":0.5},'
        '{"caption":"a face","shot_type":"bogus"}]'
    )
    out = parse_response(text, 2)
    assert len(out) == 2
    assert out[0].caption == "a dog runs"
    assert out[0].shot_type == "wide"
    assert out[0].objects == ["dog"]
    assert out[1].shot_type == "unknown"  # invalid enum coerced


def test_parse_response_strips_code_fence():
    text = '```json\n[{"caption":"x","shot_type":"close"}]\n```'
    out = parse_response(text, 1)
    assert out[0].caption == "x" and out[0].shot_type == "close"


def test_parse_response_pads_when_too_few():
    out = parse_response('[{"caption":"only one","shot_type":"wide"}]', 3)
    assert len(out) == 3
    assert out[0].caption == "only one"
    assert out[1].caption.startswith("[")  # error placeholder, keeps alignment


def test_parse_response_garbage_returns_n_placeholders():
    out = parse_response("I'm sorry, I can't.", 2)
    assert len(out) == 2
    assert all(c.caption.startswith("[") for c in out)
