from pathlib import Path

from rec_agent.catalog import Catalog
from rec_agent.config import Settings
from rec_agent.models import CreateTaskRequest, Location, Scene, TriggerType
from rec_agent.service import RecommendationService
from rec_agent.store import Store


def _req(user_id: str, scene_id: str) -> CreateTaskRequest:
    return CreateTaskRequest(
        tenant_id="t_demo",
        user_id=user_id,
        scene=Scene(
            scene_id=scene_id,
            trigger=TriggerType.impression,
            occurred_at="2026-10-20T12:10:00+08:00",
            location=Location(lng=121.47, lat=31.23, district="南京西路"),
            radius_m=3000,
            meal_period="lunch",
        ),
    )


def test_rule_rerank_differs_from_engine_on_conflict(tmp_path: Path):
    engine = RecommendationService(
        catalog=Catalog(),
        store=Store(tmp_path / "e.db"),
        settings=Settings(force_route="RULE_ENGINE", enable_llm=False, cooldown_minutes=0, scene_dedupe_minutes=0),
    )
    rerank = RecommendationService(
        catalog=Catalog(),
        store=Store(tmp_path / "r.db"),
        settings=Settings(force_route="RULE_RERANK", enable_llm=False, cooldown_minutes=0, scene_dedupe_minutes=0),
    )
    a = engine.create_task(_req("u_conflict", "eval_eng"))
    b = rerank.create_task(_req("u_conflict", "eval_rr"))
    assert a.merchants and b.merchants
    assert a.source == "rule_engine"
    assert b.source == "rule_rerank"
    # engine head is burger; rule rerank prefers short-term 面食
    assert a.merchants[0].merchant_id != b.merchants[0].merchant_id


def test_unified_force_react_reports_tool_evidence(tmp_path: Path):
    svc = RecommendationService(
        catalog=Catalog(),
        store=Store(tmp_path / "x.db"),
        settings=Settings(force_route="REACT", cooldown_minutes=0, scene_dedupe_minutes=0),
    )
    res = svc.create_task(_req("u_conflict", "eval_react"))
    assert res.status.value in {"succeeded", "degraded"}
    assert res.merchants
    assert any(eid.startswith("cmp_") for m in res.merchants for eid in m.evidence_ids)
