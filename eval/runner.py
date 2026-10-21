from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rec_agent.catalog import Catalog
from rec_agent.config import Settings
from rec_agent.models import CreateTaskRequest, Location, Scene, TriggerType
from rec_agent.service import RecommendationService
from rec_agent.store import Store


def _scene(scene_id: str) -> Scene:
    return Scene(
        scene_id=scene_id,
        trigger=TriggerType.impression,
        occurred_at="2026-10-20T12:10:00+08:00",
        location=Location(lng=121.47, lat=31.23, district="南京西路"),
        radius_m=3000,
        meal_period="lunch",
    )


def _settings_for_arm(arm: str) -> Settings:
    base = dict(cooldown_minutes=0, scene_dedupe_minutes=0, enable_react=True)
    if arm == "engine":
        return Settings(force_route="RULE_ENGINE", enable_llm=False, **base)
    if arm == "rule_rerank":
        return Settings(force_route="RULE_RERANK", enable_llm=False, **base)
    if arm == "direct":
        return Settings(force_route="DIRECT", enable_llm=True, **base)
    if arm == "react":
        return Settings(force_route="REACT", enable_llm=True, **base)
    raise ValueError(f"unknown arm {arm}")


def run_arm(user_id: str, scene_id: str, arm: str) -> dict:
    """Unified evaluation path: always RecommendationService + force_route."""
    tmp = Path(tempfile.mkdtemp()) / f"eval_{arm}.db"
    settings = _settings_for_arm(arm)
    svc = RecommendationService(catalog=Catalog(), store=Store(tmp), settings=settings)
    res = svc.create_task(
        CreateTaskRequest(tenant_id="t_demo", user_id=user_id, scene=_scene(scene_id))
    )
    merchants = []
    # attach category from catalog for assertions
    cat = Catalog()
    for m in res.merchants:
        mc = cat.get_merchant(m.merchant_id)
        merchants.append(
            {
                "merchant_id": m.merchant_id,
                "category": mc.category if mc else "",
                "reason": m.reason,
                "evidence_ids": list(m.evidence_ids),
                "products": [{"product_id": p.product_id} for p in m.products],
            }
        )
    return {
        "status": res.status.value,
        "merchants": merchants,
        "model_calls": res.metrics.get("model_calls", 0),
        "arm": arm,
        "source": res.source,
        "route_mode": res.route_mode.value if res.route_mode else None,
        "engine_baseline_ids": None,  # filled by caller when comparing
    }


def _ids(merchants: list[dict]) -> list[str]:
    return [m["merchant_id"] for m in merchants]


def check_case(case: dict, result: dict, baselines: dict) -> dict:
    ok = True
    notes: list[str] = []
    status = result.get("status")
    if "expect_status" in case and status not in case["expect_status"]:
        ok = False
        notes.append(f"status={status}")

    mids = set(_ids(result.get("merchants", [])))
    cats = {m.get("category") for m in result.get("merchants", [])}

    for mid in case.get("must_exclude_merchants", []):
        if mid in mids:
            ok = False
            notes.append(f"excluded_present:{mid}")

    for cat in case.get("must_include_category", []):
        if result.get("merchants") and cat not in cats:
            if case.get("agent_gain_expected") and result.get("arm") in {"engine", "rule_rerank"}:
                notes.append(f"baseline_may_miss_category:{cat}")
            else:
                ok = False
                notes.append(f"missing_category:{cat}")

    engine_ids = set(baselines.get("engine") or [])
    rerank_ids = set(baselines.get("rule_rerank") or [])
    selection_change = None
    quality_gain = None
    beats_rule_rerank = None

    if result.get("arm") in {"direct", "react"} and case.get("agent_gain_expected"):
        # selection_change: ≠ engine baseline set
        selection_change = bool(mids) and mids != engine_ids
        beats_rule_rerank = bool(mids) and bool(rerank_ids) and mids != rerank_ids
        # quality_gain: cites tool-written evidence ids
        quality_gain = False
        for m in result.get("merchants", []):
            for eid in m.get("evidence_ids") or []:
                if eid.startswith(("cmp_", "supp_", "detail_")):
                    quality_gain = True
                    break
        if case.get("expect_selection_change") and not selection_change:
            ok = False
            notes.append("no_selection_change")
        if case.get("expect_quality_gain") and not quality_gain:
            ok = False
            notes.append("no_quality_gain")
        if case.get("expect_beats_rule_rerank") and not beats_rule_rerank:
            ok = False
            notes.append("no_beats_rule_rerank")
        if not case.get("expect_selection_change") and not case.get("expect_quality_gain"):
            if not (selection_change or quality_gain):
                ok = False
                notes.append("no_agent_gain")

    return {
        "ok": ok,
        "notes": notes,
        "selection_change": selection_change,
        "quality_gain": quality_gain,
        "beats_rule_rerank": beats_rule_rerank,
    }


def main() -> None:
    cases = json.loads((Path(__file__).parent / "cases.json").read_text(encoding="utf-8"))
    rows = []
    for case in cases:
        arms = case.get("force_arms", ["engine"])
        # always compute engine (+ rule_rerank if needed) baselines for gain checks
        baselines: dict[str, list[str]] = {}
        need_baselines = any(a in {"direct", "react"} for a in arms) and case.get(
            "agent_gain_expected"
        )
        if need_baselines or "engine" in arms:
            eng = run_arm(case["user_id"], case["scene_id"] + "_base_engine", "engine")
            baselines["engine"] = _ids(eng.get("merchants", []))
        if need_baselines or "rule_rerank" in arms:
            rr = run_arm(case["user_id"], case["scene_id"] + "_base_rerank", "rule_rerank")
            baselines["rule_rerank"] = _ids(rr.get("merchants", []))

        for arm in arms:
            result = run_arm(case["user_id"], case["scene_id"] + "_" + arm, arm)
            if arm == "engine":
                baselines["engine"] = _ids(result.get("merchants", []))
            if arm == "rule_rerank":
                baselines["rule_rerank"] = _ids(result.get("merchants", []))
            verdict = check_case(case, result, baselines)
            rows.append(
                {
                    "case": case["id"],
                    "arm": arm,
                    "status": result.get("status"),
                    "merchants": _ids(result.get("merchants", [])),
                    "model_calls": result.get("model_calls", 0),
                    "pass": verdict["ok"],
                    "notes": verdict["notes"],
                    "selection_change": verdict["selection_change"],
                    "quality_gain": verdict["quality_gain"],
                    "beats_rule_rerank": verdict["beats_rule_rerank"],
                }
            )

    passed = sum(1 for r in rows if r["pass"])
    print(json.dumps({"passed": passed, "total": len(rows), "rows": rows}, ensure_ascii=False, indent=2))
    if passed < len(rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
