package com.recagent.runtime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.recagent.catalog.Catalog;
import com.recagent.config.AppSettings;
import com.recagent.engine.RecommendationEngine;
import com.recagent.model.Domain.CreateTaskRequest;
import com.recagent.model.Domain.RecommendationResult;
import com.recagent.model.Enums.RouteMode;
import com.recagent.model.Enums.TaskStatus;
import com.recagent.policy.Policy;
import com.recagent.runtime.budget.DeadlineBudget;
import com.recagent.runtime.guard.Guard;
import com.recagent.runtime.loop.AgentLoop;
import com.recagent.service.RecommendationService;
import com.recagent.support.TestSupport;
import com.recagent.time.Utc;
import com.recagent.tools.ToolExecutor;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class DeadlineTest {
  @TempDir Path tmp;

  @Test
  void budgetRejectsWhenDeadlinePassed() {
    AppSettings settings = new AppSettings();
    settings.setModelMinRemainingMs(500);
    var past = Utc.now().minus(Duration.ofSeconds(1));
    assertFalse(new DeadlineBudget(past, settings).allowModel());
    assertFalse(new DeadlineBudget(past, settings).allowTool(2000));
  }

  @Test
  void loopDeadlineExhaustedNoModelCalls() throws Exception {
    Catalog catalog = TestSupport.catalog();
    AppSettings settings = new AppSettings();
    RecommendationEngine eng = new RecommendationEngine(catalog, settings);
    var snap = catalog.getUser("u_noodle_regular");
    var out = eng.run(snap, TestSupport.scene("s_dl"));
    AgentLoop loop =
        new AgentLoop(
            new ToolExecutor(catalog, eng, null, settings), new Guard(settings), settings);
    var past = Utc.now().minus(Duration.ofSeconds(1));
    Map<String, Object> result =
        loop.run(
            RouteMode.DIRECT,
            snap,
            TestSupport.scene("s_dl"),
            out.candidates,
            null,
            past,
            out.candidateSetVersion,
            false,
            false,
            false);
    assertEquals(false, result.get("ok"));
    assertEquals("deadline_exhausted", result.get("fallback_reason"));
    assertEquals(0, result.get("model_calls"));
  }

  @Test
  void serviceRoutesFallbackWhenBudgetExhausted() throws Exception {
    AppSettings settings = TestSupport.settings();
    settings.setTaskTimeoutMs(0);
    settings.setModelMinRemainingMs(500);
    settings.setEnableLlm(true);
    RecommendationService svc = TestSupport.service(tmp.resolve("d.db"), settings);
    RecommendationResult res =
        svc.createTask(
            new CreateTaskRequest("t_demo", "u_noodle_regular", TestSupport.scene("s_fb")));
    assertEquals(TaskStatus.degraded, res.status);
    assertTrue(res.routeMode == RouteMode.FALLBACK || "ranker_fallback".equals(res.source));
    assertFalse(res.merchants.isEmpty());
    Object mc = res.metrics.getOrDefault("model_calls", 0);
    assertEquals(0, ((Number) mc).intValue());
  }

  @Test
  void policyBudgetOkUsesModelMinRemaining() {
    AppSettings settings = new AppSettings();
    settings.setModelMinRemainingMs(1000);
    Policy policy = new Policy(settings);
    assertFalse(policy.budgetOk(Utc.now().plus(Duration.ofMillis(200))));
    assertTrue(policy.budgetOk(Utc.now().plus(Duration.ofSeconds(5))));
  }
}
