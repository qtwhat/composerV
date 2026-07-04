"""Split one assembled reel into parts: a new part per day, and per 5-minute budget."""

from composerv.models import IntentionList, Segment
from composerv.music.parts import split_by_day


def _il(*spans):  # (source_id, dur)
    return IntentionList(story_id="s", segments=[
        Segment(kind="clip", source_id=sid, in_sec=0.0, out_sec=d, duration_s=d) for sid, d in spans])


def test_split_groups_by_day():
    il = _il(("a", 10), ("b", 10), ("c", 10))   # a,b day-1; c day-2
    day = {"a": "D1", "b": "D1", "c": "D2"}.get
    parts = split_by_day(il, day, max_part_s=300)
    assert [lbl for lbl, _ in parts] == ["D1", "D2"]
    assert [s.source_id for s in parts[0][1].segments] == ["a", "b"]
    assert [s.source_id for s in parts[1][1].segments] == ["c"]


def test_split_breaks_a_long_day_at_the_budget():
    il = _il(("a", 200), ("b", 200), ("c", 200))   # all one day, 600s > 300s
    parts = split_by_day(il, lambda sid: "D1", max_part_s=300)
    assert len(parts) == 3                          # 200 each -> one per part (2x200 > 300)
    assert parts[0][0] == "D1（1）" and parts[1][0] == "D1（2）"   # suffixed when a day splits


def test_single_part_day_keeps_plain_label():
    parts = split_by_day(_il(("a", 30), ("b", 30)), lambda sid: "D1", max_part_s=300)
    assert len(parts) == 1 and parts[0][0] == "D1"


def test_one_oversized_shot_is_its_own_part():
    parts = split_by_day(_il(("a", 400)), lambda sid: "D1", max_part_s=300)
    assert len(parts) == 1 and [s.source_id for s in parts[0][1].segments] == ["a"]
