"""Tests for story.storylines: analyze the archive (visual / internal-logic / timeline),
surface latent story-lines with a viability judgment, and an honest overall verdict.
Pure prompt/parse tested here; the live claude call is injectable.
"""

import json

from composerv.story.brief import ArchiveBrief
from composerv.story.storylines import (
    StoryAnalysis,
    analyze_storylines,
    build_analysis_prompt,
    parse_analysis,
)


def test_prompt_covers_three_axes_and_viability():
    p = build_analysis_prompt("BRIEF_TEXT")
    assert "BRIEF_TEXT" in p
    low = p.lower()
    for axis in ["visual", "logic", "timeline"]:
        assert axis in low
    assert "viab" in low  # asks for a viability judgment
    assert "missing" in low  # asks what's missing
    assert "json" in low


def test_parse_analysis_full_object():
    text = json.dumps({
        "visual": "a person, parks, a city plaza, a wooden table",
        "internal_logic": "moments connect by a body moving from enclosure to openness",
        "timeline": "spans late Nov to New Year's Day",
        "overall_verdict": "yes, a tender one-month portrait holds",
        "storylines": [{
            "title": "Becoming", "logline": "from vulnerability to a solo run",
            "target_feeling": "tender", "structure": "person_portrait",
            "supporting_moments": ["0001.MP4", "0061.MP4"],
            "viability": "strong", "why": "clear before/after arc",
            "missing": "a clean arrival shot",
        }],
    })
    a = parse_analysis(text)
    assert isinstance(a, StoryAnalysis)
    assert a.visual.startswith("a person")
    assert a.internal_logic and a.timeline
    assert a.overall_verdict.startswith("yes")
    assert len(a.storylines) == 1
    s = a.storylines[0]
    assert s.title == "Becoming"
    assert s.viability == "strong"
    assert s.supporting_moments == ["0001.MP4", "0061.MP4"]
    assert s.missing == "a clean arrival shot"


def test_parse_analysis_flattens_nested_fields():
    # the model sometimes elaborates a field into a nested object/list instead of a string;
    # flatten it rather than dropping the content
    text = json.dumps({
        "visual": {"subjects": ["a young woman", "a caregiver"], "motifs": ["ascending"]},
        "internal_logic": ["body to openness", "enclosure to field"],
        "timeline": "late Nov to NYE",
        "overall_verdict": "holds",
        "storylines": [],
    })
    a = parse_analysis(text)
    assert a.visual and "young woman" in a.visual
    assert a.internal_logic and "openness" in a.internal_logic
    assert a.timeline == "late Nov to NYE"


def test_parse_analysis_skips_prose_preamble_with_braces():
    # the model sometimes adds prose (even containing a stray {brace}) before the real JSON
    text = ('The user wants only JSON. Here is the {object} now:\n'
            '{"visual":"v","internal_logic":"l","timeline":"t","overall_verdict":"ok","storylines":[]}')
    a = parse_analysis(text)
    assert a.visual == "v" and a.overall_verdict == "ok"


def test_parse_analysis_ignores_trailing_prose():
    text = '{"visual":"v","storylines":[],"overall_verdict":"done"}\nHope that helps!'
    a = parse_analysis(text)
    assert a.visual == "v" and a.overall_verdict == "done"


def test_parse_analysis_tolerates_garbage():
    a = parse_analysis("sorry, cannot")
    assert isinstance(a, StoryAnalysis)
    assert a.storylines == []
    assert a.overall_verdict == ""


def test_analyze_storylines_uses_injected_runner():
    brief = ArchiveBrief(n_clips=0, date_start=None, date_end=None, clips=[])
    canned = json.dumps({"visual": "v", "internal_logic": "l", "timeline": "t",
                         "overall_verdict": "ok", "storylines": []})
    got = {}

    def fake_run(p):
        got["p"] = p
        return canned

    a = analyze_storylines(brief, run=fake_run)
    assert a.visual == "v" and a.overall_verdict == "ok"
    assert "clip" in got["p"]  # brief text fed to the runner
