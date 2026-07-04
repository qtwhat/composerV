from composerv.models import IntentionList, MusicIntent
from composerv.music.montage import MontagePlan


def test_montage_plan_old_ctor_unchanged():
    p = MontagePlan(feeling="calm", track="/m/a.mp3", tempo=120.0, label="day1",
                    intention=IntentionList(story_id="x"))
    assert p.match_score == 0.0
    assert p.match_breakdown == {}
    assert p.beat_snaps == []
    assert p.library_gap is False
    assert p.intent is None


def test_montage_plan_records_rationale():
    p = MontagePlan(
        feeling="calm", track="/m/a.mp3", tempo=120.0, label="day1",
        intention=IntentionList(story_id="x"),
        intent=MusicIntent(energy_curve=[0.5] * 16),
        match_score=0.82,
        match_breakdown={"shape": 0.9, "tempo": 1.0, "mode": 1.0, "valence": 0.7, "duration": 1.0},
        beat_snaps=[(3.0, 3.05, 6)],
        library_gap=False,
    )
    assert p.match_score == 0.82
    assert p.match_breakdown["shape"] == 0.9
    assert p.beat_snaps[0] == (3.0, 3.05, 6)
