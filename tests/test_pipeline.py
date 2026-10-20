from pathlib import Path

from rec_agent.catalog import Catalog
from rec_agent.models import CreateTaskRequest, FeedbackRequest, Location, Scene, TriggerType
from rec_agent.service import RecommendationService
from rec_agent.store import Store


def _req(user_id: str, scene_id: str, occurred_at: str = "2026-10-20T12:10:00+08:00") -> CreateTaskRequest:
    return CreateTaskRequest(
        tenant_id="t_demo",
        user_id=user_id,
        scene=Scene(
            scene_id=scene_id,
            trigger=TriggerType.impression,
            occurred_at=occurred_at,
            location=Location(lng=121.47, lat=31.23, district="南京西路"),
            radius_m=3000,
            meal_period="lunch",
        ),
    )


def test_end_to_end_noodle_regular(tmp_path: Path):
    svc = RecommendationService(catalog=Catalog(), store=Store(tmp_path / "t.db"))
    result = svc.create_task(_req("u_noodle_regular", "s_e2e_1"))
    assert result.status.value in {"succeeded", "degraded"}
    assert result.merchants
    assert all(m.products for m in result.merchants)


def test_opt_out_skipped(tmp_path: Path):
    svc = RecommendationService(catalog=Catalog(), store=Store(tmp_path / "t.db"))
    result = svc.create_task(_req("u_opt_out", "s_e2e_opt"))
    assert result.status.value == "skipped"
    assert result.decision_reason_code == "not_authorized"


def test_idempotent(tmp_path: Path):
    svc = RecommendationService(catalog=Catalog(), store=Store(tmp_path / "t.db"))
    a = svc.create_task(_req("u_noodle_regular", "s_idem"))
    b = svc.create_task(_req("u_noodle_regular", "s_idem"))
    assert a.task_id == b.task_id


def test_not_interested_filters_next(tmp_path: Path):
    cat = Catalog()
    svc = RecommendationService(catalog=cat, store=Store(tmp_path / "t.db"))
    first = svc.create_task(_req("u_noodle_regular", "s_ni_1"))
    assert first.merchants
    mid = first.merchants[0].merchant_id
    fb = svc.feedback(first.task_id, FeedbackRequest(event_type="not_interested", merchant_id=mid))
    assert fb["ok"]
    second = svc.create_task(_req("u_noodle_regular", "s_ni_2"))
    ids = {m.merchant_id for m in second.merchants}
    assert mid not in ids


def test_conflict_user_may_prefer_noodle(tmp_path: Path):
    svc = RecommendationService(catalog=Catalog(), store=Store(tmp_path / "t.db"))
    result = svc.create_task(_req("u_conflict", "s_conflict_1"))
    assert result.status.value in {"succeeded", "degraded"}
    if result.route_mode and result.route_mode.value == "REACT":
        assert result.metrics.get("model_calls", 0) >= 1
