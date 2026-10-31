from rec_agent.catalog import Catalog
from rec_agent.config import Settings
from rec_agent.models import Location, RuntimeSignals, Scene, TriggerType
from rec_agent.policy import Policy


def _scene(scene_id: str = "s1") -> Scene:
    return Scene(
        scene_id=scene_id,
        trigger=TriggerType.impression,
        occurred_at="2026-10-20T12:10:00+08:00",
        location=Location(lng=121.47, lat=31.23, district="南京西路"),
        meal_period="lunch",
    )


def test_opportunity_score_logged_on_recommend_now():
    cat = Catalog()
    policy = Policy()
    snap = cat.get_user("u_noodle_regular")
    dec = policy.check_opportunity(snap, _scene())
    assert dec.result.value == "recommend_now"
    assert 0.0 <= dec.score <= 1.0
    assert "has_short_term" in dec.features
    assert dec.features["frequent_merchant_count"] >= 0


def test_opportunity_min_score_soft_suppress():
    cat = Catalog()
    policy = Policy(Settings(opportunity_min_score=0.99))
    snap = cat.get_user("u_newcomer")
    # newcomer: weaker frequent/short-term → score usually < 0.99
    dec = policy.check_opportunity(snap, _scene("soft"))
    assert dec.result.value == "suppress"
    assert dec.reason_code == "low_opportunity_score"
    assert dec.score < 0.99


def test_hard_rule_still_beats_score():
    cat = Catalog()
    policy = Policy(Settings(opportunity_min_score=0.0))
    snap = cat.get_user("u_noodle_regular")
    snap.runtime = RuntimeSignals(scene_seen_recently=True)
    dec = policy.check_opportunity(snap, _scene("dup"))
    assert dec.reason_code == "duplicate_scene_suppress"
    assert "scene_seen_recently" in dec.features
