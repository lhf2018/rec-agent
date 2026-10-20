from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rec_agent.catalog import Catalog
from rec_agent.config import Settings
from rec_agent.engine import RecommendationEngine
from rec_agent.models import CreateTaskRequest, Location, Scene, TriggerType
from rec_agent.policy import template_reason
from rec_agent.runtime.guard import Guard
from rec_agent.runtime.loop import AgentLoop
from rec_agent.service import RecommendationService
from rec_agent.store import Store
from rec_agent.tools import ToolExecutor


def _scene(scene_id: str) -> Scene:
    return Scene(
        scene_id=scene_id,
        trigger=TriggerType.impression,
        occurred_at="2026-10-20T12:10:00+08:00",
        location=Location(lng=121.47, lat=31.23, district="南京西路"),
        radius_m=3000,
        meal_period="lunch",
    )


def run_engine_arm(catalog: Catalog, user_id: str, scene_id: str) -> dict:
    snap = catalog.get_user(user_id)
    if not snap:
        return {"status": "failed", "merchants": []}
    if not snap.allow_personalized:
        return {"status": "skipped", "merchants": [], "reason": "not_authorized"}
    eng = RecommendationEngine(catalog)
    out = eng.run(snap, _scene(scene_id))
    guard = Guard()
    merchants = []
    for c in out.baseline:
        if not c.products:
            continue
        p = c.products[0]
        merchants.append(
            {
                "merchant_id": c.merchant_id,
                "category": c.category,
                "reason": template_reason(c),
                "products": [{"product_id": p.product_id}],
            }
        )
    return {
        "status": "succeeded" if merchants else "empty",
        "merchants": merchants,
        "model_calls": 0,
        "arm": "engine",
        "intent_conflict": out.intent_conflict,
    }


def run_agent_arm(catalog: Catalog, user_id: str, scene_id: str, mode: str) -> dict:
    settings = Settings(enable_llm=True, enable_react=True)
    # isolate store
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "eval.db"
    svc = RecommendationService(catalog=Catalog(), store=Store(tmp), settings=settings)
    # For direct/react we still go through service routing; to force arm we call loop manually when needed
    if mode == "direct":
        settings2 = Settings(enable_llm=True, enable_react=False)
        svc = RecommendationService(catalog=Catalog(), store=Store(tmp), settings=settings2)
        # with react disabled and facts complete -> rule_engine often; force via loop
        cat = Catalog()
        snap = cat.get_user(user_id)
        eng = RecommendationEngine(cat)
        out = eng.run(snap, _scene(scene_id))
        loop = AgentLoop(ToolExecutor(cat, eng), Guard(), settings2)
        from rec_agent.models import RouteMode

        ao = loop.run(RouteMode.direct, snap, _scene(scene_id), out.candidates)
        merchants = [
            {
                "merchant_id": m.merchant_id,
                "category": next((c.category for c in out.candidates if c.merchant_id == m.merchant_id), ""),
                "reason": m.reason,
                "products": [{"product_id": p.product_id} for p in m.products],
            }
            for m in ao.get("merchants", [])
        ]
        return {
            "status": "succeeded" if merchants else "empty",
            "merchants": merchants,
            "model_calls": ao.get("model_calls", 0),
            "arm": "direct",
        }

    if mode == "react":
        cat = Catalog()
        snap = cat.get_user(user_id)
        eng = RecommendationEngine(cat)
        out = eng.run(snap, _scene(scene_id))
        loop = AgentLoop(ToolExecutor(cat, eng), Guard())
        from rec_agent.models import RouteMode

        ao = loop.run(RouteMode.react, snap, _scene(scene_id), out.candidates)
        merchants = [
            {
                "merchant_id": m.merchant_id,
                "category": next((c.category for c in out.candidates if c.merchant_id == m.merchant_id), ""),
                "reason": m.reason,
                "products": [{"product_id": p.product_id} for p in m.products],
            }
            for m in ao.get("merchants", [])
        ]
        return {
            "status": "succeeded" if merchants else "empty",
            "merchants": merchants,
            "model_calls": ao.get("model_calls", 0),
            "arm": "react",
            "engine_top": out.candidates[0].merchant_id if out.candidates else None,
        }

    # default service path
    res = svc.create_task(
        CreateTaskRequest(tenant_id="t_demo", user_id=user_id, scene=_scene(scene_id))
    )
    return {
        "status": res.status.value,
        "merchants": [m.model_dump() for m in res.merchants],
        "model_calls": res.metrics.get("model_calls", 0),
        "arm": "service",
    }


def check_case(case: dict, result: dict) -> dict:
    ok = True
    notes = []
    status = result.get("status")
    if "expect_status" in case and status not in case["expect_status"]:
        ok = False
        notes.append(f"status={status}")
    mids = {m["merchant_id"] for m in result.get("merchants", [])}
    cats = {m.get("category") for m in result.get("merchants", [])}
    for mid in case.get("must_exclude_merchants", []):
        if mid in mids:
            ok = False
            notes.append(f"excluded_present:{mid}")
    for cat in case.get("must_include_category", []):
        if result.get("merchants") and cat not in cats:
            # soft for engine arm on conflict — still record
            if case.get("agent_gain_expected") and result.get("arm") == "engine":
                notes.append(f"engine_may_miss_category:{cat}")
            else:
                ok = False
                notes.append(f"missing_category:{cat}")
    gain = None
    if case.get("agent_gain_expected") and result.get("arm") == "react":
        engine_top = result.get("engine_top")
        gain = bool(mids) and (engine_top not in mids or any(c == "面食" for c in cats))
        if not gain:
            notes.append("no_agent_gain")
            ok = False
    return {"ok": ok, "notes": notes, "gain": gain}


def main() -> None:
    cases = json.loads((Path(__file__).parent / "cases.json").read_text(encoding="utf-8"))
    catalog = Catalog()
    rows = []
    for case in cases:
        for arm in case.get("force_arms", ["engine"]):
            if arm == "engine":
                result = run_engine_arm(catalog, case["user_id"], case["scene_id"] + "_" + arm)
            else:
                result = run_agent_arm(catalog, case["user_id"], case["scene_id"] + "_" + arm, arm)
            verdict = check_case(case, result)
            rows.append(
                {
                    "case": case["id"],
                    "arm": arm,
                    "status": result.get("status"),
                    "merchants": [m["merchant_id"] for m in result.get("merchants", [])],
                    "model_calls": result.get("model_calls", 0),
                    "pass": verdict["ok"],
                    "notes": verdict["notes"],
                    "gain": verdict["gain"],
                }
            )

    passed = sum(1 for r in rows if r["pass"])
    print(json.dumps({"passed": passed, "total": len(rows), "rows": rows}, ensure_ascii=False, indent=2))
    if passed < len(rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
