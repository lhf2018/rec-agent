"""Supplement recall must bump version and still pass Guard (§7.3 / §11)."""

from datetime import timedelta
from pathlib import Path

from rec_agent.catalog import Catalog
from rec_agent.config import Settings
from rec_agent.engine import RecommendationEngine
from rec_agent.models import (
    CreateTaskRequest,
    Location,
    RouteMode,
    Scene,
    TriggerType,
)
from rec_agent.policy import Policy
from rec_agent.runtime.guard import Guard
from rec_agent.runtime.loop import AgentLoop
from rec_agent.service import RecommendationService
from rec_agent.store import Store
from rec_agent.tools import ToolExecutor
from rec_agent.timeutil import to_utc_iso, utc_now


def _scene(scene_id: str = "supp_1") -> Scene:
    return Scene(
        scene_id=scene_id,
        trigger=TriggerType.impression,
        occurred_at="2026-10-20T12:10:00+08:00",
        location=Location(lng=121.47, lat=31.23, district="南京西路"),
        radius_m=3000,
        meal_period="lunch",
    )


def test_supplement_rebuilds_evidence_and_succeeds():
    cat = Catalog()
    settings = Settings(supplement_min=100)
    eng = RecommendationEngine(cat, settings)
    snap = cat.get_user("u_budget")
    out = eng.run(snap, _scene("supp_loop"))
    assert out.supplement_needed is True

    loop = AgentLoop(ToolExecutor(cat, eng, settings=settings), Guard(settings), settings)
    result = loop.run(
        RouteMode.react,
        snap,
        _scene("supp_loop"),
        out.candidates,
        candidate_set_version=out.candidate_set_version,
        supplement_needed=True,
    )
    assert result["ok"] is True
    assert result["merchants"]
    assert any(s.get("tool_name") == "supplement_recall" for s in result["steps"])


def test_supplement_strategies_return_new_candidates():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    snap = cat.get_user("u_noodle_regular")
    scene = _scene("strat")
    base = eng.run(snap, scene)
    existing = {c.merchant_id for c in base.candidates[:1]}
    for strategy in ("widen_radius", "adjacent_category", "trending"):
        out = eng.supplement(snap, scene, strategy, existing)
        assert out.candidate_set_version.endswith("_supp")
        assert all(c.provenance == "supplement" for c in out.candidates)
        assert all(c.merchant_id not in existing for c in out.candidates)


def test_budget_user_end_to_end_succeeds(tmp_path: Path):
    svc = RecommendationService(
        catalog=Catalog(),
        store=Store(tmp_path / "t.db"),
        settings=Settings(cooldown_minutes=0, similar_suppress_minutes=0),
    )
    result = svc.create_task(
        CreateTaskRequest(
            tenant_id="t_demo",
            user_id="u_budget",
            scene=_scene("e2e_budget"),
        )
    )
    assert result.status.value in {"succeeded", "degraded"}
    assert result.merchants
    assert result.metrics.get("deadline_at")
    trace = svc.get_trace(result.task_id)
    summary = trace.get("trace_summary") or {}
    assert summary.get("deadline_at")
    assert "budget_exhausted" in summary


def test_need_freshness_routes_react_and_refreshes():
    cat = Catalog()
    settings = Settings(freshness_horizon_minutes=180)
    eng = RecommendationEngine(cat, settings)
    snap = cat.get_user("u_noodle_regular")
    out = eng.run(snap, _scene("fresh"))
    # Force near-expiry so need_freshness is true without changing horizon globally
    soon = to_utc_iso(utc_now() + timedelta(minutes=10))
    for c in out.candidates:
        c.expires_at = soon
    out.need_freshness = eng._need_freshness(out.candidates)
    assert out.need_freshness is True
    assert Policy(settings).route(out).mode == RouteMode.react

    loop = AgentLoop(ToolExecutor(cat, eng, settings=settings), Guard(settings), settings)
    result = loop.run(
        RouteMode.react,
        snap,
        _scene("fresh"),
        out.candidates,
        candidate_set_version=out.candidate_set_version,
        need_freshness=True,
        supplement_needed=False,
    )
    assert result["ok"] is True
    assert any(s.get("tool_name") == "get_merchant_detail" for s in result["steps"])


def test_submit_recommendation_tool_executable():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    tools = ToolExecutor(cat, eng)
    snap = cat.get_user("u_noodle_regular")
    out = eng.run(snap, _scene("submit_tool"))
    obs = tools.execute(
        "submit_recommendation",
        {"decision": "recommend", "merchants": [{"merchant_id": out.candidates[0].merchant_id}]},
        allowed_ids={c.merchant_id for c in out.candidates},
        snapshot=snap,
        scene=_scene("submit_tool"),
        candidates=out.candidates,
    )
    assert obs["ok"] is True
    assert obs["data"]["accepted"] is True
