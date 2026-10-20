from mini_agent.catalog import Catalog
from mini_agent.engine import RecommendationEngine
from mini_agent.models import Location, Scene, TriggerType


def _scene(scene_id: str = "s1") -> Scene:
    return Scene(
        scene_id=scene_id,
        trigger=TriggerType.impression,
        occurred_at="2026-10-20T12:10:00+08:00",
        location=Location(lng=121.47, lat=31.23, district="南京西路"),
        radius_m=3000,
        meal_period="lunch",
    )


def test_hard_filter_drops_closed_blocked_dietary_price():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    snap = cat.get_user("u_dietary")
    assert snap is not None
    out = eng.run(snap, _scene())
    dropped_reasons = {d.merchant_id: d.reason for d in out.dropped}
    assert dropped_reasons.get("m_closed_1") == "closed"
    assert dropped_reasons.get("m_blocked_demo") == "blocked"
    assert dropped_reasons.get("m_spicy_1") == "dietary"
    kept_ids = {c.merchant_id for c in out.candidates}
    assert "m_spicy_1" not in kept_ids
    assert "m_blocked_demo" not in kept_ids
    assert "m_closed_1" not in kept_ids


def test_budget_user_drops_expensive():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    snap = cat.get_user("u_budget")
    out = eng.run(snap, _scene("s_budget"))
    kept = {c.merchant_id for c in out.candidates}
    assert "m_expensive_1" not in kept


def test_noodle_regular_keeps_noodle():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    snap = cat.get_user("u_noodle_regular")
    out = eng.run(snap, _scene("s_noodle"))
    assert out.candidates
    assert any(c.category == "面食" for c in out.candidates[:3])
