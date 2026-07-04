"""Human-readable capture labels: 年月日 + a coarse time-of-day word (no hh:mm:ss)."""

from composerv.index.when import capture_label, time_of_day


def test_time_of_day_buckets():
    assert time_of_day(2) == "凌晨"
    assert time_of_day(6) == "清晨"
    assert time_of_day(9) == "上午"
    assert time_of_day(12) == "中午"
    assert time_of_day(15) == "下午"
    assert time_of_day(18) == "傍晚"
    assert time_of_day(21) == "晚上"
    assert time_of_day(23) == "深夜"


def test_capture_label_date_plus_part_of_day():
    assert capture_label("2026-01-01T15:55:37") == "2026年1月1日 下午"
    assert capture_label("2026-01-01T17:24:57") == "2026年1月1日 傍晚"
    assert capture_label("2026-01-01T08:30:00") == "2026年1月1日 上午"


def test_capture_label_no_leading_zeros_in_month_day():
    assert capture_label("2025-11-30T07:46:22") == "2025年11月30日 清晨"


def test_capture_label_empty_when_missing_or_unparseable():
    assert capture_label(None) == ""
    assert capture_label("") == ""
    assert capture_label("not a date") == ""
