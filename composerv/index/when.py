"""Turn a capture timestamp into a human review label: 年月日 + a coarse time-of-day word.

For looking back ("which day was this?"), hh:mm:ss is noise — a date plus 上午/下午/傍晚 is
what a person actually remembers by. Source = the clip's own capture_time (parsed from the
filename / file metadata; the two agree, both local time). Pure + tested.
"""

from __future__ import annotations

from datetime import datetime

# hour bucket -> word. Each entry is the INCLUSIVE start hour of that part of the day.
_PARTS = [
    (0, "凌晨"), (5, "清晨"), (8, "上午"), (11, "中午"),
    (13, "下午"), (17, "傍晚"), (19, "晚上"), (23, "深夜"),
]


def time_of_day(hour: int) -> str:
    word = _PARTS[0][1]
    for start, name in _PARTS:
        if hour >= start:
            word = name
        else:
            break
    return word


def capture_label(capture_time: str | None) -> str:
    """'2026-01-01T15:55:37' -> '2026年1月1日 下午'. Empty string if missing/unparseable."""
    if not capture_time:
        return ""
    try:
        dt = datetime.fromisoformat(capture_time)
    except ValueError:
        return ""
    return f"{dt.year}年{dt.month}月{dt.day}日 {time_of_day(dt.hour)}"
