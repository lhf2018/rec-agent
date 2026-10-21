package com.recagent.tools;

import com.recagent.catalog.Catalog;
import com.recagent.config.AppSettings;
import com.recagent.engine.RecommendationEngine;
import com.recagent.model.Domain.CandidateMerchant;
import com.recagent.model.Domain.CandidateProduct;
import com.recagent.model.Domain.DroppedItem;
import com.recagent.model.Domain.EngineOutput;
import com.recagent.model.Domain.Merchant;
import com.recagent.model.Domain.Product;
import com.recagent.model.Domain.Scene;
import com.recagent.model.Domain.UserSnapshot;
import com.recagent.runtime.registry.ToolRegistry;
import com.recagent.store.Store;
import com.recagent.store.Store.ExposureItem;
import com.recagent.time.Utc;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.springframework.stereotype.Component;

@Component
public class ToolExecutor {
  private final Catalog catalog;
  private final RecommendationEngine engine;
  private final Store store;
  private final AppSettings settings;
  private final ToolRegistry registry = new ToolRegistry();
  private boolean supplementUsed;

  public ToolExecutor(
      Catalog catalog, RecommendationEngine engine, Store store, AppSettings settings) {
    this.catalog = catalog;
    this.engine = engine;
    this.store = store;
    this.settings = settings;
  }

  public void reset() {
    supplementUsed = false;
  }

  public Map<String, Object> execute(
      String name,
      Map<String, Object> args,
      Set<String> allowedIds,
      UserSnapshot snapshot,
      Scene scene,
      List<CandidateMerchant> candidates)
      throws Exception {
    if (!registry.isAllowed(name)) {
      return err("invalid_argument", "unknown tool " + name);
    }
    String argErr = registry.validateArgs(name, args);
    if (argErr != null) {
      return err("invalid_argument", argErr);
    }
    return switch (name) {
      case "get_merchant_detail" -> getMerchantDetail(args, allowedIds, candidates);
      case "get_product_detail" -> getProductDetail(args, allowedIds, candidates);
      case "compare_candidates" -> compareCandidates(args, allowedIds, candidates);
      case "get_exposure_history" -> getExposureHistory(args, snapshot);
      case "supplement_recall" -> supplementRecall(args, snapshot, scene, candidates);
      default -> err("invalid_argument", "unknown tool " + name);
    };
  }

  private Map<String, Object> getMerchantDetail(
      Map<String, Object> args, Set<String> allowedIds, List<CandidateMerchant> candidates) {
    String mid = str(args.get("merchant_id"));
    if (!allowedIds.contains(mid)) {
      return err("not_in_allowed_set", "merchant not allowed");
    }
    Merchant m = catalog.getMerchant(mid);
    if (m == null) {
      return err("not_found", "missing");
    }
    CandidateMerchant cand =
        candidates.stream().filter(c -> c.merchantId.equals(mid)).findFirst().orElse(null);
    Map<String, Object> data = new LinkedHashMap<>();
    data.put("merchant_id", mid);
    data.put("name", m.name);
    data.put("category", m.category);
    data.put("avg_price", m.avgPrice);
    data.put("distance_m", cand != null ? cand.distanceM : null);
    data.put("is_open", m.isOpen);
    data.put("tags", m.tags);
    data.put("score", m.score);
    return ok("detail_" + mid, data);
  }

  private Map<String, Object> getProductDetail(
      Map<String, Object> args, Set<String> allowedIds, List<CandidateMerchant> candidates) {
    String pid = str(args.get("product_id"));
    for (CandidateMerchant c : candidates) {
      for (CandidateProduct p : c.products) {
        if (!p.productId.equals(pid)) {
          continue;
        }
        if (!allowedIds.contains(c.merchantId)) {
          return err("not_in_allowed_set", "not allowed");
        }
        Merchant m = catalog.getMerchant(c.merchantId);
        Product raw =
            m == null
                ? null
                : m.products.stream()
                    .filter(x -> x.productId.equals(pid))
                    .findFirst()
                    .orElse(null);
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("product_id", pid);
        data.put("merchant_id", c.merchantId);
        data.put("name", p.name);
        data.put("price", p.price);
        data.put("in_stock", p.inStock);
        data.put("dietary_tags", p.dietaryTags);
        data.put("untrusted_text", raw != null ? raw.description : "");
        return ok("prod_" + pid, data);
      }
    }
    return err("not_found", "product missing");
  }

  @SuppressWarnings("unchecked")
  private Map<String, Object> compareCandidates(
      Map<String, Object> args, Set<String> allowedIds, List<CandidateMerchant> candidates) {
    String left = str(args.get("left_id"));
    String right = str(args.get("right_id"));
    List<String> aspects = List.of("price", "distance", "category");
    if (args.get("aspects") instanceof List<?> list) {
      aspects = list.stream().map(String::valueOf).toList();
    }
    if (!allowedIds.contains(left) || !allowedIds.contains(right)) {
      return err("not_in_allowed_set", "ids not allowed");
    }
    CandidateMerchant lc =
        candidates.stream().filter(c -> c.merchantId.equals(left)).findFirst().orElse(null);
    CandidateMerchant rc =
        candidates.stream().filter(c -> c.merchantId.equals(right)).findFirst().orElse(null);
    if (lc == null || rc == null) {
      return err("not_found", "candidate missing");
    }
    Map<String, Object> data = new LinkedHashMap<>();
    data.put("left_id", left);
    data.put("right_id", right);
    Map<String, Object> aspectMap = new LinkedHashMap<>();
    for (String a : aspects) {
      Map<String, Object> sides = new LinkedHashMap<>();
      switch (a) {
        case "price" -> {
          sides.put("left", lc.avgPrice);
          sides.put("right", rc.avgPrice);
        }
        case "distance" -> {
          sides.put("left", lc.distanceM);
          sides.put("right", rc.distanceM);
        }
        case "category" -> {
          sides.put("left", lc.category);
          sides.put("right", rc.category);
        }
        case "open_now" -> {
          sides.put("left", lc.isOpen);
          sides.put("right", rc.isOpen);
        }
        case "score" -> {
          sides.put("left", lc.score);
          sides.put("right", rc.score);
        }
        default -> {}
      }
      if (!sides.isEmpty()) {
        aspectMap.put(a, sides);
      }
    }
    data.put("aspects", aspectMap);
    return ok("cmp_" + left + "_" + right, data);
  }

  private Map<String, Object> getExposureHistory(Map<String, Object> args, UserSnapshot snapshot)
      throws Exception {
    int limit = 10;
    if (args.get("limit") instanceof Number n) {
      limit = n.intValue();
    }
    List<Map<String, Object>> items = new ArrayList<>();
    if (store != null) {
      var since = Utc.now().minus(Duration.ofHours(settings.getExposureHistoryWindowHours()));
      for (ExposureItem it : store.listExposureMerchants(snapshot.userId, since, limit, null)) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("merchant_id", it.merchantId());
        row.put("event_type", it.eventType());
        row.put("event_time", it.eventTime());
        items.add(row);
      }
    } else {
      for (String mid :
          snapshot.runtime.exposureMerchantIds.subList(
              0, Math.min(limit, snapshot.runtime.exposureMerchantIds.size()))) {
        items.add(Map.of("merchant_id", mid, "event_type", "impression"));
      }
      for (String mid :
          snapshot.hard.notInterestedMerchants.subList(
              0, Math.min(limit, snapshot.hard.notInterestedMerchants.size()))) {
        items.add(Map.of("merchant_id", mid, "event_type", "not_interested"));
      }
    }
    items = items.stream().filter(x -> x.get("merchant_id") != null).toList();
    return ok("expo_1", Map.of("items", items));
  }

  private Map<String, Object> supplementRecall(
      Map<String, Object> args,
      UserSnapshot snapshot,
      Scene scene,
      List<CandidateMerchant> candidates) {
    if (supplementUsed) {
      return err("supplement_already_used", "already used");
    }
    String strategy = args.get("strategy") == null ? "widen_radius" : str(args.get("strategy"));
    if (!Set.of("widen_radius", "adjacent_category", "trending").contains(strategy)) {
      return err("invalid_argument", "bad strategy");
    }
    supplementUsed = true;
    Set<String> existing =
        candidates.stream().map(c -> c.merchantId).collect(java.util.stream.Collectors.toSet());
    EngineOutput out = engine.supplement(snapshot, scene, strategy, existing);
    List<Map<String, Object>> newCands = new ArrayList<>();
    for (CandidateMerchant c : out.candidates) {
      newCands.add(candidateToMap(c));
    }
    List<Map<String, Object>> dropped = new ArrayList<>();
    for (DroppedItem d : out.dropped) {
      dropped.add(Map.of("merchant_id", d.merchantId(), "reason", d.reason()));
    }
    Map<String, Object> data = new LinkedHashMap<>();
    data.put("strategy", strategy);
    data.put("new_candidates", newCands);
    data.put("dropped", dropped);
    data.put("candidate_set_version", out.candidateSetVersion);
    return ok("supp_batch", data);
  }

  private Map<String, Object> candidateToMap(CandidateMerchant c) {
    Map<String, Object> m = new LinkedHashMap<>();
    m.put("merchant_id", c.merchantId);
    m.put("name", c.name);
    m.put("category", c.category);
    m.put("avg_price", c.avgPrice);
    m.put("distance_m", c.distanceM);
    m.put("is_open", c.isOpen);
    m.put("score", c.score);
    m.put("tags", c.tags);
    m.put("recall_sources", c.recallSources);
    m.put("evidence_id", c.evidenceId);
    m.put("provenance", c.provenance);
    m.put("observed_at", c.observedAt);
    m.put("expires_at", c.expiresAt);
    List<Map<String, Object>> products = new ArrayList<>();
    for (CandidateProduct p : c.products) {
      Map<String, Object> pm = new LinkedHashMap<>();
      pm.put("product_id", p.productId);
      pm.put("name", p.name);
      pm.put("price", p.price);
      pm.put("in_stock", p.inStock);
      pm.put("dietary_tags", p.dietaryTags);
      pm.put("score", p.score);
      products.add(pm);
    }
    m.put("products", products);
    return m;
  }

  private static Map<String, Object> ok(String evidenceId, Map<String, Object> data) {
    Map<String, Object> out = new LinkedHashMap<>();
    out.put("ok", true);
    out.put("evidence_id", evidenceId);
    out.put("data", data);
    return out;
  }

  private static Map<String, Object> err(String code, String message) {
    Map<String, Object> out = new HashMap<>();
    out.put("ok", false);
    out.put("error_code", code);
    out.put("message", message);
    return out;
  }

  private static String str(Object o) {
    return o == null ? null : String.valueOf(o);
  }
}
