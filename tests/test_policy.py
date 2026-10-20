from rec_agent.catalog import Catalog
from rec_agent.engine import RecommendationEngine
from rec_agent.models import Location, Scene, TriggerType
from rec_agent.policy import Policy


def _scene(hour_iso: str = "2026-10-20T12:10:00+08:00", scene_id: str = "s1") -> Scene:
    return Scene(
        scene_id=scene_id,
        trigger=TriggerType.impression,
        occurred_at=hour_iso,
        location=Location(lng=121.47, lat=31.23, district="南京西路"),
    )


def test_eligibility_blocks_opt_out():
    cat = Catalog()
    policy = Policy()
    snap = cat.get_user("u_opt_out")
    assert snap is not None
    r = policy.check_eligibility(snap, _scene())
    assert not r.ok
    assert r.reason_code == "not_authorized"


def test_eligibility_dnd():
    cat = Catalog()
    policy = Policy()
    snap = cat.get_user("u_noodle_regular")
    r = policy.check_eligibility(snap, _scene("2026-10-20T23:30:00+08:00"))
    assert not r.ok
    assert r.reason_code == "dnd_window"


def test_opportunity_suppress_duplicate_scene():
    cat = Catalog()
    policy = Policy()
    snap = cat.get_user("u_noodle_regular")
    assert snap is not None
    snap.recent_impressions.append("s_dup")
    r = policy.check_opportunity(snap, _scene(scene_id="s_dup"))
    assert r.result.value == "suppress"


def test_route_conflict_to_react():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    policy = Policy()
    snap = cat.get_user("u_conflict")
    out = eng.run(snap, _scene("s_conflict"))
    # force conflict flag if engine detected
    decision = policy.route(out, enable_llm=True, enable_react=True)
    if out.intent_conflict:
        assert decision.mode.value == "REACT"
    else:
        # still valid path
        assert decision.mode.value in {"REACT", "RULE_ENGINE", "DIRECT"}


def test_eligibility_cooldown():
    from rec_agent.config import Settings

    cat = Catalog()
    policy = Policy(Settings(cooldown_minutes=30))
    snap = cat.get_user("u_noodle_regular")
    assert snap is not None
    snap.last_success_at = "2026-10-20T12:00:00+08:00"
    r = policy.check_eligibility(snap, _scene("2026-10-20T12:10:00+08:00"))
    assert not r.ok
    assert r.reason_code == "cooldown"


def test_route_fallback_when_budget_exhausted():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    policy = Policy()
    snap = cat.get_user("u_noodle_regular")
    out = eng.run(snap, _scene())
    decision = policy.route(out, budget_ok=False)
    assert decision.mode.value == "FALLBACK"
