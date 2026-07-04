"""Split one assembled reel into parts so no part runs longer than a budget (default 5 min).

A new part starts when the day changes (so each part is "one day" — the unit a person reviews
by) and when the running length would pass the budget (a long day splits into 续集). Pure: the
day lookup is injected, so this is testable without a store.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from composerv.models import IntentionList


def split_by_day(
    il: IntentionList,
    day_of: Callable[[str], str],
    max_part_s: float = 300.0,
) -> list[tuple[str, IntentionList]]:
    """day_of(source_id) -> a day label (e.g. '2026年1月1日'). Returns [(part_label, il_part)],
    in order. A day that fits in max_part_s is one part labelled by the day; a day that overruns
    is broken into '<day>（1）', '<day>（2）', …"""
    groups: list[tuple[str, list]] = []          # (day, [segments])
    cur_day: str | None = None
    cur_segs: list = []
    cur_len = 0.0
    for seg in il.segments:
        day = day_of(seg.source_id) if seg.source_id else (cur_day or "")
        dur = seg.duration_s or 0.0
        starts_new = (day != cur_day) or (cur_segs and cur_len + dur > max_part_s)
        if starts_new and cur_segs:
            groups.append((cur_day or "", cur_segs))
            cur_segs, cur_len = [], 0.0
        cur_day = day
        cur_segs.append(seg)
        cur_len += dur
    if cur_segs:
        groups.append((cur_day or "", cur_segs))

    per_day = Counter(day for day, _ in groups)
    seen: Counter = Counter()
    out: list[tuple[str, IntentionList]] = []
    for day, segs in groups:
        if per_day[day] > 1:
            seen[day] += 1
            label = f"{day}（{seen[day]}）"
        else:
            label = day
        out.append((label, il.model_copy(update={"segments": segs})))
    return out
