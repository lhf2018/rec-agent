from datetime import timedelta

from rec_agent.catalog import Catalog
from rec_agent.engine import RecommendationEngine
from rec_agent.models import (
    Location,
    RecommendedMerchant,
    RecommendedProduct,
    Scene,
    TriggerType,
)
from rec_agent.runtime.guard import Evidence, Guard
from rec_agent.timeutil import to_utc_iso, utc_now


def _scene() -> Scene:
    return Scene(
        scene_id="g1",
        trigger=TriggerType.impression,
        occurred_at="2026-10-20T12:10:00+08:00",
        location=Location(lng=121.47, lat=31.23, district="南京西路"),
        radius_m=3000,
        meal_period="lunch",
    )


def _ctx():
    cat = Catalog()
    eng = RecommendationEngine(cat)
    snap = cat.get_user("u_noodle_regular")
    out = eng.run(snap, _scene())
    guard = Guard()
    evidence = guard.build_evidence_index(
        out.candidates, candidate_set_version=out.candidate_set_version
    )
    by_id = {c.merchant_id: c for c in out.candidates}
    allowed = set(by_id) | {p.product_id for c in out.candidates for p in c.products}
    return guard, out, evidence, by_id, allowed


def test_guard_blocks_price_bag_mix():
    guard, out, evidence, by_id, allowed = _ctx()
    c = out.candidates[0]
    p = c.products[0]
    # merchant reason cites 元 but only product evidence (price bag mix)
    draft = [
        RecommendedMerchant(
            merchant_id=c.merchant_id,
            name=c.name,
            reason=f"人均约 {int(p.price)} 元很合适",
            evidence_ids=[f"prod_{p.product_id}"],
            products=[
                RecommendedProduct(
                    product_id=p.product_id,
                    name=p.name,
                    reason=f"有库存，价格 {int(p.price)} 元",
                    evidence_ids=[f"prod_{p.product_id}"],
                )
            ],
        )
    ]
    gr = guard.validate_submission(
        draft,
        allowed,
        by_id,
        evidence,
        candidate_set_version=out.candidate_set_version,
        now=utc_now(),
    )
    assert not gr.merchants or any("price_bag" in e or "evidence_subject" in e for e in gr.errors)
    assert c.merchant_id not in {m.merchant_id for m in gr.merchants}


def test_guard_blocks_expired_evidence():
    guard, out, evidence, by_id, allowed = _ctx()
    c = out.candidates[0]
    p = c.products[0]
    eid = c.evidence_id or f"cand_{c.merchant_id}"
    evidence[eid].expires_at = to_utc_iso(utc_now() - timedelta(hours=1))
    draft = [
        RecommendedMerchant(
            merchant_id=c.merchant_id,
            name=c.name,
            reason=f"距离约 {int(c.distance_m)} 米",
            evidence_ids=[eid],
            products=[
                RecommendedProduct(
                    product_id=p.product_id,
                    name=p.name,
                    reason=f"有库存，价格 {int(p.price)} 元",
                    evidence_ids=[f"prod_{p.product_id}"],
                )
            ],
        )
    ]
    gr = guard.validate_submission(
        draft,
        allowed,
        by_id,
        evidence,
        candidate_set_version=out.candidate_set_version,
        now=utc_now(),
    )
    assert any("evidence_expired" in e for e in gr.errors)
    assert c.merchant_id not in {m.merchant_id for m in gr.merchants}


def test_guard_blocks_version_mismatch():
    guard, out, evidence, by_id, allowed = _ctx()
    c = out.candidates[0]
    p = c.products[0]
    eid = c.evidence_id or f"cand_{c.merchant_id}"
    evidence[eid].candidate_set_version = "stale_version"
    draft = [
        RecommendedMerchant(
            merchant_id=c.merchant_id,
            name=c.name,
            reason=f"距离约 {int(c.distance_m)} 米",
            evidence_ids=[eid],
            products=[
                RecommendedProduct(
                    product_id=p.product_id,
                    name=p.name,
                    reason=f"有库存，价格 {int(p.price)} 元",
                    evidence_ids=[f"prod_{p.product_id}"],
                )
            ],
        )
    ]
    gr = guard.validate_submission(
        draft,
        allowed,
        by_id,
        evidence,
        candidate_set_version=out.candidate_set_version,
        now=utc_now(),
    )
    assert any("evidence_version" in e for e in gr.errors)
    assert c.merchant_id not in {m.merchant_id for m in gr.merchants}


def test_guard_blocks_unitless_format_bypass():
    guard, out, evidence, by_id, allowed = _ctx()
    c = out.candidates[0]
    p = c.products[0]
    draft = [
        RecommendedMerchant(
            merchant_id=c.merchant_id,
            name=c.name,
            reason=f"人均约 {int(c.avg_price)}很近",
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
    ]
    gr = guard.validate_submission(
        draft,
        allowed,
        by_id,
        evidence,
        candidate_set_version=out.candidate_set_version,
        now=utc_now(),
    )
    assert any("unit" in e for e in gr.errors)
    assert c.merchant_id not in {m.merchant_id for m in gr.merchants}


def test_guard_blocks_ownership_mismatch():
    guard, out, evidence, by_id, allowed = _ctx()
    assert len(out.candidates) >= 2
    c0, c1 = out.candidates[0], out.candidates[1]
    p = c0.products[0]
    foreign = c1.evidence_id or f"cand_{c1.merchant_id}"
    draft = [
        RecommendedMerchant(
            merchant_id=c0.merchant_id,
            name=c0.name,
            reason=f"距离约 {int(c0.distance_m)} 米",
            evidence_ids=[foreign],
            products=[
                RecommendedProduct(
                    product_id=p.product_id,
                    name=p.name,
                    reason=f"有库存，价格 {int(p.price)} 元",
                    evidence_ids=[f"prod_{p.product_id}"],
                )
            ],
        )
    ]
    gr = guard.validate_submission(
        draft,
        allowed,
        by_id,
        evidence,
        candidate_set_version=out.candidate_set_version,
        now=utc_now(),
    )
    assert any("evidence_ownership" in e for e in gr.errors)
    assert c0.merchant_id not in {m.merchant_id for m in gr.merchants}
