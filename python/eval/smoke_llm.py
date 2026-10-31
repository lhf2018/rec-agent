"""Smoke the real LLM tool-calling path.

Default: local FakeOpenAI server (no API key).
Real provider: reads key from G:\\llm apikey.txt (outside repo) via --real.

  python eval/smoke_llm.py
  python eval/smoke_llm.py --real
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rec_agent.catalog import Catalog
from rec_agent.config import Settings, live_llm_settings
from rec_agent.models import CreateTaskRequest, Location, Scene, TriggerType
from rec_agent.secrets import DEFAULT_LLM_API_KEY_FILE
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


def _run_case(svc: RecommendationService, user_id: str, scene_id: str, force: str) -> dict:
    res = svc.create_task(
        CreateTaskRequest(tenant_id="t_demo", user_id=user_id, scene=_scene(scene_id))
    )
    trace = svc.get_trace(res.task_id)
    tools = [s.get("tool_name") for s in (trace.get("steps") or [])]
    return {
        "user_id": user_id,
        "force": force,
        "status": res.status.value,
        "route_mode": res.route_mode.value if res.route_mode else None,
        "source": res.source,
        "fallback_reason": res.fallback_reason,
        "merchant_ids": [m.merchant_id for m in res.merchants],
        "reasons": [m.reason for m in res.merchants],
        "model_calls": res.metrics.get("model_calls"),
        "opportunity_score": res.metrics.get("opportunity_score"),
        "use_mock_llm": res.metrics.get("use_mock_llm"),
        "self_score": res.self_score,
        "tools": tools,
        "ok": res.status.value in {"succeeded", "degraded"}
        and bool(res.merchants)
        and res.source == "agent",
        "trace_errors": [
            s.get("error") or s.get("decision_reason_code")
            for s in (trace.get("steps") or [])
            if s.get("result_status") == "error"
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke LLM tool-calling path")
    parser.add_argument(
        "--real",
        action="store_true",
        help=f"Use real LLM; API key from {DEFAULT_LLM_API_KEY_FILE} (not in repo)",
    )
    parser.add_argument(
        "--key-file",
        default=str(DEFAULT_LLM_API_KEY_FILE),
        help="External API key file path",
    )
    parser.add_argument("--model", default="", help="Override model id")
    parser.add_argument("--base-url", default="", help="Override OpenAI-compatible base URL")
    args = parser.parse_args()

    fake: FakeLLMServer | None = None
    try:
        if args.real:
            overrides = {
                "llm_api_key_file": args.key_file,
            }
            if args.model:
                overrides["llm_model"] = args.model
            if args.base_url:
                overrides["llm_base_url"] = args.base_url
            probe = live_llm_settings(**overrides)
            if not probe.llm_ready():
                print(
                    f"ERROR: cannot load LLM key from {args.key_file!r} "
                    "(and no REC_AGENT_LLM_API_KEY). Key must stay outside the repo.",
                    file=sys.stderr,
                )
                return 2
            # Never print the key — only non-secret endpoints.
            print(
                f"Using real LLM base={probe.llm_base_url} model={probe.llm_model} "
                f"key_file={args.key_file!r} key_len={len(probe.llm_api_key)}"
            )
            llm_factory = lambda force: live_llm_settings(force_route=force, **overrides)
        else:
            fake = FakeLLMServer()
            fake.start()
            print(f"Using FakeOpenAI at {fake.base_url}")

            def llm_factory(force: str) -> Settings:
                return Settings(
                    use_mock_llm=False,
                    llm_api_key="fake-key",
                    llm_api_key_file="",
                    llm_base_url=fake.base_url,
                    llm_model="fake-smoke",
                    force_route=force,
                    cooldown_minutes=0,
                    scene_dedupe_minutes=0,
                    enable_llm=True,
                    enable_react=True,
                )

        rows = []
        cases = [
            ("u_noodle_regular", "smoke_direct", "DIRECT"),
            ("u_conflict", "smoke_react", "REACT"),
        ]
        for user_id, scene_id, force in cases:
            tmp = Path(tempfile.mkdtemp()) / f"smoke_{force}.db"
            settings = llm_factory(force)
            svc = RecommendationService(
                catalog=Catalog(), store=Store(tmp), settings=settings
            )
            row = _run_case(svc, user_id, scene_id, force)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False))

        failed = [r for r in rows if not r["ok"]]
        summary = {"passed": len(rows) - len(failed), "total": len(rows), "failed": failed}
        print(json.dumps(summary, ensure_ascii=False))
        return 1 if failed else 0
    finally:
        if fake is not None:
            fake.stop()


if __name__ == "__main__":
    raise SystemExit(main())
