from datetime import timedelta
from pathlib import Path

from rec_agent.catalog import Catalog
from rec_agent.config import Settings
from rec_agent.engine import RecommendationEngine
from rec_agent.models import Location, RouteMode, Scene, TriggerType
from rec_agent.policy import Policy
from rec_agent.runtime.budget import DeadlineBudget
from rec_agent.runtime.guard import Guard
from rec_agent.runtime.loop import AgentLoop
from rec_agent.service import RecommendationService
from rec_agent.store import Store
from rec_agent.timeutil import utc_now
from rec_agent.tools import ToolExecutor


def _scene(scene_id: str = "s_dl") -> Scene:
    return Scene(
        scene_id=scene_id,
        trigger=TriggerType.impression,
        occurred_at="2026-10-20T12:10:00+08:00",
        location=Location(lng=121.47, lat=31.23, district="南京西路"),
        radius_m=3000,
        meal_period="lunch",
    )


def test_budget_rejects_when_deadline_passed():
    settings = Settings(model_min_remaining_ms=500)
    past = utc_now() - timedelta(seconds=1)
    assert DeadlineBudget(past, settings).allow_model() is False
    assert DeadlineBudget(past, settings).allow_tool(2000) is False


def test_loop_deadline_exhausted_no_model_calls():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    snap = cat.get_user("u_noodle_regular")
    out = eng.run(snap, _scene())
    loop = AgentLoop(ToolExecutor(cat, eng), Guard(), Settings())
    past = utc_now() - timedelta(seconds=1)
    result = loop.run(RouteMode.direct, snap, _scene(), out.candidates, deadline_at=past)
    assert result["ok"] is False
    assert result["fallback_reason"] == "deadline_exhausted"
    assert result["model_calls"] == 0


def test_service_routes_fallback_when_budget_exhausted(tmp_path: Path):
    settings = Settings(
        task_timeout_ms=0,
        model_min_remaining_ms=500,
        cooldown_minutes=0,
        scene_dedupe_minutes=0,
        enable_llm=True,
    )
    # timeout 0 → deadline already in the past at routing
    svc = RecommendationService(catalog=Catalog(), store=Store(tmp_path / "d.db"), settings=settings)
    from rec_agent.models import CreateTaskRequest

    res = svc.create_task(
        CreateTaskRequest(tenant_id="t_demo", user_id="u_noodle_regular", scene=_scene("s_fb"))
    )
    # With budget_ok false at route, FALLBACK → degraded baseline (still hard-filtered)
    assert res.status.value == "degraded"
    assert res.route_mode == RouteMode.fallback or res.source == "ranker_fallback"
    assert res.merchants
    assert res.metrics.get("model_calls", 0) == 0


def test_policy_budget_ok_uses_model_min_remaining():
    settings = Settings(model_min_remaining_ms=1000)
    policy = Policy(settings)
    soon = utc_now() + timedelta(milliseconds=200)
    assert policy.budget_ok(soon) is False
    later = utc_now() + timedelta(seconds=5)
    assert policy.budget_ok(later) is True
