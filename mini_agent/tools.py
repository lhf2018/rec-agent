from __future__ import annotations

from typing import Any

from mini_agent.catalog import Catalog
from mini_agent.engine import RecommendationEngine
from mini_agent.models import CandidateMerchant, Scene, UserSnapshot


class ToolExecutor:
    def __init__(self, catalog: Catalog, engine: RecommendationEngine) -> None:
        self.catalog = catalog
        self.engine = engine
        self.supplement_used = False

    def reset(self) -> None:
        self.supplement_used = False

    def execute(
        self,
        name: str,
        args: dict[str, Any],
        *,
        allowed_ids: set[str],
        snapshot: UserSnapshot,
        scene: Scene,
        candidates: list[CandidateMerchant],
    ) -> dict[str, Any]:
        if name == "get_merchant_detail":
            mid = args.get("merchant_id")
            if mid not in allowed_ids:
                return {"ok": False, "error_code": "not_in_allowed_set", "message": "merchant not allowed"}
            m = self.catalog.get_merchant(mid)
            if not m:
                return {"ok": False, "error_code": "not_found", "message": "missing"}
            cand = next((c for c in candidates if c.merchant_id == mid), None)
            return {
                "ok": True,
                "evidence_id": f"detail_{mid}",
                "data": {
                    "merchant_id": mid,
                    "name": m.name,
                    "category": m.category,
                    "avg_price": m.avg_price,
                    "distance_m": cand.distance_m if cand else None,
                    "is_open": m.is_open,
                    "tags": m.tags,
                    "score": m.score,
                },
            }
        if name == "get_product_detail":
            pid = args.get("product_id")
            for c in candidates:
                for p in c.products:
                    if p.product_id == pid:
                        if c.merchant_id not in allowed_ids:
                            return {"ok": False, "error_code": "not_in_allowed_set", "message": "not allowed"}
                        raw = next((x for x in self.catalog.get_merchant(c.merchant_id).products if x.product_id == pid), None)
                        return {
                            "ok": True,
                            "evidence_id": f"prod_{pid}",
                            "data": {
                                "product_id": pid,
                                "merchant_id": c.merchant_id,
                                "name": p.name,
                                "price": p.price,
                                "in_stock": p.in_stock,
                                "dietary_tags": p.dietary_tags,
                                "untrusted_text": getattr(raw, "description", "") if raw else "",
                            },
                        }
            return {"ok": False, "error_code": "not_found", "message": "product missing"}
        if name == "compare_candidates":
            left, right = args.get("left_id"), args.get("right_id")
            aspects = args.get("aspects") or ["price", "distance", "category"]
            if left not in allowed_ids or right not in allowed_ids:
                return {"ok": False, "error_code": "not_in_allowed_set", "message": "ids not allowed"}
            lc = next((c for c in candidates if c.merchant_id == left), None)
            rc = next((c for c in candidates if c.merchant_id == right), None)
            if not lc or not rc:
                return {"ok": False, "error_code": "not_found", "message": "candidate missing"}
            data = {"left_id": left, "right_id": right, "aspects": {}}
            for a in aspects:
                if a == "price":
                    data["aspects"]["price"] = {"left": lc.avg_price, "right": rc.avg_price}
                elif a == "distance":
                    data["aspects"]["distance"] = {"left": lc.distance_m, "right": rc.distance_m}
                elif a == "category":
                    data["aspects"]["category"] = {"left": lc.category, "right": rc.category}
                elif a == "open_now":
                    data["aspects"]["open_now"] = {"left": lc.is_open, "right": rc.is_open}
                elif a == "score":
                    data["aspects"]["score"] = {"left": lc.score, "right": rc.score}
            return {"ok": True, "evidence_id": f"cmp_{left}_{right}", "data": data}
        if name == "get_exposure_history":
            limit = int(args.get("limit") or 10)
            items = [
                {"merchant_id": mid, "event_type": "impression"}
                for mid in snapshot.recent_impressions[:limit]
            ]
            for mid in snapshot.hard.not_interested_merchants[:limit]:
                items.append({"merchant_id": mid, "event_type": "not_interested"})
            return {"ok": True, "evidence_id": "expo_1", "data": {"items": items}}
        if name == "supplement_recall":
            if self.supplement_used:
                return {"ok": False, "error_code": "supplement_already_used", "message": "already used"}
            strategy = args.get("strategy") or "widen_radius"
            if strategy not in {"widen_radius", "adjacent_category", "trending"}:
                return {"ok": False, "error_code": "invalid_argument", "message": "bad strategy"}
            self.supplement_used = True
            existing = {c.merchant_id for c in candidates}
            out = self.engine.supplement(snapshot, scene, strategy, existing)
            return {
                "ok": True,
                "evidence_id": "supp_batch",
                "data": {
                    "strategy": strategy,
                    "new_candidates": [c.model_dump() for c in out.candidates],
                    "dropped": [d.model_dump() for d in out.dropped],
                    "candidate_set_version": out.candidate_set_version,
                },
            }
        return {"ok": False, "error_code": "invalid_argument", "message": f"unknown tool {name}"}
