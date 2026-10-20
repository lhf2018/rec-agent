from __future__ import annotations

import re
from dataclasses import dataclass, field

from rec_agent.config import Settings, get_settings
from rec_agent.models import (
    CandidateMerchant,
    RecommendedMerchant,
    RecommendedProduct,
)


@dataclass
class Evidence:
    evidence_id: str
    facts: dict
    source: str = ""
    expires_at: str = ""


@dataclass
class GuardResult:
    ok: bool
    merchants: list[RecommendedMerchant] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    soft_dropped: list[str] = field(default_factory=list)


class Guard:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def build_evidence_index(self, candidates: list[CandidateMerchant]) -> dict[str, Evidence]:
        idx: dict[str, Evidence] = {}
        for c in candidates:
            eid = c.evidence_id or f"cand_{c.merchant_id}"
            idx[eid] = Evidence(
                evidence_id=eid,
                source=c.provenance,
                expires_at=c.expires_at,
                facts={
                    "merchant_id": c.merchant_id,
                    "category": c.category,
                    "avg_price": c.avg_price,
                    "distance_m": c.distance_m,
                    "is_open": c.is_open,
                    "name": c.name,
                },
            )
            for p in c.products:
                peid = f"prod_{p.product_id}"
                idx[peid] = Evidence(
                    evidence_id=peid,
                    source=c.provenance,
                    expires_at=c.expires_at,
                    facts={
                        "product_id": p.product_id,
                        "merchant_id": c.merchant_id,
                        "price": p.price,
                        "in_stock": p.in_stock,
                        "dietary_tags": p.dietary_tags,
                        "name": p.name,
                    },
                )
        idx["snap_history"] = Evidence(
            evidence_id="snap_history",
            facts={"type": "snapshot"},
            source="snapshot",
        )
        idx["snap_price_band"] = Evidence(
            evidence_id="snap_price_band",
            facts={"type": "price_band"},
            source="snapshot",
        )
        return idx

    def validate_submission(
        self,
        merchants: list[RecommendedMerchant],
        allowed_ids: set[str],
        candidates_by_id: dict[str, CandidateMerchant],
        evidence_index: dict[str, Evidence],
        decision: str = "recommend",
    ) -> GuardResult:
        if decision == "skip":
            return GuardResult(ok=True, merchants=[])

        errors: list[str] = []
        kept: list[RecommendedMerchant] = []

        if not merchants:
            return GuardResult(ok=False, errors=["empty_merchants"])
        if len(merchants) > self.settings.max_merchants:
            errors.append("too_many_merchants")
            merchants = merchants[: self.settings.max_merchants]

        for m in merchants:
            if m.merchant_id not in allowed_ids:
                errors.append(f"not_in_allowed:{m.merchant_id}")
                continue
            cand = candidates_by_id.get(m.merchant_id)
            if not cand:
                errors.append(f"missing_candidate:{m.merchant_id}")
                continue
            if not m.reason:
                errors.append(f"empty_reason:{m.merchant_id}")
                continue
            for eid in m.evidence_ids:
                if eid not in evidence_index:
                    errors.append(f"missing_evidence:{eid}")
                    break
            else:
                fact_err = self._reason_fact_mismatch(m.reason, m.evidence_ids, evidence_index)
                if fact_err:
                    errors.append(fact_err)
                    continue

            products: list[RecommendedProduct] = []
            for p in m.products[: self.settings.max_products_per_merchant]:
                if p.product_id not in allowed_ids and not any(
                    cp.product_id == p.product_id for cp in cand.products
                ):
                    errors.append(f"product_not_allowed:{p.product_id}")
                    continue
                owner_ok = any(cp.product_id == p.product_id for cp in cand.products)
                if not owner_ok:
                    errors.append(f"product_wrong_merchant:{p.product_id}")
                    continue
                for eid in p.evidence_ids:
                    if eid not in evidence_index:
                        errors.append(f"missing_evidence:{eid}")
                        break
                else:
                    products.append(
                        RecommendedProduct(
                            product_id=p.product_id,
                            name=p.name or next(cp.name for cp in cand.products if cp.product_id == p.product_id),
                            reason=p.reason,
                            evidence_ids=p.evidence_ids,
                        )
                    )
            if not products:
                errors.append(f"no_products:{m.merchant_id}")
                continue
            kept.append(
                RecommendedMerchant(
                    merchant_id=m.merchant_id,
                    name=m.name or cand.name,
                    reason=m.reason,
                    evidence_ids=m.evidence_ids,
                    products=products,
                )
            )

        # diversity hard cap
        kept, soft = self._diversity_trim(kept, candidates_by_id)
        if not kept:
            return GuardResult(ok=False, errors=errors or ["diversity_empty"], soft_dropped=soft)

        # hard errors that mention not_in_allowed etc. — if we still have kept, ok with soft drops
        hard = [e for e in errors if e.startswith("not_in_allowed") or e.startswith("missing_evidence") or e.startswith("reason_fact")]
        # allow partial success
        return GuardResult(ok=True, merchants=kept, errors=errors, soft_dropped=soft)

    def _diversity_trim(
        self,
        merchants: list[RecommendedMerchant],
        candidates_by_id: dict[str, CandidateMerchant],
    ) -> tuple[list[RecommendedMerchant], list[str]]:
        counts: dict[str, int] = {}
        out: list[RecommendedMerchant] = []
        dropped: list[str] = []
        for m in merchants:
            cat = candidates_by_id.get(m.merchant_id).category if m.merchant_id in candidates_by_id else ""
            if counts.get(cat, 0) >= self.settings.max_same_category:
                dropped.append(m.merchant_id)
                continue
            counts[cat] = counts.get(cat, 0) + 1
            out.append(m)
        return out, dropped

    def _reason_fact_mismatch(
        self, reason: str, evidence_ids: list[str], evidence_index: dict[str, Evidence]
    ) -> str | None:
        bag: dict = {}
        for eid in evidence_ids:
            bag.update(evidence_index[eid].facts)
        # prices
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*元", reason):
            val = float(match.group(1))
            price = bag.get("avg_price") or bag.get("price")
            if price is not None and abs(price - val) > max(5.0, price * 0.15):
                return "reason_fact_mismatch:price"
        for match in re.finditer(r"(\d+)\s*米", reason):
            val = float(match.group(1))
            dist = bag.get("distance_m")
            if dist is not None and abs(dist - val) > max(200.0, dist * 0.2):
                return "reason_fact_mismatch:distance"
        if "营业" in reason and bag.get("is_open") is False:
            return "reason_fact_mismatch:open"
        if "有库存" in reason and bag.get("in_stock") is False:
            return "reason_fact_mismatch:stock"
        return None

    def baseline_to_recommendations(
        self, baseline: list[CandidateMerchant]
    ) -> list[RecommendedMerchant]:
        out: list[RecommendedMerchant] = []
        for c in baseline[: self.settings.max_merchants]:
            if not c.products:
                continue
            p = c.products[0]
            out.append(
                RecommendedMerchant(
                    merchant_id=c.merchant_id,
                    name=c.name,
                    reason=f"与当前时段和距离匹配，距离约 {int(c.distance_m)} 米，排序靠前",
                    evidence_ids=[c.evidence_id or f"cand_{c.merchant_id}"],
                    products=[
                        RecommendedProduct(
                            product_id=p.product_id,
                            name=p.name,
                            reason=f"有库存，价格 {int(p.price)} 元",
                            evidence_ids=[f"prod_{p.product_id}"],
                        )
                    ],
                )
            )
        return out
