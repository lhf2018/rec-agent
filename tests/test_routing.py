from rec_agent.catalog import Catalog
from rec_agent.config import Settings
from rec_agent.engine import RecommendationEngine
from rec_agent.models import EngineOutput, Location, RouteMode, Scene, TriggerType
from rec_agent.policy import Policy
from rec_agent.runtime.guard import Guard
from rec_agent.runtime.loop import AgentLoop
from rec_agent.tools import ToolExecutor


def _scene(scene_id: str = "r1") -> Scene:
    return Scene(
        scene_id=scene_id,
        trigger=TriggerType.impression,
        occurred_at="2026-10-20T12:10:00+08:00",
        location=Location(lng=121.47, lat=31.23, district="南京西路"),
        radius_m=3000,
        meal_period="lunch",
    )


def test_conflict_routes_to_react_with_compare():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    snap = cat.get_user("u_conflict")
    out = eng.run(snap, _scene("conflict_route"))
    assert out.intent_conflict is True
    assert out.need_compare is True
    decision = Policy().route(out)
    assert decision.mode == RouteMode.react
    assert decision.reason_code in {"need_compare", "intent_conflict", "react_gap"}


def test_soft_rerank_routes_to_direct():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    # gold + frequent → soft_rerank without conflict
    snap = cat.get_user("u_noodle_regular")
    out = eng.run(snap, _scene("soft_route"))
    assert out.soft_rerank is True
    assert out.intent_conflict is False
    assert out.need_compare is False
    decision = Policy().route(out)
    assert decision.mode == RouteMode.direct
    assert decision.reason_code == "soft_rerank"


def test_supplement_needed_routes_react():
    policy = Policy(Settings(supplement_min=100))
    out = EngineOutput(
        candidates=[],
        baseline=[],
        dropped=[],
        candidate_set_version="v",
        facts_complete=True,
        supplement_needed=True,
    )
    # empty candidates → skip first
    assert policy.route(out).mode == RouteMode.skip

    cat = Catalog()
    eng = RecommendationEngine(cat, Settings(supplement_min=100))
    snap = cat.get_user("u_noodle_regular")
    real = eng.run(snap, _scene("supp"))
    assert real.supplement_needed is True
    assert Policy(Settings(supplement_min=100)).route(real).mode == RouteMode.react


def test_compare_observation_changes_selection_and_cites_tool():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    snap = cat.get_user("u_conflict")
    out = eng.run(snap, _scene("cmp_obs"))
    loop = AgentLoop(ToolExecutor(cat, eng), Guard())
    result = loop.run(
        RouteMode.react,
        snap,
        _scene("cmp_obs"),
        out.candidates,
        candidate_set_version=out.candidate_set_version,
        need_compare=True,
        supplement_needed=False,
    )
    assert result["ok"] is True
    assert result["merchants"]
    # short-term 面食 should win over engine burger head
    assert result["merchants"][0].merchant_id != out.candidates[0].merchant_id or any(
        m.category == "面食"
        for m in out.candidates
        if m.merchant_id == result["merchants"][0].merchant_id
    )
    top = result["merchants"][0]
    assert any(eid.startswith("cmp_") for eid in top.evidence_ids)
    assert any(s.get("tool_name") == "compare_candidates" for s in result["steps"])
