package com.recagent.runtime.guard;

import com.recagent.config.AppSettings;
import com.recagent.model.Domain.CandidateMerchant;
import com.recagent.model.Domain.CandidateProduct;
import com.recagent.model.Domain.RecommendedMerchant;
import com.recagent.model.Domain.RecommendedProduct;
import com.recagent.time.Utc;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import org.springframework.stereotype.Component;

@Component
public class Guard {
  private static final Set<String> MERCHANT_FACT_KEYS =
      Set.of("merchant_id", "category", "avg_price", "distance_m", "is_open", "name", "tags", "score");
  private static final Set<String> PRODUCT_FACT_KEYS =
      Set.of("product_id", "merchant_id", "price", "in_stock", "dietary_tags", "name");

  private static final Pattern CLAIM_NUM =
      Pattern.compile("(价格|人均|距离)\\s*(?:约\\s*)?(\\d+(?:\\.\\d+)?)");
  private static final Pattern UNIT_ALIAS =
      Pattern.compile("\\d+(?:\\.\\d+)?\\s*(?:块|rmb|RMB|km|KM)(?!\\w)");
  private static final Pattern YUAN = Pattern.compile("(\\d+(?:\\.\\d+)?)\\s*元");
  private static final Pattern METERS = Pattern.compile("(\\d+)\\s*米");

  private final AppSettings settings;

  public Guard(AppSettings settings) {
    this.settings = settings;
  }

  public record GuardResult(
      boolean ok,
      List<RecommendedMerchant> merchants,
      List<String> errors,
      List<String> softDropped) {}

  public Map<String, Evidence> buildEvidenceIndex(
      List<CandidateMerchant> candidates, String candidateSetVersion) {
    Map<String, Evidence> idx = new LinkedHashMap<>();
    String version = candidateSetVersion != null ? candidateSetVersion : "";
    for (CandidateMerchant c : candidates) {
      String eid =
          c.evidenceId != null && !c.evidenceId.isBlank()
              ? c.evidenceId
              : "cand_" + c.merchantId;
      Map<String, Object> facts = new LinkedHashMap<>();
      facts.put("merchant_id", c.merchantId);
      facts.put("category", c.category);
      facts.put("avg_price", c.avgPrice);
      facts.put("distance_m", c.distanceM);
      facts.put("is_open", c.isOpen);
      facts.put("name", c.name);
      idx.put(
          eid,
          new Evidence(eid, facts, c.provenance, c.expiresAt, "merchant", c.merchantId, version));
      for (CandidateProduct p : c.products) {
        String peid = "prod_" + p.productId;
        Map<String, Object> pf = new LinkedHashMap<>();
        pf.put("product_id", p.productId);
        pf.put("merchant_id", c.merchantId);
        pf.put("price", p.price);
        pf.put("in_stock", p.inStock);
        pf.put("dietary_tags", p.dietaryTags);
        pf.put("name", p.name);
        idx.put(
            peid,
            new Evidence(peid, pf, c.provenance, c.expiresAt, "product", p.productId, version));
      }
    }
    idx.put(
        "snap_history",
        new Evidence(
            "snap_history",
            Map.of("type", "snapshot"),
            "snapshot",
            "",
            "snapshot",
            "history",
            ""));
    idx.put(
        "snap_price_band",
        new Evidence(
            "snap_price_band",
            Map.of("type", "price_band"),
            "snapshot",
            "",
            "snapshot",
            "price_band",
            ""));
    return idx;
  }

  public GuardResult validateSubmission(
      List<RecommendedMerchant> merchants,
      Set<String> allowedIds,
      Map<String, CandidateMerchant> candidatesById,
      Map<String, Evidence> evidenceIndex,
      String decision,
      String candidateSetVersion,
      Instant now) {
    if ("skip".equals(decision)) {
      return new GuardResult(true, List.of(), List.of(), List.of());
    }
    Instant clock = now != null ? now : Utc.now();
    List<String> errors = new ArrayList<>();
    List<RecommendedMerchant> kept = new ArrayList<>();
    List<RecommendedMerchant> working = merchants;

    if (working == null || working.isEmpty()) {
      return new GuardResult(false, List.of(), List.of("empty_merchants"), List.of());
    }
    if (working.size() > settings.getMaxMerchants()) {
      errors.add("too_many_merchants");
      working = working.subList(0, settings.getMaxMerchants());
    }

    for (RecommendedMerchant m : working) {
      if (!allowedIds.contains(m.merchantId)) {
        errors.add("not_in_allowed:" + m.merchantId);
        continue;
      }
      CandidateMerchant cand = candidatesById.get(m.merchantId);
      if (cand == null) {
        errors.add("missing_candidate:" + m.merchantId);
        continue;
      }
      if (m.reason == null || m.reason.isBlank()) {
        errors.add("empty_reason:" + m.merchantId);
        continue;
      }
      String evErr =
          checkEvidenceRefs(
              m.evidenceIds,
              evidenceIndex,
              "merchant",
              m.merchantId,
              candidateSetVersion,
              clock,
              true,
              true,
              null);
      if (evErr != null) {
        errors.add(evErr);
        continue;
      }
      String factErr =
          reasonFactMismatchMerchant(m.reason, m.evidenceIds, evidenceIndex, m.merchantId);
      if (factErr != null) {
        errors.add(factErr);
        continue;
      }

      List<RecommendedProduct> products = new ArrayList<>();
      List<RecommendedProduct> srcProducts =
          m.products == null
              ? List.of()
              : m.products.subList(
                  0, Math.min(settings.getMaxProductsPerMerchant(), m.products.size()));
      for (RecommendedProduct p : srcProducts) {
        boolean ownerOk =
            cand.products.stream().anyMatch(cp -> cp.productId.equals(p.productId));
        if (!ownerOk) {
          errors.add("product_wrong_merchant:" + p.productId);
          continue;
        }
        String pev =
            checkEvidenceRefs(
                p.evidenceIds,
                evidenceIndex,
                "product",
                p.productId,
                candidateSetVersion,
                clock,
                false,
                true,
                m.merchantId);
        if (pev != null) {
          errors.add(pev);
          continue;
        }
        if (p.reason != null && !p.reason.isBlank()) {
          String perr =
              reasonFactMismatchProduct(p.reason, p.evidenceIds, evidenceIndex, p.productId);
          if (perr != null) {
            errors.add(perr);
            continue;
          }
        }
        String name = p.name;
        if (name == null || name.isBlank()) {
          name =
              cand.products.stream()
                  .filter(cp -> cp.productId.equals(p.productId))
                  .map(cp -> cp.name)
                  .findFirst()
                  .orElse("");
        }
        products.add(new RecommendedProduct(p.productId, name, p.reason, p.evidenceIds));
      }
      if (products.isEmpty()) {
        errors.add("no_products:" + m.merchantId);
        continue;
      }
      String mName = m.name != null && !m.name.isBlank() ? m.name : cand.name;
      kept.add(
          new RecommendedMerchant(m.merchantId, mName, m.reason, m.evidenceIds, products));
    }

    DiversityResult trimmed = diversityTrim(kept, candidatesById);
    if (trimmed.kept().isEmpty()) {
      List<String> errs = errors.isEmpty() ? List.of("diversity_empty") : errors;
      return new GuardResult(false, List.of(), errs, trimmed.dropped());
    }
    return new GuardResult(true, trimmed.kept(), errors, trimmed.dropped());
  }

  private String checkEvidenceRefs(
      List<String> evidenceIds,
      Map<String, Evidence> evidenceIndex,
      String expType,
      String expId,
      String candidateSetVersion,
      Instant now,
      boolean allowTool,
      boolean allowSnapshot,
      String merchantId) {
    if (evidenceIds == null || evidenceIds.isEmpty()) {
      return "missing_evidence:empty";
    }
    for (String eid : evidenceIds) {
      if (!evidenceIndex.containsKey(eid)) {
        return "missing_evidence:" + eid;
      }
      Evidence ev = evidenceIndex.get(eid);
      if ("snapshot".equals(ev.subjectType)) {
        if (!allowSnapshot) {
          return "evidence_subject:" + eid;
        }
        continue;
      }
      if ("tool".equals(ev.subjectType)) {
        if (!allowTool) {
          return "evidence_subject:" + eid;
        }
        continue;
      }
      if (ev.expiresAt != null && !ev.expiresAt.isBlank()) {
        Instant exp = Utc.parse(ev.expiresAt);
        if (exp != null && !exp.isAfter(now)) {
          return "evidence_expired:" + eid;
        }
      }
      if (candidateSetVersion != null
          && !candidateSetVersion.isBlank()
          && ev.candidateSetVersion != null
          && !ev.candidateSetVersion.isBlank()
          && !ev.candidateSetVersion.equals(candidateSetVersion)) {
        return "evidence_version:" + eid;
      }
      if (ev.subjectType.equals(expType)) {
        if (!ev.subjectId.equals(expId)) {
          return "evidence_ownership:" + eid;
        }
      } else if ("product".equals(expType) && "merchant".equals(ev.subjectType)) {
        if (merchantId != null && !ev.subjectId.equals(merchantId)) {
          return "evidence_ownership:" + eid;
        }
        return "evidence_subject:" + eid;
      } else {
        return "evidence_subject:" + eid;
      }
    }
    return null;
  }

  @SuppressWarnings("unchecked")
  private Map<String, Object> merchantBag(
      List<String> evidenceIds, Map<String, Evidence> evidenceIndex, String merchantId) {
    Map<String, Object> bag = new HashMap<>();
    for (String eid : evidenceIds) {
      Evidence ev = evidenceIndex.get(eid);
      if (ev == null) {
        continue;
      }
      if ("merchant".equals(ev.subjectType) && merchantId.equals(ev.subjectId)) {
        for (Map.Entry<String, Object> e : ev.facts.entrySet()) {
          if (MERCHANT_FACT_KEYS.contains(e.getKey())) {
            bag.put(e.getKey(), e.getValue());
          }
        }
      } else if ("tool".equals(ev.subjectType)) {
        Object dataObj = ev.facts.get("data");
        Map<String, Object> data =
            dataObj instanceof Map<?, ?> m ? (Map<String, Object>) m : ev.facts;
        Object aspectsObj = data.get("aspects");
        if (!(aspectsObj instanceof Map<?, ?> aspects)) {
          continue;
        }
        for (Map.Entry<?, ?> aspectEntry : aspects.entrySet()) {
          String aspect = String.valueOf(aspectEntry.getKey());
          if (!(aspectEntry.getValue() instanceof Map<?, ?> sides)) {
            continue;
          }
          String leftId = data.get("left_id") == null ? null : String.valueOf(data.get("left_id"));
          String rightId =
              data.get("right_id") == null ? null : String.valueOf(data.get("right_id"));
          if ("price".equals(aspect)) {
            if (merchantId.equals(leftId) && sides.containsKey("left")) {
              bag.put("avg_price", sides.get("left"));
            }
            if (merchantId.equals(rightId) && sides.containsKey("right")) {
              bag.put("avg_price", sides.get("right"));
            }
          } else if ("distance".equals(aspect)) {
            if (merchantId.equals(leftId) && sides.containsKey("left")) {
              bag.put("distance_m", sides.get("left"));
            }
            if (merchantId.equals(rightId) && sides.containsKey("right")) {
              bag.put("distance_m", sides.get("right"));
            }
          } else if ("open_now".equals(aspect)) {
            if (merchantId.equals(leftId) && sides.containsKey("left")) {
              bag.put("is_open", sides.get("left"));
            }
            if (merchantId.equals(rightId) && sides.containsKey("right")) {
              bag.put("is_open", sides.get("right"));
            }
          }
        }
      }
    }
    return bag;
  }

  private Map<String, Object> productBag(
      List<String> evidenceIds, Map<String, Evidence> evidenceIndex, String productId) {
    Map<String, Object> bag = new HashMap<>();
    for (String eid : evidenceIds) {
      Evidence ev = evidenceIndex.get(eid);
      if (ev == null) {
        continue;
      }
      if ("product".equals(ev.subjectType) && productId.equals(ev.subjectId)) {
        for (Map.Entry<String, Object> e : ev.facts.entrySet()) {
          if (PRODUCT_FACT_KEYS.contains(e.getKey())) {
            bag.put(e.getKey(), e.getValue());
          }
        }
      }
    }
    return bag;
  }

  private String rejectUnitlessClaims(String reason) {
    Matcher match = CLAIM_NUM.matcher(reason);
    while (match.find()) {
      String rest = reason.substring(match.end());
      if (!Pattern.compile("^\\s*(?:元|米)").matcher(rest).find()) {
        return "reason_fact_mismatch:unitless";
      }
    }
    if (UNIT_ALIAS.matcher(reason).find()) {
      return "reason_fact_mismatch:unit_alias";
    }
    return null;
  }

  private String reasonFactMismatchMerchant(
      String reason,
      List<String> evidenceIds,
      Map<String, Evidence> evidenceIndex,
      String merchantId) {
    String unitErr = rejectUnitlessClaims(reason);
    if (unitErr != null) {
      return unitErr;
    }
    Map<String, Object> bag = merchantBag(evidenceIds, evidenceIndex, merchantId);
    Matcher yuan = YUAN.matcher(reason);
    while (yuan.find()) {
      double val = Double.parseDouble(yuan.group(1));
      if (!bag.containsKey("avg_price")) {
        return "reason_fact_mismatch:price_bag";
      }
      double avg = ((Number) bag.get("avg_price")).doubleValue();
      if (Math.abs(avg - val) > Math.max(5.0, avg * 0.15)) {
        return "reason_fact_mismatch:price";
      }
    }
    Matcher meters = METERS.matcher(reason);
    while (meters.find()) {
      double val = Double.parseDouble(meters.group(1));
      Object distObj = bag.get("distance_m");
      if (distObj == null) {
        return "reason_fact_mismatch:distance_bag";
      }
      double dist = ((Number) distObj).doubleValue();
      if (Math.abs(dist - val) > Math.max(200.0, dist * 0.2)) {
        return "reason_fact_mismatch:distance";
      }
    }
    if (reason.contains("营业") && Boolean.FALSE.equals(bag.get("is_open"))) {
      return "reason_fact_mismatch:open";
    }
    return null;
  }

  private String reasonFactMismatchProduct(
      String reason,
      List<String> evidenceIds,
      Map<String, Evidence> evidenceIndex,
      String productId) {
    String unitErr = rejectUnitlessClaims(reason);
    if (unitErr != null) {
      return unitErr;
    }
    Map<String, Object> bag = productBag(evidenceIds, evidenceIndex, productId);
    Matcher yuan = YUAN.matcher(reason);
    while (yuan.find()) {
      double val = Double.parseDouble(yuan.group(1));
      if (!bag.containsKey("price")) {
        return "reason_fact_mismatch:price_bag";
      }
      double price = ((Number) bag.get("price")).doubleValue();
      if (Math.abs(price - val) > Math.max(5.0, price * 0.15)) {
        return "reason_fact_mismatch:price";
      }
    }
    if (reason.contains("有库存") && Boolean.FALSE.equals(bag.get("in_stock"))) {
      return "reason_fact_mismatch:stock";
    }
    if (METERS.matcher(reason).find()) {
      return "reason_fact_mismatch:price_bag";
    }
    return null;
  }

  private DiversityResult diversityTrim(
      List<RecommendedMerchant> merchants, Map<String, CandidateMerchant> candidatesById) {
    Map<String, Integer> counts = new HashMap<>();
    List<RecommendedMerchant> out = new ArrayList<>();
    List<String> dropped = new ArrayList<>();
    for (RecommendedMerchant m : merchants) {
      CandidateMerchant cand = candidatesById.get(m.merchantId);
      String cat = cand != null ? cand.category : "";
      if (counts.getOrDefault(cat, 0) >= settings.getMaxSameCategory()) {
        dropped.add(m.merchantId);
        continue;
      }
      counts.merge(cat, 1, Integer::sum);
      out.add(m);
    }
    return new DiversityResult(out, dropped);
  }

  public List<RecommendedMerchant> baselineToRecommendations(List<CandidateMerchant> baseline) {
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
              "与当前时段和距离匹配，距离约 " + ((int) c.distanceM) + " 米，排序靠前",
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

  private record DiversityResult(List<RecommendedMerchant> kept, List<String> dropped) {}
}
