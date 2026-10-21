package com.recagent.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.recagent.catalog.Catalog;
import com.recagent.config.AppSettings;
import com.recagent.engine.RecommendationEngine;
import com.recagent.feedback.FeedbackService;
import com.recagent.model.Domain.CandidateMerchant;
import com.recagent.model.Domain.CandidateProduct;
import com.recagent.model.Domain.CreateTaskRequest;
import com.recagent.model.Domain.EngineOutput;
import com.recagent.model.Domain.FeedbackRequest;
import com.recagent.model.Domain.RecommendedMerchant;
import com.recagent.model.Domain.RecommendedProduct;
import com.recagent.model.Domain.RecommendationResult;
import com.recagent.model.Domain.Scene;
import com.recagent.model.Domain.UserSnapshot;
import com.recagent.model.Enums.OpportunityResult;
import com.recagent.model.Enums.RouteMode;
import com.recagent.model.Enums.TaskStatus;
import com.recagent.policy.Policy;
import com.recagent.runtime.guard.Guard;
import com.recagent.runtime.guard.Guard.GuardResult;
import com.recagent.runtime.loop.AgentLoop;
import com.recagent.runtime.trace.TraceRecorder;
import com.recagent.snapshot.SnapshotBuilder;
import com.recagent.store.Store;
import com.recagent.time.Utc;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.springframework.stereotype.Service;

@Service
public class RecommendationService {
  private final Catalog catalog;
  private final Store store;
  private final Policy policy;
  private final AppSettings settings;
  private final RecommendationEngine engine;
  private final Guard guard;
  private final AgentLoop loop;
  private final SnapshotBuilder snapshots;
  private final FeedbackService feedbackSvc;
  private final ObjectMapper mapper;

  public RecommendationService(
      Catalog catalog,
      Store store,
      Policy policy,
      AppSettings settings,
      RecommendationEngine engine,
      Guard guard,
      AgentLoop loop,
      SnapshotBuilder snapshots,
      FeedbackService feedbackSvc,
      ObjectMapper mapper) {
    this.catalog = catalog;
    this.store = store;
    this.policy = policy;
    this.settings = settings;
    this.engine = engine;
    this.guard = guard;
    this.loop = loop;
    this.snapshots = snapshots;
    this.feedbackSvc = feedbackSvc;
    this.mapper = mapper;
  }

  /** Manual wiring for unit tests. */
  public RecommendationService(
      Catalog catalog, Store store, AppSettings settings, ObjectMapper mapper) {
    this.catalog = catalog;
    this.store = store;
    this.settings = settings;
    this.mapper = mapper;
    this.policy = new Policy(settings);
    this.engine = new RecommendationEngine(catalog, settings);
    this.guard = new Guard(settings);
    this.snapshots = new SnapshotBuilder(catalog, store, settings);
    var tools = new com.recagent.tools.ToolExecutor(catalog, engine, store, settings);
    this.loop = new AgentLoop(tools, guard, settings);
    this.feedbackSvc = new FeedbackService(catalog, store, settings);
  }

  public RecommendationResult createTask(CreateTaskRequest req) throws Exception {
    Scene scene = req.scene();
    String key =
        req.tenantId()
            + ":"
            + req.userId()
            + ":"
            + scene.trigger().name()
            + ":"
            + scene.sceneId();
    Map<String, Object> existing = store.getByIdempotency(key);
    if (existing != null) {
      return mapExisting(existing);
    }

    String taskId = store.newTaskId();
    Instant started = Utc.now();
    Instant deadlineAt = started.plus(Duration.ofMillis(settings.getTaskTimeoutMs()));
    TraceRecorder trace = new TraceRecorder(taskId);
    String sceneKey = scene.trigger().name() + ":" + scene.sceneId();

    UserSnapshot snapshot = snapshots.build(req.userId(), started, sceneKey);
    if (snapshot == null || !req.tenantId().equals(snapshot.tenantId)) {
      RecommendationResult result = new RecommendationResult();
      result.taskId = taskId;
      result.status = TaskStatus.failed;
      result.decisionReasonCode = "user_not_found";
      persist(taskId, req, key, result, trace);
      return result;
    }

    var elig = policy.checkEligibility(snapshot, scene);
    if (!elig.ok()) {
      RecommendationResult result = new RecommendationResult();
      result.taskId = taskId;
      result.status = TaskStatus.skipped;
      result.decisionReasonCode = elig.reasonCode();
      persist(taskId, req, key, result, trace);
      return result;
    }

    var opp = policy.checkOpportunity(snapshot, scene);
    trace.setMeta(null, opp.result().name(), null, null);
    if (opp.result() == OpportunityResult.suppress) {
      RecommendationResult result = new RecommendationResult();
      result.taskId = taskId;
      result.status = TaskStatus.suppressed;
      result.opportunityResult = opp.result();
      result.decisionReasonCode = opp.reasonCode();
      persist(taskId, req, key, result, trace);
      return result;
    }
    if (opp.result() == OpportunityResult.skip) {
      RecommendationResult result = new RecommendationResult();
      result.taskId = taskId;
      result.status = TaskStatus.skipped;
      result.opportunityResult = opp.result();
      result.decisionReasonCode = opp.reasonCode();
      persist(taskId, req, key, result, trace);
      return result;
    }

    EngineOutput engineOut = engine.run(snapshot, scene);
    if (engineOut.candidates.isEmpty()) {
      RecommendationResult result = new RecommendationResult();
      result.taskId = taskId;
      result.status = TaskStatus.empty;
      result.opportunityResult = opp.result();
      result.decisionReasonCode = "no_candidates";
      result.routeMode = RouteMode.SKIP;
      result.metrics.put("filter_stats", engineOut.filterStats);
      persist(taskId, req, key, result, trace);
      return result;
    }

    boolean budgetOk = policy.budgetOk(deadlineAt);
    RouteMode forceMode = null;
    boolean useRuleRerank = "RULE_RERANK".equals(settings.getForceRoute());
    if (settings.getForceRoute() != null
        && !settings.getForceRoute().isBlank()
        && !useRuleRerank) {
      forceMode = RouteMode.valueOf(settings.getForceRoute());
    }
    var route =
        policy.route(
            engineOut,
            budgetOk,
            settings.isEnableLlm(),
            settings.isEnableReact(),
            forceMode);
    if (useRuleRerank) {
      route = new Policy.RouteDecision(RouteMode.RULE_ENGINE, "force_rule_rerank");
    }
    trace.setMeta(route.mode().name(), null, route.reasonCode(), null);

    List<RecommendedMerchant> merchants = new ArrayList<>();
    TaskStatus status = TaskStatus.succeeded;
    String source = "rule_engine";
    String fallbackReason = null;
    int modelCalls = 0;

    if (route.mode() == RouteMode.SKIP) {
      status = TaskStatus.empty;
    } else if (route.mode() == RouteMode.FALLBACK) {
      merchants = guard.baselineToRecommendations(engineOut.baseline);
      status = TaskStatus.degraded;
      source = "ranker_fallback";
      fallbackReason = route.reasonCode();
      trace.setMeta(null, null, null, fallbackReason);
    } else if (useRuleRerank) {
      List<CandidateMerchant> ranked =
          Policy.ruleRerank(engineOut.candidates, snapshot, settings.getMaxMerchants());
      merchants = ruleEngineResult(ranked);
      source = "rule_rerank";
      trace.addStep(
          1,
          "rule_rerank",
          null,
          "ok",
          merchants.stream()
              .filter(m -> m.evidenceIds != null && !m.evidenceIds.isEmpty())
              .map(m -> m.evidenceIds.get(0))
              .toList(),
          null,
          null);
    } else if (route.mode() == RouteMode.RULE_ENGINE) {
      merchants = ruleEngineResult(engineOut.baseline);
      source = "rule_engine";
      trace.addStep(
          1,
          "rule_engine_template",
          null,
          "ok",
          merchants.stream()
              .filter(m -> m.evidenceIds != null && !m.evidenceIds.isEmpty())
              .map(m -> m.evidenceIds.get(0))
              .toList(),
          null,
          null);
    } else if (route.mode() == RouteMode.DIRECT || route.mode() == RouteMode.REACT) {
      Map<String, Object> agentOut =
          loop.run(
              route.mode(),
              snapshot,
              scene,
              engineOut.candidates,
              trace,
              deadlineAt,
              engineOut.candidateSetVersion,
              engineOut.needCompare,
              engineOut.supplementNeeded,
              engineOut.softRerank);
      Object mc = agentOut.get("model_calls");
      modelCalls = mc instanceof Number n ? n.intValue() : 0;
      if (Boolean.TRUE.equals(agentOut.get("ok")) && agentOut.get("merchants") instanceof List<?> ml && !ml.isEmpty()) {
        @SuppressWarnings("unchecked")
        List<RecommendedMerchant> got = (List<RecommendedMerchant>) agentOut.get("merchants");
        merchants = got;
        source = "agent";
      } else {
        merchants = guard.baselineToRecommendations(engineOut.baseline);
        status = TaskStatus.degraded;
        source = "ranker_fallback";
        Object fr = agentOut.get("fallback_reason");
        fallbackReason = fr != null ? String.valueOf(fr) : "agent_failed";
        trace.setMeta(null, null, null, fallbackReason);
      }
    } else {
      merchants = ruleEngineResult(engineOut.baseline);
    }

    if (!merchants.isEmpty() && !"agent".equals(source)) {
      var evidence = guard.buildEvidenceIndex(engineOut.candidates, engineOut.candidateSetVersion);
      Map<String, CandidateMerchant> byId = new HashMap<>();
      Set<String> allowed = new HashSet<>();
      for (CandidateMerchant c : engineOut.candidates) {
        byId.put(c.merchantId, c);
        allowed.add(c.merchantId);
        for (CandidateProduct p : c.products) {
          allowed.add(p.productId);
        }
      }
      GuardResult gr =
          guard.validateSubmission(
              merchants,
              allowed,
              byId,
              evidence,
              "recommend",
              engineOut.candidateSetVersion,
              Utc.now());
      merchants = gr.merchants();
      trace.addGuard(
          Map.of("ok", gr.ok(), "errors", gr.errors(), "soft_dropped", gr.softDropped()));
      if (merchants.isEmpty()) {
        status = TaskStatus.empty;
        fallbackReason = fallbackReason != null ? fallbackReason : "guard_empty";
      }
    }

    if (!merchants.isEmpty()) {
      var delivery =
          policy.checkDelivery(
              snapshot, merchants.stream().map(m -> m.merchantId).toList());
      if (!delivery.ok()) {
        RecommendationResult result = new RecommendationResult();
        result.taskId = taskId;
        result.status = TaskStatus.skipped;
        result.routeMode = route.mode();
        result.opportunityResult = opp.result();
        result.decisionReasonCode = delivery.reasonCode();
        result.merchants = List.of();
        result.metrics.put("model_calls", modelCalls);
        result.metrics.put("filter_stats", engineOut.filterStats);
        result.metrics.put("latency_ms", trace.summary().get("latency_ms"));
        persist(taskId, req, key, result, trace);
        return result;
      }
    }

    if (!merchants.isEmpty()
        && (status == TaskStatus.succeeded || status == TaskStatus.degraded)) {
      Map<String, CandidateMerchant> byId = new HashMap<>();
      for (CandidateMerchant c : engineOut.candidates) {
        byId.put(c.merchantId, c);
      }
      List<String> cats = new ArrayList<>();
      for (RecommendedMerchant m : merchants) {
        CandidateMerchant c = byId.get(m.merchantId);
        if (c != null) {
          cats.add(c.category);
        }
      }
      store.recordDelivery(
          req.userId(),
          taskId,
          merchants.stream().map(m -> m.merchantId).toList(),
          cats,
          Utc.toIso(started));
      store.recordSceneSeen(req.userId(), sceneKey, Utc.toIso(started));
    }

    RecommendationResult result = new RecommendationResult();
    result.taskId = taskId;
    result.status = status;
    result.routeMode = route.mode();
    result.opportunityResult = opp.result();
    result.decisionReasonCode = route.reasonCode();
    result.fallbackReason = fallbackReason;
    result.source = source;
    result.merchants = merchants;
    result.metrics.put("model_calls", modelCalls);
    result.metrics.put("filter_stats", engineOut.filterStats);
    result.metrics.put("candidate_set_version", engineOut.candidateSetVersion);
    result.metrics.put("intent_conflict", engineOut.intentConflict);
    result.metrics.put("latency_ms", trace.summary().get("latency_ms"));
    result.metrics.put("run_id", trace.runId);
    result.metrics.put("deadline_at", Utc.toIso(deadlineAt));
    persist(taskId, req, key, result, trace);
    return result;
  }

  private List<RecommendedMerchant> ruleEngineResult(List<CandidateMerchant> baseline) {
    List<RecommendedMerchant> out = new ArrayList<>();
    for (CandidateMerchant c :
        baseline.subList(0, Math.min(settings.getMaxMerchants(), baseline.size()))) {
      if (c.products == null || c.products.isEmpty()) {
        continue;
      }
      CandidateProduct p = c.products.get(0);
      String eid =
          c.evidenceId != null && !c.evidenceId.isBlank()
              ? c.evidenceId
              : "cand_" + c.merchantId;
      out.add(
          new RecommendedMerchant(
              c.merchantId,
              c.name,
              Policy.templateReason(c),
              List.of(eid),
              List.of(
                  new RecommendedProduct(
                      p.productId,
                      p.name,
                      "有库存，价格 " + ((int) p.price) + " 元",
                      List.of("prod_" + p.productId)))));
    }
    return out;
  }

  private void persist(
      String taskId,
      CreateTaskRequest req,
      String key,
      RecommendationResult result,
      TraceRecorder trace)
      throws Exception {
    @SuppressWarnings("unchecked")
    Map<String, Object> payload = mapper.convertValue(result, Map.class);
    payload.put("request", mapper.convertValue(req, Map.class));
    Map<String, Object> traceSummary = new LinkedHashMap<>();
    traceSummary.put("run_id", trace.runId);
    traceSummary.put("route_mode", trace.routeMode);
    traceSummary.put("opportunity_result", trace.opportunityResult);
    traceSummary.put(
        "decision_reason_code",
        trace.decisionReasonCode != null
            ? trace.decisionReasonCode
            : result.decisionReasonCode);
    traceSummary.put(
        "fallback_reason",
        trace.fallbackReason != null ? trace.fallbackReason : result.fallbackReason);
    Object mc = result.metrics.getOrDefault("model_calls", 0);
    traceSummary.put(
        "model_calls",
        trace.modelCalls > 0
            ? trace.modelCalls
            : (mc instanceof Number n ? n.intValue() : 0));
    traceSummary.put("latency_ms", trace.summary().get("latency_ms"));
    traceSummary.put("guard_results", trace.guardResults);
    payload.put("trace_summary", traceSummary);
    store.saveTask(
        taskId, req.tenantId(), req.userId(), key, result.status.name(), payload);
    if (!trace.steps.isEmpty()) {
      store.saveSteps(taskId, trace.steps);
    }
  }

  public Map<String, Object> getTask(String taskId) throws Exception {
    return store.getTask(taskId);
  }

  public Map<String, Object> getTrace(String taskId) throws Exception {
    Map<String, Object> task = store.getTask(taskId);
    List<Map<String, Object>> steps = store.getSteps(taskId);
    Map<String, Object> out = new LinkedHashMap<>();
    out.put("task", task);
    out.put("steps", steps);
    out.put("trace_summary", task == null ? null : task.get("trace_summary"));
    return out;
  }

  public Map<String, Object> feedback(String taskId, FeedbackRequest body) throws Exception {
    return feedbackSvc.apply(taskId, body);
  }

  @SuppressWarnings("unchecked")
  private RecommendationResult mapExisting(Map<String, Object> existing) {
    return mapper.convertValue(existing, RecommendationResult.class);
  }
}
