from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from rec_agent.config import Settings, get_settings
from rec_agent.models import (
    CandidateMerchant,
    RecommendedMerchant,
    RecommendedProduct,
)
from rec_agent.timeutil import to_utc, utc_now


MERCHANT_FACT_KEYS = frozenset(
    {"merchant_id", "category", "avg_price", "distance_m", "is_open", "name", "tags", "score"}
)
PRODUCT_FACT_KEYS = frozenset(
    {"product_id", "merchant_id", "price", "in_stock", "dietary_tags", "name"}
)


@dataclass
class Evidence:
    evidence_id: str
    facts: dict
    source: str = ""
    expires_at: str = ""
    subject_type: str = "merchant"  # merchant | product | tool | snapshot
    subject_id: str = ""
    candidate_set_version: str = ""


@dataclass
class GuardResult:
    ok: bool
    merchants: list[RecommendedMerchant] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    soft_dropped: list[str] = field(default_factory=list)


class Guard:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def build_evidence_index(
        self,
        candidates: list[CandidateMerchant],
        *,
        candidate_set_version: str = "",
    ) -> dict[str, Evidence]:
        idx: dict[str, Evidence] = {}
        for c in candidates:
            eid = c.evidence_id or f"cand_{c.merchant_id}"
            idx[eid] = Evidence(
                evidence_id=eid,
                source=c.provenance,
                expires_at=c.expires_at,
                subject_type="merchant",
                subject_id=c.merchant_id,
                candidate_set_version=candidate_set_version or "",
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
                    subject_type="product",
                    subject_id=p.product_id,
                    candidate_set_version=candidate_set_version or "",
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
            subject_type="snapshot",
            subject_id="history",
        )
        idx["snap_price_band"] = Evidence(
            evidence_id="snap_price_band",
            facts={"type": "price_band"},
            source="snapshot",
            subject_type="snapshot",
            subject_id="price_band",
        )
        return idx

    def validate_submission(
        self,
        merchants: list[RecommendedMerchant],
        allowed_ids: set[str],
        candidates_by_id: dict[str, CandidateMerchant],
        evidence_index: dict[str, Evidence],
        decision: str = "recommend",
        *,
        candidate_set_version: str = "",
        now: datetime | None = None,
    ) -> GuardResult:
        if decision == "skip":
            return GuardResult(ok=True, merchants=[])

        clock = now or utc_now()
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

            ev_err = self._check_evidence_refs(
                m.evidence_ids,
                evidence_index,
                expected_subject=("merchant", m.merchant_id),
                candidate_set_version=candidate_set_version,
                now=clock,
                allow_tool=True,
                allow_snapshot=True,
            )
            if ev_err:
                errors.append(ev_err)
                continue

            fact_err = self._reason_fact_mismatch_merchant(
                m.reason, m.evidence_ids, evidence_index, m.merchant_id
            )
            if fact_err:
                errors.append(fact_err)
                continue

            products: list[RecommendedProduct] = []
            for p in m.products[: self.settings.max_products_per_merchant]:
                owner_ok = any(cp.product_id == p.product_id for cp in cand.products)
                if not owner_ok:
                    errors.append(f"product_wrong_merchant:{p.product_id}")
                    continue
                if p.product_id not in allowed_ids and not owner_ok:
                    errors.append(f"product_not_allowed:{p.product_id}")
                    continue
                pev = self._check_evidence_refs(
                    p.evidence_ids,
                    evidence_index,
                    expected_subject=("product", p.product_id),
                    candidate_set_version=candidate_set_version,
                    now=clock,
                    allow_tool=False,
                    allow_snapshot=True,
                    merchant_id=m.merchant_id,
                )
                if pev:
                    errors.append(pev)
                    continue
                if p.reason:
                    perr = self._reason_fact_mismatch_product(
                        p.reason, p.evidence_ids, evidence_index, p.product_id
                    )
                    if perr:
                        errors.append(perr)
                        continue
                products.append(
                    RecommendedProduct(
                        product_id=p.product_id,
                        name=p.name
                        or next(cp.name for cp in cand.products if cp.product_id == p.product_id),
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

        kept, soft = self._diversity_trim(kept, candidates_by_id)
        if not kept:
            return GuardResult(ok=False, errors=errors or ["diversity_empty"], soft_dropped=soft)
        return GuardResult(ok=True, merchants=kept, errors=errors, soft_dropped=soft)

    def _check_evidence_refs(
        self,
        evidence_ids: list[str],
        evidence_index: dict[str, Evidence],
        *,
        expected_subject: tuple[str, str],
        candidate_set_version: str,
        now: datetime,
        allow_tool: bool,
        allow_snapshot: bool,
        merchant_id: str | None = None,
    ) -> str | None:
        if not evidence_ids:
            return "missing_evidence:empty"
        exp_type, exp_id = expected_subject
        for eid in evidence_ids:
            if eid not in evidence_index:
                return f"missing_evidence:{eid}"
            ev = evidence_index[eid]
            if ev.subject_type == "snapshot":
                if not allow_snapshot:
                    return f"evidence_subject:{eid}"
                continue
            if ev.subject_type == "tool":
                if not allow_tool:
                    return f"evidence_subject:{eid}"
                continue
            if ev.expires_at:
                exp = to_utc(ev.expires_at)
                if exp is not None and exp <= now:
                    return f"evidence_expired:{eid}"
            if candidate_set_version and ev.candidate_set_version:
                if ev.candidate_set_version != candidate_set_version:
                    return f"evidence_version:{eid}"
            if ev.subject_type == exp_type:
                if ev.subject_id != exp_id:
                    return f"evidence_ownership:{eid}"
            elif exp_type == "product" and ev.subject_type == "merchant":
                # product must not cite foreign merchant evidence
                if merchant_id and ev.subject_id != merchant_id:
                    return f"evidence_ownership:{eid}"
                return f"evidence_subject:{eid}"
            else:
                return f"evidence_subject:{eid}"
        return None

    def _merchant_bag(
        self, evidence_ids: list[str], evidence_index: dict[str, Evidence], merchant_id: str
    ) -> dict:
        bag: dict = {}
        for eid in evidence_ids:
            ev = evidence_index[eid]
            if ev.subject_type == "merchant" and ev.subject_id == merchant_id:
                for k, v in ev.facts.items():
                    if k in MERCHANT_FACT_KEYS:
                        bag[k] = v
            elif ev.subject_type == "tool":
                # tool compare may nest aspects; flatten carefully without product price
                data = ev.facts.get("data") or ev.facts
                aspects = data.get("aspects") if isinstance(data, dict) else None
                if isinstance(aspects, dict):
                    for aspect, sides in aspects.items():
                        if not isinstance(sides, dict):
                            continue
                        if aspect == "price":
                            # avg prices for merchants in compare — store as avg_price only if side matches
                            if data.get("left_id") == merchant_id and "left" in sides:
                                bag["avg_price"] = sides["left"]
                            if data.get("right_id") == merchant_id and "right" in sides:
                                bag["avg_price"] = sides["right"]
                        elif aspect == "distance":
                            if data.get("left_id") == merchant_id and "left" in sides:
                                bag["distance_m"] = sides["left"]
                            if data.get("right_id") == merchant_id and "right" in sides:
                                bag["distance_m"] = sides["right"]
                        elif aspect == "open_now":
                            if data.get("left_id") == merchant_id and "left" in sides:
                                bag["is_open"] = sides["left"]
                            if data.get("right_id") == merchant_id and "right" in sides:
                                bag["is_open"] = sides["right"]
        return bag

    def _product_bag(
        self, evidence_ids: list[str], evidence_index: dict[str, Evidence], product_id: str
    ) -> dict:
        bag: dict = {}
        for eid in evidence_ids:
            ev = evidence_index[eid]
            if ev.subject_type == "product" and ev.subject_id == product_id:
                for k, v in ev.facts.items():
                    if k in PRODUCT_FACT_KEYS:
                        bag[k] = v
        return bag

    def _reject_unitless_claims(self, reason: str) -> str | None:
        # claim keyword + number must be followed by 元/米 (avoid \d+ backtracking traps)
        for match in re.finditer(r"(价格|人均|距离)\s*(?:约\s*)?(\d+(?:\.\d+)?)", reason):
            rest = reason[match.end() :]
            if not re.match(r"\s*(?:元|米)", rest):
                return "reason_fact_mismatch:unitless"
        if re.search(r"\d+(?:\.\d+)?\s*(?:块|rmb|RMB|km|KM)(?!\w)", reason):
            return "reason_fact_mismatch:unit_alias"
        return None

    def _reason_fact_mismatch_merchant(
        self,
        reason: str,
        evidence_ids: list[str],
        evidence_index: dict[str, Evidence],
        merchant_id: str,
    ) -> str | None:
        unit_err = self._reject_unitless_claims(reason)
        if unit_err:
            return unit_err
        bag = self._merchant_bag(evidence_ids, evidence_index, merchant_id)
        # must NOT fall back to product `price`
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*元", reason):
            val = float(match.group(1))
            if "avg_price" not in bag:
                return "reason_fact_mismatch:price_bag"
            if abs(bag["avg_price"] - val) > max(5.0, bag["avg_price"] * 0.15):
                return "reason_fact_mismatch:price"
        for match in re.finditer(r"(\d+)\s*米", reason):
            val = float(match.group(1))
            dist = bag.get("distance_m")
            if dist is None:
                return "reason_fact_mismatch:distance_bag"
            if abs(dist - val) > max(200.0, dist * 0.2):
                return "reason_fact_mismatch:distance"
        if "营业" in reason and bag.get("is_open") is False:
            return "reason_fact_mismatch:open"
        return None

    def _reason_fact_mismatch_product(
        self,
        reason: str,
        evidence_ids: list[str],
        evidence_index: dict[str, Evidence],
        product_id: str,
    ) -> str | None:
        unit_err = self._reject_unitless_claims(reason)
        if unit_err:
            return unit_err
        bag = self._product_bag(evidence_ids, evidence_index, product_id)
        for match in re.finditer(r"(\d+(?:\.\d+)?)\s*元", reason):
            val = float(match.group(1))
            if "price" not in bag:
                return "reason_fact_mismatch:price_bag"
            if abs(bag["price"] - val) > max(5.0, bag["price"] * 0.15):
                return "reason_fact_mismatch:price"
        if "有库存" in reason and bag.get("in_stock") is False:
            return "reason_fact_mismatch:stock"
        # product reason must not claim distance (merchant fact)
        if re.search(r"\d+\s*米", reason):
            return "reason_fact_mismatch:price_bag"
        return None

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
