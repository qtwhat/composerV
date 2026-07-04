"""Tests for story.angles: propose story angles from an Archive Brief.

Prompt builder + parser are pure. propose_angles takes an injectable `run(prompt)->str`
so the LLM call is faked in tests; the real call shells the claude CLI.
"""

from composerv.story.angles import (
    STRUCTURE_TYPES,
    build_angles_prompt,
    parse_angles,
    propose_angles,
)
from composerv.story.brief import ArchiveBrief


def test_build_angles_prompt_includes_brief_and_structures():
    p = build_angles_prompt("ARCHIVE_TEXT_HERE", n=3)
    assert "ARCHIVE_TEXT_HERE" in p
    assert "3" in p
    assert "story_circle" in p and "kishotenketsu" in p
    assert "json" in p.lower()


def test_build_angles_prompt_with_spine_constrains():
    p = build_angles_prompt("X", controlling_idea="the quiet exhaustion of earning a view")
    assert "the quiet exhaustion of earning a view" in p


def test_parse_angles_full_object():
    text = (
        '[{"title":"The climb that broke us","logline":"A calm ride turns into a test.",'
        '"target_feeling":"pride","structure":"story_circle",'
        '"beats":[{"function":"establish_ordinary","intent":"easy start"},'
        '{"function":"low_point","intent":"the storm"}]}]'
    )
    angles = parse_angles(text)
    assert len(angles) == 1
    a = angles[0]
    assert a.title == "The climb that broke us"
    assert a.target_feeling == "pride"
    assert a.structure == "story_circle"
    assert [b.function for b in a.beats] == ["establish_ordinary", "low_point"]


def test_parse_angles_tolerates_fence_and_garbage():
    assert parse_angles("sorry, no") == []
    angles = parse_angles('```json\n[{"title":"X"}]\n```')
    assert len(angles) == 1 and angles[0].title == "X"


def test_propose_angles_uses_injected_runner():
    brief = ArchiveBrief(n_clips=0, date_start=None, date_end=None, clips=[])
    canned = '[{"title":"T","logline":"L","target_feeling":"calm","structure":"string_of_pearls","beats":[]}]'
    seen = {}

    def fake_run(prompt):
        seen["prompt"] = prompt
        return canned

    angles = propose_angles(brief, n=2, run=fake_run)
    assert len(angles) == 1 and angles[0].title == "T"
    assert "clip" in seen["prompt"]  # the brief text was fed to the runner


def test_structure_types_cover_family_and_travel():
    assert "journey_quest" in STRUCTURE_TYPES
    assert "person_portrait" in STRUCTURE_TYPES and "year_in_life" in STRUCTURE_TYPES
