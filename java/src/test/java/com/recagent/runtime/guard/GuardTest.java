package com.recagent.runtime.guard;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.recagent.catalog.Catalog;
import com.recagent.config.AppSettings;
import com.recagent.engine.RecommendationEngine;
import com.recagent.model.Domain.CandidateMerchant;
import com.recagent.model.Domain.EngineOutput;
import com.recagent.model.Domain.RecommendedMerchant;
import com.recagent.model.Domain.RecommendedProduct;
import com.recagent.model.Domain.UserSnapshot;
import com.recagent.support.TestSupport;
import com.recagent.time.Utc;
import java.time.Duration;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class GuardTest {
  private Guard guard;
  private EngineOutput out;
  private Map<String, Evidence> evidence;
  private Map<String, CandidateMerchant> byId;
  private Set<String> allowed;

  @BeforeEach
  void setUp() {
    Catalog catalog = TestSupport.catalog();
    RecommendationEngine eng = new RecommendationEngine(catalog, new AppSettings());
    UserSnapshot snap = catalog.getUser("u_noodle_regular");
    out = eng.run(snap, TestSupport.scene("g1"));
    guard = new Guard(new AppSettings());
    evidence = guard.buildEvidenceIndex(out.candidates, out.candidateSetVersion);
    byId = new HashMap<>();
    allowed = new HashSet<>();
    for (CandidateMerchant c : out.candidates) {
      byId.put(c.merchantId, c);
      allowed.add(c.merchantId);
      c.products.forEach(p -> allowed.add(p.productId));
    }
  }

  @Test
  void blocksPriceBagMix() {
    CandidateMerchant c = out.candidates.get(0);
    var p = c.products.get(0);
    List<RecommendedMerchant> draft =
        List.of(
            new RecommendedMerchant(
                c.merchantId,
                c.name,
                "人均约 " + ((int) p.price) + " 元很合适",
                List.of("prod_" + p.productId),
                List.of(
                    new RecommendedProduct(
                        p.productId,
                        p.name,
                        "有库存，价格 " + ((int) p.price) + " 元",
                        List.of("prod_" + p.productId)))));
    var gr =
        guard.validateSubmission(
            draft, allowed, byId, evidence, "recommend", out.candidateSetVersion, Utc.now());
    assertTrue(
        gr.merchants().isEmpty()
            || gr.errors().stream()
                .anyMatch(e -> e.contains("price_bag") || e.contains("evidence_subject")));
    assertFalse(gr.merchants().stream().anyMatch(m -> m.merchantId.equals(c.merchantId)));
  }

  @Test
  void blocksExpiredEvidence() {
    CandidateMerchant c = out.candidates.get(0);
    var p = c.products.get(0);
    String eid = c.evidenceId != null && !c.evidenceId.isBlank() ? c.evidenceId : "cand_" + c.merchantId;
    evidence.get(eid).expiresAt = Utc.toIso(Utc.now().minus(Duration.ofHours(1)));
    List<RecommendedMerchant> draft =
        List.of(
            new RecommendedMerchant(
                c.merchantId,
                c.name,
                "距离约 " + ((int) c.distanceM) + " 米",
                List.of(eid),
                List.of(
                    new RecommendedProduct(
                        p.productId,
                        p.name,
                        "有库存，价格 " + ((int) p.price) + " 元",
                        List.of("prod_" + p.productId)))));
    var gr =
        guard.validateSubmission(
            draft, allowed, byId, evidence, "recommend", out.candidateSetVersion, Utc.now());
    assertTrue(gr.errors().stream().anyMatch(e -> e.contains("evidence_expired")));
    assertFalse(gr.merchants().stream().anyMatch(m -> m.merchantId.equals(c.merchantId)));
  }

  @Test
  void blocksVersionMismatch() {
    CandidateMerchant c = out.candidates.get(0);
    var p = c.products.get(0);
    String eid = c.evidenceId != null && !c.evidenceId.isBlank() ? c.evidenceId : "cand_" + c.merchantId;
    evidence.get(eid).candidateSetVersion = "stale_version";
    List<RecommendedMerchant> draft =
        List.of(
            new RecommendedMerchant(
                c.merchantId,
                c.name,
                "距离约 " + ((int) c.distanceM) + " 米",
                List.of(eid),
                List.of(
                    new RecommendedProduct(
                        p.productId,
                        p.name,
                        "有库存，价格 " + ((int) p.price) + " 元",
                        List.of("prod_" + p.productId)))));
    var gr =
        guard.validateSubmission(
            draft, allowed, byId, evidence, "recommend", out.candidateSetVersion, Utc.now());
    assertTrue(gr.errors().stream().anyMatch(e -> e.contains("evidence_version")));
    assertFalse(gr.merchants().stream().anyMatch(m -> m.merchantId.equals(c.merchantId)));
  }
}
