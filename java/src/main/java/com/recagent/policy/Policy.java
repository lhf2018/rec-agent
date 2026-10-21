package com.recagent.policy;

import com.recagent.config.AppSettings;
import com.recagent.model.Domain.CandidateMerchant;
import com.recagent.model.Domain.EngineOutput;
import com.recagent.model.Domain.Scene;
import com.recagent.model.Domain.ShortTermIntent;
import com.recagent.model.Domain.UserSnapshot;
import com.recagent.model.Enums.OpportunityResult;
import com.recagent.model.Enums.RouteMode;
import com.recagent.runtime.budget.DeadlineBudget;
import com.recagent.time.Utc;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import org.springframework.stereotype.Component;

@Component
public class Policy {
  private final AppSettings settings;

  public Policy(AppSettings settings) {
    this.settings = settings;
  }

  public record EligibilityResult(boolean ok, String reasonCode) {}

  public record OpportunityDecision(OpportunityResult result, String reasonCode) {}

  public record RouteDecision(RouteMode mode, String reasonCode) {}

  public record DeliveryResult(boolean ok, String reasonCode) {}

  public EligibilityResult checkEligibility(UserSnapshot snapshot, Scene scene) {
    if (!snapshot.allowPersonalized) {
      return new EligibilityResult(false, "not_authorized");
    }
    if (snapshot.categoryOptOut.contains("*")
        || snapshot.categoryOptOut.contains("active_recommend")) {
      return new EligibilityResult(false, "category_opt_out");
    }
    int hour = Utc.wallClockHour(scene.occurredAt());
    int start = settings.getDndStartHour();
    int end = settings.getDndEndHour();
    boolean inDnd = start > end ? (hour >= start || hour < end) : (hour >= start && hour < end);
    if (inDnd) {
      return new EligibilityResult(false, "dnd_window");
    }
    if (scene.location() == null) {
      return new EligibilityResult(false, "invalid_scene");
    }
    if (snapshot.runtime.deliveryCountInWindow >= settings.getFreqCapPerDay()) {
      return new EligibilityResult(false, "freq_cap");
    }
    Instant last = Utc.parse(snapshot.runtime.lastSuccessAt);
    if (last != null && settings.getCooldownMinutes() > 0) {
      Instant now = Utc.parse(scene.occurredAt());
      if (now == null) {
        now = Utc.now();
      }
      if (Duration.between(last, now).toMinutes() < settings.getCooldownMinutes()) {
        return new EligibilityResult(false, "cooldown");
      }
    }
    return new EligibilityResult(true, null);
  }

  public OpportunityDecision checkOpportunity(UserSnapshot snapshot, Scene scene) {
    if (snapshot.recentConversions != null && !snapshot.recentConversions.isEmpty()) {
      return new OpportunityDecision(OpportunityResult.suppress, "recent_conversion_suppress");
    }
    if (snapshot.runtime.sceneSeenRecently) {
      return new OpportunityDecision(OpportunityResult.suppress, "duplicate_scene_suppress");
    }
    Instant last = Utc.parse(snapshot.runtime.lastSuccessAt);
    if (last != null
        && snapshot.runtime.recentShownCategories != null
        && !snapshot.runtime.recentShownCategories.isEmpty()
        && snapshot.shortTerm != null
        && !snapshot.shortTerm.isEmpty()) {
      Instant now = Utc.parse(scene.occurredAt());
      if (now == null) {
        now = Utc.now();
      }
      if (Duration.between(last, now).toMinutes() < settings.getSimilarSuppressMinutes()) {
        Set<String> shortTopics = new HashSet<>();
        for (ShortTermIntent i : snapshot.shortTerm) {
          shortTopics.add(i.topic);
        }
        Set<String> shown = new HashSet<>(snapshot.runtime.recentShownCategories);
        shortTopics.retainAll(shown);
        if (!shortTopics.isEmpty()) {
          return new OpportunityDecision(
              OpportunityResult.suppress, "similar_category_suppress");
        }
      }
    }
    if (snapshot.hard.notInterestedMerchants.size() >= 5
        && (snapshot.shortTerm == null || snapshot.shortTerm.isEmpty())
        && (snapshot.frequentMerchants == null || snapshot.frequentMerchants.isEmpty())) {
      return new OpportunityDecision(OpportunityResult.suppress, "sparse_after_negative");
    }
    return new OpportunityDecision(OpportunityResult.recommend_now, "opportunity_ok");
  }

  public RouteDecision route(
      EngineOutput engine,
      boolean budgetOk,
      boolean enableLlm,
      boolean enableReact,
      RouteMode forceMode) {
    if (forceMode != null) {
      return new RouteDecision(forceMode, "force_" + forceMode.name().toLowerCase());
    }
    if (engine.candidates == null || engine.candidates.isEmpty()) {
      return new RouteDecision(RouteMode.SKIP, "no_candidates");
    }
    if (!budgetOk) {
      return new RouteDecision(RouteMode.FALLBACK, "budget_exhausted");
    }
    if (!enableLlm) {
      return new RouteDecision(RouteMode.RULE_ENGINE, "llm_disabled");
    }

    boolean needReact =
        engine.intentConflict
            || engine.needFreshness
            || engine.needCompare
            || engine.supplementNeeded;
    if (needReact && enableReact) {
      String reason = "react_gap";
      if (engine.supplementNeeded
          && !(engine.intentConflict || engine.needCompare || engine.needFreshness)) {
        reason = "supplement_needed";
      } else if (engine.needCompare) {
        reason = "need_compare";
      } else if (engine.intentConflict) {
        reason = "intent_conflict";
      }
      return new RouteDecision(RouteMode.REACT, reason);
    }

    if (engine.factsComplete
        && engine.softRerank
        && !engine.intentConflict
        && !engine.needFreshness
        && !engine.needCompare
        && !engine.supplementNeeded) {
      return new RouteDecision(RouteMode.DIRECT, "soft_rerank");
    }

    if (engine.factsComplete && !engine.intentConflict && !engine.needFreshness) {
      return new RouteDecision(RouteMode.RULE_ENGINE, "facts_complete_no_conflict");
    }
    return new RouteDecision(RouteMode.DIRECT, "need_natural_reason");
  }

  public RouteDecision route(EngineOutput engine) {
    return route(engine, true, true, true, null);
  }

  public DeliveryResult checkDelivery(UserSnapshot snapshot, List<String> merchantIds) {
    if (!snapshot.allowPersonalized) {
      return new DeliveryResult(false, "delivery_blocked_not_authorized");
    }
    for (String mid : merchantIds) {
      if (snapshot.hard.notInterestedMerchants.contains(mid)) {
        return new DeliveryResult(false, "delivery_blocked_not_interested");
      }
      if (snapshot.hard.blockedMerchants.contains(mid)) {
        return new DeliveryResult(false, "delivery_blocked_blocked");
      }
    }
    return new DeliveryResult(true, null);
  }

  public boolean budgetOk(Instant deadlineAt, Instant now) {
    return new DeadlineBudget(deadlineAt, settings).allowModel(now);
  }

  public boolean budgetOk(Instant deadlineAt) {
    return budgetOk(deadlineAt, null);
  }

  public static String templateReason(CandidateMerchant c) {
    return "与当前场景匹配，距离约 "
        + ((int) c.distanceM)
        + " 米，人均约 "
        + ((int) c.avgPrice)
        + " 元，排序靠前";
  }

  public static List<CandidateMerchant> ruleRerank(
      List<CandidateMerchant> candidates, UserSnapshot snapshot, int limit) {
    String shortTopic = null;
    if (snapshot.shortTerm != null && !snapshot.shortTerm.isEmpty()) {
      shortTopic =
          snapshot.shortTerm.stream()
              .max(Comparator.comparingDouble(s -> s.confidence))
              .map(s -> s.topic)
              .orElse(null);
    }
    String shortFinal = shortTopic;
    List<CandidateMerchant> sorted = new ArrayList<>(candidates);
    sorted.sort(
        (a, b) -> {
          int cmp = Integer.compare(scoreKey(b, snapshot, shortFinal), scoreKey(a, snapshot, shortFinal));
          if (cmp != 0) {
            return cmp;
          }
          return Double.compare(b.score, a.score);
        });
    return sorted.subList(0, Math.min(limit, sorted.size()));
  }

  private static int scoreKey(CandidateMerchant c, UserSnapshot snapshot, String shortTopic) {
    int score = 0;
    if (shortTopic != null && shortTopic.equals(c.category)) {
      score += 100;
    }
    for (String t : snapshot.preferredTags) {
      if (c.tags.contains(t) || t.equals(c.category)) {
        score += 50;
        break;
      }
    }
    if (snapshot.frequentMerchants.contains(c.merchantId)) {
      score += 20;
    }
    return score;
  }
}
