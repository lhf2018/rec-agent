package com.recagent.policy;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.recagent.catalog.Catalog;
import com.recagent.config.AppSettings;
import com.recagent.engine.RecommendationEngine;
import com.recagent.model.Domain.EngineOutput;
import com.recagent.model.Domain.RuntimeSignals;
import com.recagent.model.Domain.UserSnapshot;
import com.recagent.model.Enums.RouteMode;
import com.recagent.support.TestSupport;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

class PolicyTest {
  private Catalog catalog;
  private Policy policy;
  private RecommendationEngine engine;

  @BeforeEach
  void setUp() {
    catalog = TestSupport.catalog();
    AppSettings settings = TestSupport.settings();
    policy = new Policy(settings);
    engine = new RecommendationEngine(catalog, settings);
  }

  @Test
  void blocksOptOut() {
    UserSnapshot snap = catalog.getUser("u_opt_out");
    var r = policy.checkEligibility(snap, TestSupport.scene("s1"));
    assertFalse(r.ok());
    assertEquals("not_authorized", r.reasonCode());
  }

  @Test
  void blocksDndWindow() {
    UserSnapshot snap = catalog.getUser("u_noodle_regular");
    var r = policy.checkEligibility(snap, TestSupport.scene("s1", "2026-10-20T23:30:00+08:00"));
    assertFalse(r.ok());
    assertEquals("dnd_window", r.reasonCode());
  }

  @Test
  void softRerankRoutesToDirect() {
    UserSnapshot snap = catalog.getUser("u_noodle_regular");
    EngineOutput out = engine.run(snap, TestSupport.scene("soft_route"));
    assertTrue(out.softRerank);
    assertFalse(out.intentConflict);
    assertFalse(out.needCompare);
    var decision = policy.route(out);
    assertEquals(RouteMode.DIRECT, decision.mode());
    assertEquals("soft_rerank", decision.reasonCode());
  }

  @Test
  void conflictRoutesToReact() {
    UserSnapshot snap = catalog.getUser("u_conflict");
    EngineOutput out = engine.run(snap, TestSupport.scene("conflict_route"));
    assertTrue(out.intentConflict);
    assertTrue(out.needCompare);
    var decision = policy.route(out);
    assertEquals(RouteMode.REACT, decision.mode());
    assertTrue(
        decision.reasonCode().equals("need_compare")
            || decision.reasonCode().equals("intent_conflict")
            || decision.reasonCode().equals("react_gap"));
  }

  @Test
  void opportunitySuppressDuplicateScene() {
    UserSnapshot snap = catalog.getUser("u_noodle_regular");
    snap.runtime = new RuntimeSignals();
    snap.runtime.sceneSeenRecently = true;
    var r = policy.checkOpportunity(snap, TestSupport.scene("s_dup"));
    assertEquals("suppress", r.result().name());
    assertEquals("duplicate_scene_suppress", r.reasonCode());
  }
}
