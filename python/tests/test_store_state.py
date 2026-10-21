from datetime import timedelta
from pathlib import Path

from rec_agent.catalog import Catalog
from rec_agent.config import Settings
from rec_agent.models import CreateTaskRequest, FeedbackRequest, Location, Scene, TriggerType
from rec_agent.service import RecommendationService
from rec_agent.snapshot import SnapshotBuilder
from rec_agent.store import Store
from rec_agent.timeutil import to_utc_iso, utc_now


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


def test_not_interested_persists_with_ttl(tmp_path: Path):
    store = Store(tmp_path / "s.db")
    settings = Settings(cooldown_minutes=0, scene_dedupe_minutes=0, not_interested_ttl_days=30)
    svc = RecommendationService(catalog=Catalog(), store=store, settings=settings)
    first = svc.create_task(_req("u_noodle_regular", "s_store_1"))
    assert first.merchants
    mid = first.merchants[0].merchant_id
    assert svc.feedback(first.task_id, FeedbackRequest(event_type="not_interested", merchant_id=mid))["ok"]

    # new service instance, same store → still filtered
    svc2 = RecommendationService(catalog=Catalog(), store=store, settings=settings)
    second = svc2.create_task(_req("u_noodle_regular", "s_store_2"))
    assert mid not in {m.merchant_id for m in second.merchants}

    # expired TTL → merchant allowed again
    past = to_utc_iso(utc_now() - timedelta(days=1))
    store.upsert_not_interested("u_noodle_regular", mid, expires_at=past, created_at=past)
    snap = SnapshotBuilder(Catalog(), store, settings).build("u_noodle_regular", now=utc_now())
    assert mid not in snap.hard.not_interested_merchants


def test_freq_cap_uses_delivery_window(tmp_path: Path):
    store = Store(tmp_path / "f.db")
    settings = Settings(freq_cap_per_day=1, freq_window_hours=24, cooldown_minutes=0, scene_dedupe_minutes=0)
    svc = RecommendationService(catalog=Catalog(), store=store, settings=settings)
    a = svc.create_task(_req("u_noodle_regular", "s_freq_1"))
    assert a.status.value in {"succeeded", "degraded"}
    b = svc.create_task(_req("u_noodle_regular", "s_freq_2"))
    assert b.status.value == "skipped"
    assert b.decision_reason_code == "freq_cap"


def test_scene_dedupe_separate_from_freq(tmp_path: Path):
    store = Store(tmp_path / "d.db")
    settings = Settings(
        freq_cap_per_day=100,
        cooldown_minutes=0,
        scene_dedupe_minutes=60,
    )
    svc = RecommendationService(catalog=Catalog(), store=store, settings=settings)
    a = svc.create_task(_req("u_noodle_regular", "s_dup_scene"))
    assert a.status.value in {"succeeded", "degraded"}
    # New scene_id avoids idempotency; scene_key already recorded for previous only.
    b = svc.create_task(_req("u_noodle_regular", "s_other_scene"))
    assert b.status.value in {"succeeded", "degraded", "empty"}
    # Same scene_key within window → opportunity suppress (new idempotency key needs different trigger)
    # Force via snapshot signal after record_scene_seen
    store.record_scene_seen("u_noodle_regular", "impression:s_seen_x")
    snap = SnapshotBuilder(Catalog(), store, settings).build(
        "u_noodle_regular", now=utc_now(), scene_key="impression:s_seen_x"
    )
    assert snap.runtime.scene_seen_recently is True
    from rec_agent.policy import Policy

    decision = Policy(settings).check_opportunity(snap, _req("u_noodle_regular", "s_seen_x").scene)
    assert decision.result.value == "suppress"
    assert decision.reason_code == "duplicate_scene_suppress"


def test_exposure_history_returns_merchant_ids(tmp_path: Path):
    store = Store(tmp_path / "e.db")
    settings = Settings(cooldown_minutes=0, scene_dedupe_minutes=0)
    svc = RecommendationService(catalog=Catalog(), store=store, settings=settings)
    first = svc.create_task(_req("u_noodle_regular", "s_expo_1"))
    assert first.merchants
    mid = first.merchants[0].merchant_id
    items = store.list_exposure_merchants(
        "u_noodle_regular", since=utc_now() - timedelta(hours=72), limit=10
    )
    assert any(i.merchant_id == mid for i in items)
    assert all(i.merchant_id.startswith("m_") for i in items)
