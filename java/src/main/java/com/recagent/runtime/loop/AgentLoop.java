package com.recagent.runtime.loop;

import com.recagent.config.AppSettings;
import com.recagent.model.Domain.CandidateMerchant;
import com.recagent.model.Domain.CandidateProduct;
import com.recagent.model.Domain.RecommendedMerchant;
import com.recagent.model.Domain.RecommendedProduct;
import com.recagent.model.Domain.Scene;
import com.recagent.model.Domain.UserSnapshot;
import com.recagent.model.Enums.RouteMode;
import com.recagent.runtime.budget.DeadlineBudget;
import com.recagent.runtime.guard.Evidence;
import com.recagent.runtime.guard.Guard;
import com.recagent.runtime.guard.Guard.GuardResult;
import com.recagent.runtime.trace.TraceRecorder;
import com.recagent.time.Utc;
import com.recagent.tools.ToolExecutor;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.springframework.stereotype.Component;

@Component
public class AgentLoop {
  private final ToolExecutor tools;
  private final Guard guard;
  private final AppSettings settings;

  public AgentLoop(ToolExecutor tools, Guard guard, AppSettings settings) {
    this.tools = tools;
    this.guard = guard;
    this.settings = settings;
  }

  public Map<String, Object> run(
      RouteMode mode,
      UserSnapshot snapshot,
      Scene scene,
      List<CandidateMerchant> candidates,
      TraceRecorder trace,
      Instant deadlineAt,
      String candidateSetVersion,
      boolean needCompare,
      boolean supplementNeeded,
      boolean softRerank)
      throws Exception {
    tools.reset();
    TraceRecorder recorder = trace != null ? trace : new TraceRecorder();
    recorder.setMeta(mode.name(), null, null, null);
    Instant deadline =
        deadlineAt != null
            ? deadlineAt
            : Utc.now().plus(Duration.ofMillis(settings.getTaskTimeoutMs()));
    DeadlineBudget budget = new DeadlineBudget(deadline, settings);
    String version = candidateSetVersion != null ? candidateSetVersion : "";
    List<CandidateMerchant> working = new ArrayList<>(candidates);
    Set<String> allowed = allowedIds(working);
    Map<String, Evidence> evidence = guard.buildEvidenceIndex(working, version);
    Map<String, CandidateMerchant> byId = indexById(working);
    String comparePick = null;
    List<String> toolEvidenceIds = new ArrayList<>();

    if (mode == RouteMode.DIRECT) {
      if (!budget.allowModel()) {
        return deadlineExhausted(recorder, working, evidence);
      }
      recorder.bumpModel();
      List<RecommendedMerchant> draft =
          mockSelect(snapshot, working, false, softRerank, null, null);
      recorder.addStep(
          1,
          "direct_submit",
          "submit_recommendation",
          "ok",
          draft.stream().flatMap(m -> m.evidenceIds.stream()).toList(),
          List.of(Map.of("tool", "submit_recommendation")),
          List.of());
      GuardResult gr =
          guard.validateSubmission(
              draft, allowed, byId, evidence, "recommend", version, Utc.now());
      recorder.addGuard(
          Map.of("ok", gr.ok(), "errors", gr.errors(), "soft_dropped", gr.softDropped()));
      return pack(gr, recorder, working, evidence);
    }

    int maxSteps = settings.getReactMaxSteps();
    boolean compared = false;
    boolean supplemented = false;
    for (int step = 1; step <= maxSteps; step++) {
      if (!budget.allowModel()) {
        return deadlineExhausted(recorder, working, evidence);
      }
      recorder.bumpModel();

      if (needCompare
          && !compared
          && snapshot.shortTerm != null
          && !snapshot.shortTerm.isEmpty()
          && working.size() >= 2) {
        String shortTopic =
            snapshot.shortTerm.stream()
                .max(Comparator.comparingDouble(s -> s.confidence))
                .map(s -> s.topic)
                .orElse("");
        String left = working.get(0).merchantId;
        CandidateMerchant rightCand =
            working.stream().filter(c -> shortTopic.equals(c.category)).findFirst().orElse(null);
        if (rightCand != null && !rightCand.merchantId.equals(left)) {
          if (!budget.allowTool(settings.getToolTimeoutMs())) {
            return deadlineExhausted(recorder, working, evidence);
          }
          Map<String, Object> obs =
              tools.execute(
                  "compare_candidates",
                  Map.of(
                      "left_id",
                      left,
                      "right_id",
                      rightCand.merchantId,
                      "aspects",
                      List.of("price", "distance", "category")),
                  allowed,
                  snapshot,
                  scene,
                  working);
          compared = true;
          if (Boolean.TRUE.equals(obs.get("ok"))) {
            String eid = String.valueOf(obs.get("evidence_id"));
            Map<String, Object> facts = new LinkedHashMap<>();
            facts.put("compare", true);
            facts.put("data", obs.getOrDefault("data", Map.of()));
            evidence.put(
                eid,
                new Evidence(eid, facts, "tool", "", "tool", eid, version));
            toolEvidenceIds.add(eid);
            comparePick = rightCand.merchantId;
          }
          List<String> eids =
              obs.get("evidence_id") != null
                  ? List.of(String.valueOf(obs.get("evidence_id")))
                  : List.of();
          recorder.addStep(
              step,
              "need_compare_short_term_intent",
              "compare_candidates",
              Boolean.TRUE.equals(obs.get("ok")) ? "ok" : "error",
              eids,
              List.of(Map.of("tool", "compare_candidates")),
              List.of(obs));
          continue;
        }
      }

      if (supplementNeeded
          && !supplemented
          && working.size() < settings.getSupplementMin()) {
        if (!budget.allowTool(settings.getSupplementTimeoutMs())) {
          return deadlineExhausted(recorder, working, evidence);
        }
        Map<String, Object> obs =
            tools.execute(
                "supplement_recall",
                Map.of("strategy", "widen_radius"),
                allowed,
                snapshot,
                scene,
                working);
        supplemented = true;
        recorder.addStep(
            step,
            "insufficient_candidates",
            "supplement_recall",
            Boolean.TRUE.equals(obs.get("ok")) ? "ok" : "error",
            List.of(),
            List.of(Map.of("tool", "supplement_recall")),
            List.of(obs));
        if (Boolean.TRUE.equals(obs.get("ok"))) {
          @SuppressWarnings("unchecked")
          Map<String, Object> data = (Map<String, Object>) obs.get("data");
          @SuppressWarnings("unchecked")
          List<Map<String, Object>> newRaw =
              (List<Map<String, Object>>) data.get("new_candidates");
          List<CandidateMerchant> newCands = new ArrayList<>();
          for (Map<String, Object> raw : newRaw) {
            newCands.add(fromMap(raw));
          }
          Object verObj = data.get("candidate_set_version");
          if (verObj != null) {
            version = String.valueOf(verObj);
          }
          working = new ArrayList<>(working);
          working.addAll(newCands);
          allowed = allowedIds(working);
          byId = indexById(working);
          Map<String, Evidence> filtered = new LinkedHashMap<>();
          for (Map.Entry<String, Evidence> e : evidence.entrySet()) {
            Evidence v = e.getValue();
            if ("snapshot".equals(v.subjectType)
                || "tool".equals(v.subjectType)
                || v.candidateSetVersion == null
                || v.candidateSetVersion.isBlank()
                || v.candidateSetVersion.equals(version)) {
              filtered.put(e.getKey(), v);
            }
          }
          evidence = filtered;
          evidence.putAll(guard.buildEvidenceIndex(newCands, version));
          if (obs.get("evidence_id") != null) {
            toolEvidenceIds.add(String.valueOf(obs.get("evidence_id")));
          }
        }
        continue;
      }

      List<RecommendedMerchant> draft =
          mockSelect(snapshot, working, true, false, comparePick, toolEvidenceIds);
      recorder.addStep(
          step,
          "react_submit",
          "submit_recommendation",
          "ok",
          draft.stream().flatMap(m -> m.evidenceIds.stream()).toList(),
          List.of(Map.of("tool", "submit_recommendation")),
          List.of());
      GuardResult gr =
          guard.validateSubmission(
              draft, allowed, byId, evidence, "recommend", version, Utc.now());
      recorder.addGuard(
          Map.of("ok", gr.ok(), "errors", gr.errors(), "soft_dropped", gr.softDropped()));
      return pack(gr, recorder, working, evidence);
    }

    recorder.setMeta(null, null, null, "max_steps");
    Map<String, Object> out = new LinkedHashMap<>();
    out.put("ok", false);
    out.put("merchants", List.of());
    out.put("errors", List.of("max_steps"));
    out.put("steps", recorder.steps);
    out.put("model_calls", recorder.modelCalls);
    out.put("candidates", working);
    out.put("evidence_index", evidence);
    out.put("fallback_reason", "max_steps");
    out.put("trace", recorder.summary());
    return out;
  }

  private Map<String, Object> deadlineExhausted(
      TraceRecorder recorder,
      List<CandidateMerchant> candidates,
      Map<String, Evidence> evidence) {
    recorder.setMeta(null, null, null, "deadline_exhausted");
    recorder.addStep(
        recorder.steps.size() + 1, "deadline_exhausted", null, "budget_exhausted", null, null, null);
    Map<String, Object> out = new LinkedHashMap<>();
    out.put("ok", false);
    out.put("merchants", List.of());
    out.put("errors", List.of("deadline_exhausted"));
    out.put("steps", recorder.steps);
    out.put("model_calls", recorder.modelCalls);
    out.put("candidates", candidates);
    out.put("evidence_index", evidence);
    out.put("fallback_reason", "deadline_exhausted");
    out.put("trace", recorder.summary());
    return out;
  }

  private Map<String, Object> pack(
      GuardResult gr,
      TraceRecorder recorder,
      List<CandidateMerchant> candidates,
      Map<String, Evidence> evidence) {
    Map<String, Object> out = new LinkedHashMap<>();
    out.put("ok", gr.ok());
    out.put("merchants", gr.merchants());
    out.put("errors", gr.errors());
    out.put("steps", recorder.steps);
    out.put("model_calls", recorder.modelCalls);
    out.put("candidates", candidates);
    out.put("evidence_index", evidence);
    out.put("trace", recorder.summary());
    return out;
  }

  private Set<String> allowedIds(List<CandidateMerchant> candidates) {
    Set<String> ids = new HashSet<>();
    for (CandidateMerchant c : candidates) {
      ids.add(c.merchantId);
      for (CandidateProduct p : c.products) {
        ids.add(p.productId);
      }
    }
    return ids;
  }

  private Map<String, CandidateMerchant> indexById(List<CandidateMerchant> candidates) {
    Map<String, CandidateMerchant> byId = new HashMap<>();
    for (CandidateMerchant c : candidates) {
      byId.put(c.merchantId, c);
    }
    return byId;
  }

  private List<RecommendedMerchant> mockSelect(
      UserSnapshot snapshot,
      List<CandidateMerchant> candidates,
      boolean preferShortTerm,
      boolean preferSoftTags,
      String preferMerchantId,
      List<String> extraEvidenceIds) {
    List<CandidateMerchant> ordered = new ArrayList<>(candidates);
    if (preferMerchantId != null
        && snapshot.shortTerm != null
        && !snapshot.shortTerm.isEmpty()) {
      String topic =
          snapshot.shortTerm.stream()
              .max(Comparator.comparingDouble(s -> s.confidence))
              .map(s -> s.topic)
              .orElse("");
      List<CandidateMerchant> preferred =
          candidates.stream().filter(c -> topic.equals(c.category)).toList();
      List<CandidateMerchant> pool = preferred.isEmpty() ? candidates : preferred;
      ordered = new ArrayList<>(pool);
      ordered.sort(
          Comparator.comparingInt((CandidateMerchant c) -> c.merchantId.equals(preferMerchantId) ? 0 : 1)
              .thenComparing(c -> -c.score));
    } else if (preferShortTerm && snapshot.shortTerm != null && !snapshot.shortTerm.isEmpty()) {
      String topic =
          snapshot.shortTerm.stream()
              .max(Comparator.comparingDouble(s -> s.confidence))
              .map(s -> s.topic)
              .orElse("");
      ordered.sort(
          Comparator.comparingInt((CandidateMerchant c) -> topic.equals(c.category) ? 0 : 1)
              .thenComparing(c -> -c.score));
    } else if (preferSoftTags
        && snapshot.preferredTags != null
        && !snapshot.preferredTags.isEmpty()) {
      ordered.sort(
          Comparator.comparingInt(
                  (CandidateMerchant c) ->
                      (c.tags.stream().anyMatch(snapshot.preferredTags::contains)
                              || snapshot.preferredTags.contains(c.category))
                          ? 0
                          : 1)
              .thenComparing(c -> -c.score));
    }
    List<String> extra = extraEvidenceIds != null ? new ArrayList<>(extraEvidenceIds) : List.of();
    List<RecommendedMerchant> out = new ArrayList<>();
    for (CandidateMerchant c : ordered.subList(0, Math.min(3, ordered.size()))) {
      if (c.products == null || c.products.isEmpty()) {
        continue;
      }
      CandidateProduct p = c.products.get(0);
      List<String> eids = new ArrayList<>();
      eids.add(
          c.evidenceId != null && !c.evidenceId.isBlank()
              ? c.evidenceId
              : "cand_" + c.merchantId);
      String reason;
      if (preferMerchantId != null
          && c.merchantId.equals(preferMerchantId)
          && !extra.isEmpty()) {
        eids.addAll(extra);
        reason =
            "对比后更贴合近期兴趣，距离约 "
                + ((int) c.distanceM)
                + " 米，人均约 "
                + ((int) c.avgPrice)
                + " 元";
      } else if (snapshot.frequentMerchants.contains(c.merchantId)) {
        eids.add("snap_history");
        reason =
            "近 30 天偏好相关，当前营业，距离约 " + ((int) c.distanceM) + " 米在配送范围内";
      } else {
        eids.add("snap_history");
        reason =
            "与当前场景匹配，距离约 "
                + ((int) c.distanceM)
                + " 米，人均约 "
                + ((int) c.avgPrice)
                + " 元";
      }
      out.add(
          new RecommendedMerchant(
              c.merchantId,
              c.name,
              reason,
              eids,
              List.of(
                  new RecommendedProduct(
                      p.productId,
                      p.name,
                      "有库存，价格 " + ((int) p.price) + " 元落在可接受区间",
                      List.of("prod_" + p.productId, "snap_price_band")))));
    }
    return out;
  }

  @SuppressWarnings("unchecked")
  private CandidateMerchant fromMap(Map<String, Object> raw) {
    CandidateMerchant c = new CandidateMerchant();
    c.merchantId = str(raw.get("merchant_id"));
    c.name = str(raw.get("name"));
    c.category = str(raw.get("category"));
    c.avgPrice = num(raw.get("avg_price"));
    c.distanceM = num(raw.get("distance_m"));
    c.isOpen = Boolean.TRUE.equals(raw.get("is_open"));
    c.score = num(raw.get("score"));
    if (raw.get("tags") instanceof List<?> tags) {
      c.tags = tags.stream().map(String::valueOf).toList();
    }
    if (raw.get("recall_sources") instanceof List<?> rs) {
      c.recallSources = new ArrayList<>(rs.stream().map(String::valueOf).toList());
    }
    c.evidenceId = raw.get("evidence_id") == null ? "" : str(raw.get("evidence_id"));
    c.provenance = raw.get("provenance") == null ? "primary" : str(raw.get("provenance"));
    c.observedAt = raw.get("observed_at") == null ? "" : str(raw.get("observed_at"));
    c.expiresAt = raw.get("expires_at") == null ? "" : str(raw.get("expires_at"));
    List<CandidateProduct> products = new ArrayList<>();
    if (raw.get("products") instanceof List<?> plist) {
      for (Object o : plist) {
        if (o instanceof Map<?, ?> pm) {
          Map<String, Object> pmap = (Map<String, Object>) pm;
          CandidateProduct p = new CandidateProduct();
          p.productId = str(pmap.get("product_id"));
          p.name = str(pmap.get("name"));
          p.price = num(pmap.get("price"));
          p.inStock = !Boolean.FALSE.equals(pmap.get("in_stock"));
          if (pmap.get("dietary_tags") instanceof List<?> dt) {
            p.dietaryTags = dt.stream().map(String::valueOf).toList();
          }
          products.add(p);
        }
      }
    }
    c.products = products;
    return c;
  }

  private static String str(Object o) {
    return o == null ? null : String.valueOf(o);
  }

  private static double num(Object o) {
    if (o instanceof Number n) {
      return n.doubleValue();
    }
    return 0;
  }
}
