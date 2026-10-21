package com.recagent.service;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.recagent.catalog.Catalog;
import com.recagent.config.AppSettings;
import com.recagent.engine.RecommendationEngine;
import com.recagent.model.Domain.CreateTaskRequest;
import com.recagent.model.Domain.FeedbackRequest;
import com.recagent.model.Domain.RecommendationResult;
import com.recagent.model.Enums.RouteMode;
import com.recagent.model.Enums.TaskStatus;
import com.recagent.runtime.guard.Guard;
import com.recagent.runtime.loop.AgentLoop;
import com.recagent.support.TestSupport;
import com.recagent.tools.ToolExecutor;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class PipelineTest {
  @TempDir Path tmp;

  private CreateTaskRequest req(String userId, String sceneId) {
    return new CreateTaskRequest("t_demo", userId, TestSupport.scene(sceneId));
  }

  @Test
  void endToEndNoodleRegular() throws Exception {
    RecommendationService svc = TestSupport.service(tmp.resolve("t.db"));
    RecommendationResult result = svc.createTask(req("u_noodle_regular", "s_e2e_1"));
    assertTrue(
        result.status == TaskStatus.succeeded || result.status == TaskStatus.degraded);
    assertFalse(result.merchants.isEmpty());
    assertTrue(result.merchants.stream().allMatch(m -> m.products != null && !m.products.isEmpty()));
    assertNotNull(result.metrics.get("run_id"));
  }

  @Test
  void optOutSkipped() throws Exception {
    RecommendationService svc = TestSupport.service(tmp.resolve("t.db"));
    RecommendationResult result = svc.createTask(req("u_opt_out", "s_e2e_opt"));
    assertEquals(TaskStatus.skipped, result.status);
    assertEquals("not_authorized", result.decisionReasonCode);
  }

  @Test
  void notInterestedFiltersNext() throws Exception {
    RecommendationService svc = TestSupport.service(tmp.resolve("t.db"));
    RecommendationResult first = svc.createTask(req("u_noodle_regular", "s_ni_1"));
    assertFalse(first.merchants.isEmpty());
    String mid = first.merchants.get(0).merchantId;
    Map<String, Object> fb =
        svc.feedback(first.taskId, new FeedbackRequest("not_interested", mid, null));
    assertEquals(true, fb.get("ok"));
    RecommendationResult second = svc.createTask(req("u_noodle_regular", "s_ni_2"));
    Set<String> ids =
        second.merchants.stream().map(m -> m.merchantId).collect(Collectors.toSet());
    assertFalse(ids.contains(mid));
  }

  @Test
  void conflictReactCitesCmp() throws Exception {
    Catalog catalog = TestSupport.catalog();
    AppSettings settings = TestSupport.settings();
    RecommendationEngine eng = new RecommendationEngine(catalog, settings);
    var snap = catalog.getUser("u_conflict");
    var out = eng.run(snap, TestSupport.scene("cmp_obs"));
    ToolExecutor tools = new ToolExecutor(catalog, eng, null, settings);
    AgentLoop loop = new AgentLoop(tools, new Guard(settings), settings);
    Map<String, Object> result =
        loop.run(
            RouteMode.REACT,
            snap,
            TestSupport.scene("cmp_obs"),
            out.candidates,
            null,
            null,
            out.candidateSetVersion,
            true,
            false,
            false);
    assertEquals(true, result.get("ok"));
    @SuppressWarnings("unchecked")
    List<com.recagent.model.Domain.RecommendedMerchant> merchants =
        (List<com.recagent.model.Domain.RecommendedMerchant>) result.get("merchants");
    assertFalse(merchants.isEmpty());
    var top = merchants.get(0);
    assertTrue(top.evidenceIds.stream().anyMatch(eid -> eid.startsWith("cmp_")));
    @SuppressWarnings("unchecked")
    List<Map<String, Object>> steps = (List<Map<String, Object>>) result.get("steps");
    assertTrue(steps.stream().anyMatch(s -> "compare_candidates".equals(s.get("tool_name"))));
  }
}
