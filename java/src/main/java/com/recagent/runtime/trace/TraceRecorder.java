package com.recagent.runtime.trace;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public class TraceRecorder {
  public final String runId;
  private final long startedNs;
  public final List<Map<String, Object>> steps = new ArrayList<>();
  public String routeMode;
  public String opportunityResult;
  public String decisionReasonCode;
  public String fallbackReason;
  public int modelCalls;
  public final List<Map<String, Object>> guardResults = new ArrayList<>();

  public TraceRecorder() {
    this(null);
  }

  public TraceRecorder(String runId) {
    this.runId = runId != null ? runId : "run_" + UUID.randomUUID().toString().replace("-", "").substring(0, 10);
    this.startedNs = System.nanoTime();
  }

  public void setMeta(
      String routeMode, String opportunityResult, String decisionReasonCode, String fallbackReason) {
    if (routeMode != null) {
      this.routeMode = routeMode;
    }
    if (opportunityResult != null) {
      this.opportunityResult = opportunityResult;
    }
    if (decisionReasonCode != null) {
      this.decisionReasonCode = decisionReasonCode;
    }
    if (fallbackReason != null) {
      this.fallbackReason = fallbackReason;
    }
  }

  public void addStep(
      int stepIndex,
      String decisionReasonCode,
      String toolName,
      String resultStatus,
      List<String> evidenceIds,
      List<Map<String, Object>> actions,
      List<Map<String, Object>> observations) {
    Map<String, Object> row = new LinkedHashMap<>();
    row.put("run_id", runId);
    row.put("step_index", stepIndex);
    row.put("route_mode", routeMode);
    row.put("opportunity_result", opportunityResult);
    row.put("decision_reason_code", decisionReasonCode);
    row.put("tool_name", toolName);
    row.put("result_status", resultStatus != null ? resultStatus : "ok");
    row.put("evidence_ids", evidenceIds != null ? evidenceIds : List.of());
    row.put("guard_results", List.of());
    row.put("fallback_reason", fallbackReason);
    row.put("latency_ms", null);
    row.put("model_calls", modelCalls);
    row.put("actions", actions != null ? actions : List.of());
    row.put("observations", observations != null ? observations : List.of());
    steps.add(row);
  }

  public void addGuard(Map<String, Object> result) {
    guardResults.add(result);
    if (!steps.isEmpty()) {
      steps.get(steps.size() - 1).put("guard_results", new ArrayList<>(guardResults));
    }
  }

  public void bumpModel(int n) {
    modelCalls += n;
  }

  public void bumpModel() {
    bumpModel(1);
  }

  public Map<String, Object> summary() {
    Map<String, Object> out = new LinkedHashMap<>();
    out.put("run_id", runId);
    out.put("route_mode", routeMode);
    out.put("opportunity_result", opportunityResult);
    out.put("decision_reason_code", decisionReasonCode);
    out.put("fallback_reason", fallbackReason);
    out.put("model_calls", modelCalls);
    double latencyMs = (System.nanoTime() - startedNs) / 1_000_000.0;
    out.put("latency_ms", Math.round(latencyMs * 100.0) / 100.0);
    out.put("guard_results", guardResults);
    out.put("steps", steps);
    return out;
  }
}
