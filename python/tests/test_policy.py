from rec_agent.catalog import Catalog
from rec_agent.config import Settings
from rec_agent.engine import RecommendationEngine
from rec_agent.models import Location, RuntimeSignals, Scene, TriggerType
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
    # 23:30 +08:00 → local hour 23 in DND
    r = policy.check_eligibility(snap, _scene("2026-10-20T23:30:00+08:00"))
    assert not r.ok
    assert r.reason_code == "dnd_window"


def test_opportunity_suppress_duplicate_scene():
    cat = Catalog()
    policy = Policy()
    snap = cat.get_user("u_noodle_regular")
    assert snap is not None
    snap.runtime = RuntimeSignals(scene_seen_recently=True)
    r = policy.check_opportunity(snap, _scene(scene_id="s_dup"))
    assert r.result.value == "suppress"
    assert r.reason_code == "duplicate_scene_suppress"


def test_route_conflict_to_react():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    policy = Policy()
    snap = cat.get_user("u_conflict")
    out = eng.run(snap, _scene("s_conflict"))
    decision = policy.route(out, enable_llm=True, enable_react=True)
    if out.intent_conflict:
        assert decision.mode.value == "REACT"
    else:
        assert decision.mode.value in {"REACT", "RULE_ENGINE", "DIRECT"}


def test_eligibility_cooldown():
    cat = Catalog()
    policy = Policy(Settings(cooldown_minutes=30))
    snap = cat.get_user("u_noodle_regular")
    assert snap is not None
    # scene 12:10+08 == 04:10Z; last success 15 minutes earlier → still in 30m cooldown
    snap.runtime = RuntimeSignals(last_success_at="2026-10-20T03:55:00Z")
    r = policy.check_eligibility(snap, _scene("2026-10-20T12:10:00+08:00"))
    assert not r.ok
    assert r.reason_code == "cooldown"


def test_eligibility_freq_cap_from_runtime():
    cat = Catalog()
    policy = Policy(Settings(freq_cap_per_day=2))
    snap = cat.get_user("u_noodle_regular")
    assert snap is not None
    snap.runtime = RuntimeSignals(delivery_count_in_window=2)
    r = policy.check_eligibility(snap, _scene())
    assert not r.ok
    assert r.reason_code == "freq_cap"


def test_route_fallback_when_budget_exhausted():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    policy = Policy()
    snap = cat.get_user("u_noodle_regular")
    out = eng.run(snap, _scene())
    decision = policy.route(out, budget_ok=False)
    assert decision.mode.value == "FALLBACK"
