from __future__ import annotations

import math
from collections import defaultdict
from datetime import timedelta

from rec_agent.catalog import Catalog
from rec_agent.config import Settings, get_settings
from rec_agent.models import (
    CandidateMerchant,
    CandidateProduct,
    DroppedItem,
    EngineOutput,
    Merchant,
    Scene,
    UserSnapshot,
)
from rec_agent.timeutil import to_utc_iso, utc_now


def _haversine_m(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _now_iso() -> str:
    return to_utc_iso(utc_now())


def _expires_iso(hours: int = 2) -> str:
    return to_utc_iso(utc_now() + timedelta(hours=hours))


class RecommendationEngine:
    def __init__(self, catalog: Catalog, settings: Settings | None = None) -> None:
        self.catalog = catalog
        self.settings = settings or get_settings()

    def run(self, snapshot: UserSnapshot, scene: Scene, radius_override: int | None = None) -> EngineOutput:
        radius = radius_override or scene.radius_m
        recalled = self._merge_recalls(snapshot, scene, radius)
        kept, dropped, stats = self._hard_filter(recalled, snapshot, scene, radius)
        ranked = self._rank(kept, snapshot, scene)
        ranked = self._diversity_cap_pool(ranked)
        candidates = ranked[: self.settings.candidate_limit]
        for i, c in enumerate(candidates):
            c.evidence_id = f"cand_{c.merchant_id}"
            c.observed_at = _now_iso()
            c.expires_at = _expires_iso()
            c.provenance = "primary"
        baseline = candidates[: self.settings.max_merchants]
        intent_conflict = self._intent_conflict(snapshot, candidates)
        facts_complete = all(c.is_open and c.distance_m >= 0 for c in candidates[:3]) if candidates else False
        version = f"csv_{scene.scene_id}_{len(candidates)}_{len(dropped)}"
        return EngineOutput(
            candidates=candidates,
            baseline=baseline,
            dropped=dropped,
            candidate_set_version=version,
            facts_complete=facts_complete and bool(candidates),
            intent_conflict=intent_conflict,
            need_freshness=False,
            filter_stats=stats,
        )

    def supplement(
        self,
        snapshot: UserSnapshot,
        scene: Scene,
        strategy: str,
        existing_ids: set[str],
    ) -> EngineOutput:
        radius = scene.radius_m
        if strategy == "widen_radius":
            radius = int(scene.radius_m * 1.8)
        out = self.run(snapshot, scene, radius_override=radius)
        # keep only new ids for supplement merge helper; caller merges
        new_cands = [c for c in out.candidates if c.merchant_id not in existing_ids]
        for c in new_cands:
            c.provenance = "supplement"
            c.evidence_id = f"supp_{c.merchant_id}"
        out.candidates = new_cands
        out.baseline = new_cands[: self.settings.max_merchants]
        out.candidate_set_version = out.candidate_set_version + "_supp"
        return out

    def _merge_recalls(
        self, snapshot: UserSnapshot, scene: Scene, radius: int
    ) -> dict[str, CandidateMerchant]:
        buckets: dict[str, list[str]] = defaultdict(list)
        for mid in self._recall_similar(snapshot):
            buckets[mid].append("similar")
        for mid in self._recall_preference(snapshot):
            buckets[mid].append("preference")
        for mid in self._recall_scene(scene, radius):
            buckets[mid].append("scene")
        for mid in self._recall_trending(scene):
            buckets[mid].append("trending")

        merged: dict[str, CandidateMerchant] = {}
        for mid, sources in buckets.items():
            m = self.catalog.get_merchant(mid)
            if not m:
                continue
            merged[mid] = self._to_candidate(m, scene, list(dict.fromkeys(sources)))
        return merged

    def _recall_similar(self, snapshot: UserSnapshot) -> list[str]:
        ids = list(snapshot.frequent_merchants)
        for m in self.catalog.all_merchants():
            if m.category in snapshot.long_term_categories and m.merchant_id not in ids:
                ids.append(m.merchant_id)
        return ids[: self.settings.recall_limit]

    def _recall_preference(self, snapshot: UserSnapshot) -> list[str]:
        ids = []
        for m in self.catalog.all_merchants():
            if any(t in m.tags for t in snapshot.preferred_tags) or m.category in snapshot.preferred_tags:
                ids.append(m.merchant_id)
        for intent in snapshot.short_term:
            for m in self.catalog.all_merchants():
                if m.category == intent.topic or intent.topic in m.tags:
                    if m.merchant_id not in ids:
                        ids.append(m.merchant_id)
        return ids[: self.settings.recall_limit]

    def _recall_scene(self, scene: Scene, radius: int) -> list[str]:
        ids = []
        for m in self.catalog.all_merchants():
            dist = _haversine_m(scene.location.lng, scene.location.lat, m.lng, m.lat)
            if dist <= radius and m.is_open:
                ids.append(m.merchant_id)
        return ids[: self.settings.recall_limit]

    def _recall_trending(self, scene: Scene) -> list[str]:
        ranked = sorted(self.catalog.all_merchants(), key=lambda m: m.score, reverse=True)
        return [m.merchant_id for m in ranked[: self.settings.recall_limit]]

    def _to_candidate(self, m: Merchant, scene: Scene, sources: list[str]) -> CandidateMerchant:
        dist = _haversine_m(scene.location.lng, scene.location.lat, m.lng, m.lat)
        products = [
            CandidateProduct(
                product_id=p.product_id,
                name=p.name,
                price=p.price,
                in_stock=p.in_stock,
                dietary_tags=p.dietary_tags,
            )
            for p in m.products
        ]
        return CandidateMerchant(
            merchant_id=m.merchant_id,
            name=m.name,
            category=m.category,
            avg_price=m.avg_price,
            distance_m=round(dist),
            is_open=m.is_open,
            score=m.score,
            tags=m.tags,
            products=products,
            recall_sources=sources,
        )

    def _hard_filter(
        self,
        recalled: dict[str, CandidateMerchant],
        snapshot: UserSnapshot,
        scene: Scene,
        radius: int,
    ) -> tuple[list[CandidateMerchant], list[DroppedItem], dict[str, int]]:
        kept: list[CandidateMerchant] = []
        dropped: list[DroppedItem] = []
        stats: dict[str, int] = defaultdict(int)
        hard = snapshot.hard

        for mid, cand in recalled.items():
            m = self.catalog.get_merchant(mid)
            if not m:
                continue
            reason = None
            if m.status != "online":
                reason = "offline"
            elif not m.is_open:
                reason = "closed"
            elif cand.distance_m > radius or cand.distance_m > m.delivery_radius_m:
                reason = "out_of_range"
            elif mid in hard.blocked_merchants:
                reason = "blocked"
            elif mid in hard.not_interested_merchants:
                reason = "not_interested"
            elif hard.budget_max is not None and m.avg_price > hard.budget_max:
                reason = "price_band"
            elif hard.budget_min is not None and m.avg_price < hard.budget_min:
                reason = "price_band"
            elif any(tag in hard.dietary_forbid for tag in m.tags):
                reason = "dietary"
            else:
                # filter products by dietary / stock
                ok_products = []
                for p in cand.products:
                    if not p.in_stock:
                        continue
                    if any(t in hard.dietary_forbid for t in p.dietary_tags):
                        continue
                    if hard.budget_max is not None and p.price > hard.budget_max:
                        continue
                    ok_products.append(p)
                if not ok_products:
                    reason = "out_of_stock" if cand.products else "out_of_stock"
                else:
                    cand.products = ok_products

            if reason:
                dropped.append(DroppedItem(merchant_id=mid, reason=reason))
                stats[reason] += 1
            else:
                kept.append(cand)
        return kept, dropped, dict(stats)

    def _rank(
        self, kept: list[CandidateMerchant], snapshot: UserSnapshot, scene: Scene
    ) -> list[CandidateMerchant]:
        short_topics = {i.topic for i in snapshot.short_term if i.confidence >= 0.5}

        def score(c: CandidateMerchant) -> float:
            s = c.score * 10
            if c.merchant_id in snapshot.frequent_merchants:
                s += 8
            if c.category in snapshot.long_term_categories:
                s += 5
            if c.category in short_topics:
                # modest boost so long-term/trending can still rank first → intent_conflict
                s += 3
            if any(t in snapshot.preferred_tags for t in c.tags):
                s += 4
            # prefer closer
            s += max(0, 5 - c.distance_m / 1000)
            if "trending" in c.recall_sources and snapshot.lifecycle == "new":
                s += 6
            if "scene" in c.recall_sources:
                s += 2
            c.score = round(s, 3)
            return s

        return sorted(kept, key=score, reverse=True)

    def _diversity_cap_pool(self, ranked: list[CandidateMerchant]) -> list[CandidateMerchant]:
        # coarse: within top pool, no category > 50%
        if not ranked:
            return ranked
        limit = self.settings.candidate_limit
        max_per_cat = max(1, limit // 2)
        counts: dict[str, int] = defaultdict(int)
        out: list[CandidateMerchant] = []
        deferred: list[CandidateMerchant] = []
        for c in ranked:
            if counts[c.category] < max_per_cat:
                out.append(c)
                counts[c.category] += 1
            else:
                deferred.append(c)
            if len(out) >= limit:
                break
        if len(out) < limit:
            for c in deferred:
                out.append(c)
                if len(out) >= limit:
                    break
        return out

    def _intent_conflict(self, snapshot: UserSnapshot, candidates: list[CandidateMerchant]) -> bool:
        if not candidates or not snapshot.short_term:
            return False
        top_intent = max(snapshot.short_term, key=lambda x: x.confidence)
        if top_intent.confidence < 0.8:
            return False
        return candidates[0].category != top_intent.topic
