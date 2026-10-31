import tempfile
from pathlib import Path

from rec_agent.catalog import Catalog
from rec_agent.config import Settings
from rec_agent.models import CreateTaskRequest, Location, Scene, TriggerType
from rec_agent.service import RecommendationService
from rec_agent.store import Store
from rec_agent.testing.fake_llm import FakeLLMServer


def _scene(scene_id: str) -> Scene:
    return Scene(
        scene_id=scene_id,
        trigger=TriggerType.impression,
        occurred_at="2026-10-20T12:10:00+08:00",
        location=Location(lng=121.47, lat=31.23, district="南京西路"),
        radius_m=3000,
        meal_period="lunch",
    )


def test_real_path_direct_and_react_via_fake_openai():
    server = FakeLLMServer()
    server.start()
    try:
        for user_id, scene_id, force in [
            ("u_noodle_regular", "fake_direct", "DIRECT"),
            ("u_conflict", "fake_react", "REACT"),
        ]:
            tmp = Path(tempfile.mkdtemp()) / f"{force}.db"
            settings = Settings(
                use_mock_llm=False,
                llm_api_key="fake",
                llm_base_url=server.base_url,
                llm_model="fake-smoke",
                force_route=force,
                cooldown_minutes=0,
                scene_dedupe_minutes=0,
            )
            svc = RecommendationService(
                catalog=Catalog(), store=Store(tmp), settings=settings
            )
            res = svc.create_task(
                CreateTaskRequest(
                    tenant_id="t_demo", user_id=user_id, scene=_scene(scene_id)
                )
            )
            assert res.status.value in {"succeeded", "degraded"}, res
            assert res.merchants, res
            assert res.metrics.get("use_mock_llm") is False
            assert (res.metrics.get("model_calls") or 0) >= 1
            assert res.source == "agent"
            trace = svc.get_trace(res.task_id)
            tools = [s.get("tool_name") for s in trace.get("steps") or []]
            assert "submit_recommendation" in tools
            if force == "REACT":
                assert "get_exposure_history" in tools
    finally:
        server.stop()
