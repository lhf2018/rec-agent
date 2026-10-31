import tempfile
from pathlib import Path

from rec_agent.catalog import Catalog
from rec_agent.config import Settings
from rec_agent.engine import RecommendationEngine
from rec_agent.models import (
    CreateTaskRequest,
    Location,
    RecommendedMerchant,
    RecommendedProduct,
    RouteMode,
    Scene,
    TriggerType,
)
from rec_agent.runtime.guard import Guard
from rec_agent.runtime.loop import AgentLoop
from rec_agent.service import RecommendationService
from rec_agent.store import Store
from rec_agent.tools import ToolExecutor


def _scene(scene_id: str = "proto_1") -> Scene:
    return Scene(
        scene_id=scene_id,
        trigger=TriggerType.impression,
        occurred_at="2026-10-20T12:10:00+08:00",
        location=Location(lng=121.47, lat=31.23, district="南京西路"),
        radius_m=3000,
        meal_period="lunch",
    )


def test_react_uses_exposure_product_and_submit_tools():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    snap = cat.get_user("u_conflict")
    out = eng.run(snap, _scene("tools_obs"))
    loop = AgentLoop(ToolExecutor(cat, eng), Guard())
    result = loop.run(
        RouteMode.react,
        snap,
        _scene("tools_obs"),
        out.candidates,
        candidate_set_version=out.candidate_set_version,
        need_compare=True,
    )
    assert result["ok"] is True
    tools = [s.get("tool_name") for s in result["steps"]]
    assert "get_exposure_history" in tools
    assert "compare_candidates" in tools
    assert "get_product_detail" in tools
    assert "submit_recommendation" in tools
    assert any(s.get("tool_name") == "submit_recommendation" for s in result["steps"])


def test_confidence_threshold_triggers_arbitration():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    snap = cat.get_user("u_noodle_regular")
    out = eng.run(snap, _scene("conf"))
    settings = Settings(use_mock_llm=True, display_confidence_threshold=0.99, max_submit_failures=1)
    loop = AgentLoop(ToolExecutor(cat, eng), Guard(settings), settings)
    # Patch finalize path via direct mode: mock self_score 0.75 < 0.99
    result = loop.run(
        RouteMode.direct,
        snap,
        _scene("conf"),
        out.candidates,
        candidate_set_version=out.candidate_set_version,
        soft_rerank=True,
    )
    assert result["ok"] is False
    assert result.get("fallback_reason") == "submit_arbitration"
    assert any("confidence_below_threshold" in e for e in result["errors"])


def test_submit_arbitration_after_repeated_guard_reject(monkeypatch):
    cat = Catalog()
    eng = RecommendationEngine(cat)
    snap = cat.get_user("u_noodle_regular")
    out = eng.run(snap, _scene("arb"))
    settings = Settings(use_mock_llm=True, max_submit_failures=2, react_max_steps=8)
    loop = AgentLoop(ToolExecutor(cat, eng), Guard(settings), settings)

    bad = [
        RecommendedMerchant(
            merchant_id="not_in_set",
            name="x",
            reason="bad",
            evidence_ids=["cand_x"],
            products=[
                RecommendedProduct(
                    product_id="p_x", name="x", reason="x", evidence_ids=["prod_x"]
                )
            ],
        )
    ]
    monkeypatch.setattr(loop, "_mock_select", lambda *a, **k: bad)
    result = loop.run(
        RouteMode.react,
        snap,
        _scene("arb"),
        out.candidates,
        candidate_set_version=out.candidate_set_version,
        need_compare=False,
        need_freshness=False,
        supplement_needed=False,
    )
    assert result["ok"] is False
    assert result.get("fallback_reason") == "submit_arbitration"


def test_real_llm_without_config_falls_back():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    snap = cat.get_user("u_noodle_regular")
    out = eng.run(snap, _scene("llm_cfg"))
    settings = Settings(use_mock_llm=False, llm_api_key="", llm_base_url="", llm_model="gpt-x")
    loop = AgentLoop(ToolExecutor(cat, eng), Guard(settings), settings)
    result = loop.run(
        RouteMode.direct,
        snap,
        _scene("llm_cfg"),
        out.candidates,
        candidate_set_version=out.candidate_set_version,
    )
    assert result["ok"] is False
    assert result.get("fallback_reason") == "llm_not_configured"


def test_opportunity_events_recorded():
    tmp = Path(tempfile.mkdtemp()) / "opp.db"
    store = Store(tmp)
    svc = RecommendationService(
        catalog=Catalog(),
        store=store,
        settings=Settings(cooldown_minutes=0, scene_dedupe_minutes=0),
    )
    svc.create_task(
        CreateTaskRequest(
            tenant_id="t_demo",
            user_id="u_noodle_regular",
            scene=_scene("opp_ok"),
        )
    )
    events = store.list_opportunity_events("u_noodle_regular")
    assert events
    assert events[0]["result"] == "recommend_now"
    assert events[0]["reason_code"] == "opportunity_ok"
