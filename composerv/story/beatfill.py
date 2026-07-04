"""Bind a chosen story-line's beats to real clips + in/out ranges, then build a Story.

This is the step after analysis: the human picked a viable story-line; now the AI fills its
beats with concrete moments from the indexed footage, producing a Story that compiles to
an IntentionList (and thus to the live preview + FCPXML).

build_fill_prompt / parse_beats are pure; fill_story takes an injectable run(prompt)->str.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable

from pydantic import BaseModel

from composerv.models import Beat, ControllingIdea, Moment, Story, Structure
from composerv.story.brief import ArchiveBrief
from composerv.story.storylines import StorylineCandidate
from composerv.store.db import Store


class BoundBeat(BaseModel):
    function: str = ""
    intent: str = ""
    clip: str = ""        # clip name or path the AI chose from the brief
    in_sec: float = 0.0
    out_sec: float = 0.0
    why: str = ""


def build_fill_prompt(storyline: StorylineCandidate, brief_text: str) -> str:
    return (
        "You are assembling a rough cut for a chosen story-line from a personal video archive.\n\n"
        f"STORY-LINE:\n  title: {storyline.title}\n  logline (the spine): {storyline.logline}\n"
        f"  target_feeling: {storyline.target_feeling}\n  structure: {storyline.structure}\n\n"
        f"AVAILABLE FOOTAGE (clip name, time, duration, captions):\n{brief_text}\n\n"
        "Produce an ordered beat sheet that tells THIS story with THESE clips. For each beat pick "
        "ONE clip (by its name from the brief) and an in/out window in seconds WITHIN that clip's "
        "duration (keep beats roughly 2-5s). Order the beats to serve the logline and the chosen "
        "structure. A clip may be used more than once.\n"
        'Reply with ONLY a JSON array, each item: {"function":"<dramatic function>", '
        '"intent":"<what it should make you feel>", "clip":"<clip name from the brief>", '
        '"in":<seconds>, "out":<seconds>, "why":"<why this clip here>"}. '
        "Your reply MUST start with [ and end with ] — no other text."
    )


def parse_beats(text: str) -> list[BoundBeat]:
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
    out: list[BoundBeat] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            in_sec = float(it.get("in", 0.0))
            out_sec = float(it.get("out", 0.0))
        except (TypeError, ValueError):
            in_sec, out_sec = 0.0, 0.0
        out.append(BoundBeat(
            function=str(it.get("function", "")), intent=str(it.get("intent", "")),
            clip=str(it.get("clip", "")), in_sec=in_sec, out_sec=out_sec,
            why=str(it.get("why", "")),
        ))
    return out


def _default_run(prompt: str) -> str:
    from composerv.analyze.backends.claude_cli import claude_text

    return claude_text(prompt)


def fill_story(
    storyline: StorylineCandidate,
    brief: ArchiveBrief,
    store: Store,
    run: Callable[[str], str] | None = None,
    retries: int = 2,
) -> tuple[Story, dict[str, Moment], dict[str, str]]:
    """Returns (Story, moments_by_id, source_paths). source_paths maps a Moment's
    source_clip_id -> its proxy path, for building the preview EDL."""
    run = run or _default_run

    # resolve a clip string (name or full path) to the canonical asset
    by_key: dict[str, object] = {}
    for a in store.list_assets():
        by_key[a.path] = a
        by_key[os.path.basename(a.path)] = a

    prompt = build_fill_prompt(storyline, brief.to_prompt_text())
    bound: list[BoundBeat] = []
    for _ in range(retries + 1):
        bound = parse_beats(run(prompt))
        if bound:
            break

    beats: list[Beat] = []
    moments: dict[str, Moment] = {}
    source_paths: dict[str, str] = {}
    order = 0
    for bb in bound:
        asset = by_key.get(bb.clip) or by_key.get(os.path.basename(bb.clip))
        if asset is None:
            continue  # AI named a clip we don't have; skip rather than fabricate
        dur = asset.duration_s or 0.0
        in_sec = max(0.0, bb.in_sec)
        out_sec = bb.out_sec if bb.out_sec > in_sec else in_sec + 3.0
        if dur:
            out_sec = min(out_sec, dur)
        if out_sec <= in_sec:
            continue
        mid = f"m{order}"
        moments[mid] = Moment(id=mid, source_clip_id=asset.path, in_sec=in_sec, out_sec=out_sec)
        beats.append(Beat(
            id=f"b{order}", order=order, function=bb.function, intent=bb.intent,
            target_duration_s=out_sec - in_sec, chosen_moment=mid, why_moment=bb.why,
        ))
        if asset.proxy_path:
            source_paths[asset.path] = asset.proxy_path
        order += 1

    story = Story(
        id="story-1",
        name=storyline.title,
        controlling_idea=ControllingIdea(
            one_line=storyline.logline,
            target_feeling=storyline.target_feeling,
            authored_by="human_from_ai_draft",
        ),
        structure=Structure(type=storyline.structure),
        beats=beats,
    )
    return story, moments, source_paths
