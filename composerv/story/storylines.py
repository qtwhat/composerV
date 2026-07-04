"""Analyze an archive and surface its latent story-lines, with a viability judgment.

This is the story layer's analysis-first step (per the user's framing): not "pick from
finished angles" but "tell me what story-lines are latent here, analyzed across visuals /
internal logic / timeline, and whether they can even constitute a story (and what's
missing)." The human reacts to this analysis; only then do we fill beats and compile.

build_analysis_prompt / parse_analysis are pure; analyze_storylines takes an injectable
`run(prompt)->str` (defaults to the claude CLI, text-only).
"""

from __future__ import annotations

import json
from collections.abc import Callable

from pydantic import BaseModel, Field

from composerv.story.angles import STRUCTURE_TYPES
from composerv.story.brief import ArchiveBrief


class StorylineCandidate(BaseModel):
    title: str = ""
    logline: str = ""
    target_feeling: str = ""
    structure: str = ""
    supporting_moments: list[str] = Field(default_factory=list)  # clip names/times backing it
    viability: str = ""  # strong | plausible | weak | insufficient
    why: str = ""        # reasoning for the viability
    missing: str = ""    # what's needed to make it hold


class StoryAnalysis(BaseModel):
    visual: str = ""          # 画面: what the imagery offers
    internal_logic: str = ""  # 内在逻辑: causal/thematic/emotional connections
    timeline: str = ""        # 时间线: what the chronology implies
    overall_verdict: str = "" # honest: does this constitute a story, or a collection + what's needed
    storylines: list[StorylineCandidate] = Field(default_factory=list)


def build_analysis_prompt(brief_text: str) -> str:
    return (
        "You are a documentary editor analyzing a personal video archive to find whether a "
        "story is latent in it. Here is a brief of the footage (clips with captions, objects, "
        f"times):\n\n{brief_text}\n\n"
        "Analyze the material along THREE axes and be honest, including when it does NOT cohere:\n"
        "1. visual: what the imagery actually offers (recurring people, places, objects, shot "
        "types, visual motifs/continuity).\n"
        "2. internal_logic: do the moments connect causally / thematically / emotionally? Does "
        "one lead to another, or is it a loose collection?\n"
        "3. timeline: what the capture-time chronology implies (a day, a month, a progression?).\n\n"
        "Then surface the 2-3 STRONGEST latent story-lines (fewer is fine if the footage is thin). "
        "Keep every field concise (1-2 sentences). For EACH give: title, logline, "
        "target_feeling (one word), structure (one of: " + "/".join(STRUCTURE_TYPES) + "), "
        "supporting_moments (clip names/times that back it), viability (one of: strong | "
        "plausible | weak | insufficient), why (reasoning for that viability), and missing (what "
        "footage/beat is needed to make it hold).\n"
        "Finally give an overall_verdict: does this footage constitute a story (and which line is "
        "strongest), or is it currently a collection that needs more — say so plainly.\n\n"
        "visual, internal_logic, timeline, and overall_verdict must each be a SINGLE plain-text "
        "string of 2-4 concise sentences (NOT a nested object). supporting_moments is a list of "
        "strings.\n"
        "Reply with ONLY a JSON object: {\"visual\":\"...\", \"internal_logic\":\"...\", "
        "\"timeline\":\"...\", \"overall_verdict\":\"...\", \"storylines\":[...]}. "
        "Your reply MUST start with the character { and end with } — no text, reasoning, or "
        "markdown fence before or after the JSON."
    )


def _as_text(v) -> str:
    """Flatten a string/list/dict the model may have returned into readable prose, so a
    field elaborated into a nested object is never silently dropped."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        return "; ".join(_as_text(x) for x in v if x is not None)
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_as_text(val)}" for k, val in v.items())
    return str(v)


_EXPECTED_KEYS = ("visual", "internal_logic", "timeline", "overall_verdict", "storylines")


def _matching_brace(text: str, start: int) -> int:
    """Index of the '}' matching the '{' at `start`, string/escape aware; -1 if none."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _extract_obj(text: str) -> dict | None:
    """Find the first balanced {...} that parses to a dict with our expected keys.

    Robust to a prose preamble (even one containing a stray brace) and to trailing text.
    """
    i = 0
    while True:
        start = text.find("{", i)
        if start == -1:
            return None
        end = _matching_brace(text, start)
        if end == -1:
            return None
        try:
            d = json.loads(text[start : end + 1])
            if isinstance(d, dict) and any(k in d for k in _EXPECTED_KEYS):
                return d
        except json.JSONDecodeError:
            pass
        i = start + 1


def parse_analysis(text: str) -> StoryAnalysis:
    d = _extract_obj(text)
    if d is None:
        return StoryAnalysis()

    lines = []
    for s in d.get("storylines") or []:
        if not isinstance(s, dict):
            continue
        sm = s.get("supporting_moments") or []
        if not isinstance(sm, list):
            sm = [str(sm)]
        lines.append(
            StorylineCandidate(
                title=_as_text(s.get("title", "")),
                logline=_as_text(s.get("logline", "")),
                target_feeling=_as_text(s.get("target_feeling", "")),
                structure=_as_text(s.get("structure", "")),
                supporting_moments=[str(x) for x in sm],
                viability=_as_text(s.get("viability", "")),
                why=_as_text(s.get("why", "")),
                missing=_as_text(s.get("missing", "")),
            )
        )
    return StoryAnalysis(
        visual=_as_text(d.get("visual", "")),
        internal_logic=_as_text(d.get("internal_logic", "")),
        timeline=_as_text(d.get("timeline", "")),
        overall_verdict=_as_text(d.get("overall_verdict", "")),
        storylines=lines,
    )


def _default_run(prompt: str) -> str:
    from composerv.analyze.backends.claude_cli import claude_text

    return claude_text(prompt)


def analyze_storylines(
    brief: ArchiveBrief, run: Callable[[str], str] | None = None, retries: int = 2
) -> StoryAnalysis:
    run = run or _default_run
    prompt = build_analysis_prompt(brief.to_prompt_text())
    result = StoryAnalysis()
    for _ in range(retries + 1):
        result = parse_analysis(run(prompt))
        if result.storylines or result.overall_verdict or result.visual:
            return result  # got something usable
    return result  # last attempt (may be empty if the LLM kept failing)
