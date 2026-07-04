"""Propose story angles from an Archive Brief (the AI's "reflect, human decides" move).

build_angles_prompt / parse_angles are pure. propose_angles takes an injectable
`run(prompt) -> str` (defaults to the claude CLI, text-only) so it's testable offline.
An angle is a candidate spine + structure + beat sheet; the human picks/rewrites it, then
beats get filled with real moments (next step).
"""

from __future__ import annotations

import json
from collections.abc import Callable

from pydantic import BaseModel, Field

from composerv.story.brief import ArchiveBrief

STRUCTURE_TYPES = [
    "story_circle", "kishotenketsu", "string_of_pearls", "theme_and_variations",
    "journey_quest", "three_act", "person_portrait", "year_in_life", "growing_up",
]


class BeatSketch(BaseModel):
    function: str = ""
    intent: str = ""


class StoryAngle(BaseModel):
    title: str = ""
    logline: str = ""
    target_feeling: str = ""
    structure: str = ""
    beats: list[BeatSketch] = Field(default_factory=list)


def build_angles_prompt(brief_text: str, n: int = 3, controlling_idea: str | None = None) -> str:
    spine = (
        f"The director's controlling idea (spine) is: \"{controlling_idea}\". "
        f"All angles must serve it.\n"
        if controlling_idea
        else "The director has not chosen a spine yet, so also propose a candidate feeling per angle.\n"
    )
    return (
        "You are a documentary editor shaping a personal video archive into a story.\n"
        "Here is a brief of the available footage (clips with captions, objects, times):\n\n"
        f"{brief_text}\n\n"
        f"{spine}"
        f"Propose {n} DISTINCT story angles that genuinely fit THIS footage. Personal/travel/"
        "family footage usually has no conflict plot, so prefer non-conflict structures "
        "(journey, kishotenketsu, string-of-pearls, theme-and-variations, person portrait, "
        "year-in-life, growing-up); only use three_act if the footage has a real obstacle.\n"
        "Each angle must be different in feeling AND structure, not three trims of the same idea.\n"
        "For each angle give: title, logline (one sentence), target_feeling (one word), "
        f"structure (one of: {'/'.join(STRUCTURE_TYPES)}), and beats = an ordered list of 3-7 "
        'beats, each {"function": "<dramatic function>", "intent": "<what it must make you feel>"}.\n'
        f"Reply with ONLY a JSON array of {n} objects. No prose, no markdown fence."
    )


def parse_angles(text: str) -> list[StoryAngle]:
    t = text.strip()
    start, end = t.find("["), t.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        items = json.loads(t[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    out: list[StoryAngle] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        beats = [
            BeatSketch(function=str(b.get("function", "")), intent=str(b.get("intent", "")))
            for b in (it.get("beats") or [])
            if isinstance(b, dict)
        ]
        out.append(
            StoryAngle(
                title=str(it.get("title", "")),
                logline=str(it.get("logline", "")),
                target_feeling=str(it.get("target_feeling", "")),
                structure=str(it.get("structure", "")),
                beats=beats,
            )
        )
    return out


def _default_run(prompt: str) -> str:
    from composerv.analyze.backends.claude_cli import claude_text

    return claude_text(prompt)


def propose_angles(
    brief: ArchiveBrief,
    n: int = 3,
    controlling_idea: str | None = None,
    run: Callable[[str], str] | None = None,
) -> list[StoryAngle]:
    run = run or _default_run
    return parse_angles(run(build_angles_prompt(brief.to_prompt_text(), n, controlling_idea)))
