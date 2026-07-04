"""compile_story: the pure Story -> IntentionList compile step.

The single place the story outline becomes a cut. Pure and one-directional: same inputs
produce the same IntentionList, and nothing compiles back from the IntentionList to the
Story. Both the live preview engine and the FCPXML emitter consume the result.
"""

from __future__ import annotations

from collections.abc import Mapping

from composerv.models import IntentionList, Moment, Segment, Story


def _fit(in_sec: float, out_sec: float, budget_s: float) -> float:
    """Return the out-point that fits a moment to a beat's duration budget.

    A moment longer than the budget is trimmed at the tail (its in-point, the meaningful
    start the AI/human chose, is kept). A moment shorter than the budget keeps its natural
    length: we never invent footage the source range does not contain.
    """
    natural = out_sec - in_sec
    return in_sec + min(natural, budget_s)


def compile_story(story: Story, moments: Mapping[str, Moment]) -> IntentionList:
    segments: list[Segment] = []

    for beat in sorted(story.beats, key=lambda b: b.order):
        if beat.chosen_moment is None:
            # an unfilled beat is a deliberate, visible hole in the preview
            segments.append(
                Segment(
                    kind="gap",
                    duration_s=beat.target_duration_s,
                    label=beat.function,
                )
            )
            continue

        moment = moments[beat.chosen_moment]  # KeyError if the story references a moment we don't have
        out_sec = _fit(moment.in_sec, moment.out_sec, beat.target_duration_s)
        segments.append(
            Segment(
                kind="clip",
                source_id=moment.source_clip_id,
                in_sec=moment.in_sec,
                out_sec=out_sec,
                duration_s=out_sec - moment.in_sec,
                label=beat.function,
                note=beat.why_moment,
                transition_in=beat.transition_intent,
            )
        )

    return IntentionList(story_id=story.id, segments=segments)
