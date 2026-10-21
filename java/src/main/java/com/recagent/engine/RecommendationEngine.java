package com.recagent.engine;

import com.recagent.catalog.Catalog;
import com.recagent.config.AppSettings;
import com.recagent.model.Domain.CandidateMerchant;
import com.recagent.model.Domain.CandidateProduct;
import com.recagent.model.Domain.DroppedItem;
import com.recagent.model.Domain.EngineOutput;
import com.recagent.model.Domain.HardConstraints;
import com.recagent.model.Domain.Merchant;
import com.recagent.model.Domain.Product;
import com.recagent.model.Domain.Scene;
import com.recagent.model.Domain.ShortTermIntent;
import com.recagent.model.Domain.UserSnapshot;
import com.recagent.time.Utc;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.springframework.stereotype.Component;

@Component
public class RecommendationEngine {
  private final Catalog catalog;
  private final AppSettings settings;

  public RecommendationEngine(Catalog catalog, AppSettings settings) {
    this.catalog = catalog;
    this.settings = settings;
  }

  public EngineOutput run(UserSnapshot snapshot, Scene scene) {
    return run(snapshot, scene, null);
  }

  public EngineOutput run(UserSnapshot snapshot, Scene scene, Integer radiusOverride) {
    int radius = radiusOverride != null ? radiusOverride : scene.radiusM();
    Map<String, CandidateMerchant> recalled = mergeRecalls(snapshot, scene, radius);
    FilterResult filtered = hardFilter(recalled, snapshot, scene, radius);
    List<CandidateMerchant> ranked = rank(filtered.kept, snapshot, scene);
    ranked = diversityCapPool(ranked);
    List<CandidateMerchant> candidates =
        new ArrayList<>(ranked.subList(0, Math.min(settings.getCandidateLimit(), ranked.size())));
    for (CandidateMerchant c : candidates) {
      c.evidenceId = "cand_" + c.merchantId;
      c.observedAt = nowIso();
      c.expiresAt = expiresIso(2);
      c.provenance = "primary";
    }
    List<CandidateMerchant> baseline =
        new ArrayList<>(
            candidates.subList(0, Math.min(settings.getMaxMerchants(), candidates.size())));
    boolean intentConflict = intentConflict(snapshot, candidates);
    boolean factsComplete =
        !candidates.isEmpty()
            && candidates.stream()
                .limit(3)
                .allMatch(c -> c.isOpen && c.distanceM >= 0);
    boolean needCompare = needCompare(snapshot, candidates, intentConflict);
    boolean softRerank = softRerank(snapshot, candidates, intentConflict);
    boolean supplementNeeded = candidates.size() < settings.getSupplementMin();
    String version =
        "csv_" + scene.sceneId() + "_" + candidates.size() + "_" + filtered.dropped.size();

    EngineOutput out = new EngineOutput();
    out.candidates = candidates;
    out.baseline = baseline;
    out.dropped = filtered.dropped;
    out.candidateSetVersion = version;
    out.factsComplete = factsComplete && !candidates.isEmpty();
    out.intentConflict = intentConflict;
    out.needFreshness = false;
    out.needCompare = needCompare;
    out.softRerank = softRerank;
    out.supplementNeeded = supplementNeeded;
    out.filterStats = filtered.stats;
    return out;
  }

  public EngineOutput supplement(
      UserSnapshot snapshot, Scene scene, String strategy, Set<String> existingIds) {
    int radius = scene.radiusM();
    if ("widen_radius".equals(strategy)) {
      radius = (int) (scene.radiusM() * 1.8);
    }
    EngineOutput out = run(snapshot, scene, radius);
    List<CandidateMerchant> newCands = new ArrayList<>();
    for (CandidateMerchant c : out.candidates) {
      if (!existingIds.contains(c.merchantId)) {
        c.provenance = "supplement";
        c.evidenceId = "supp_" + c.merchantId;
        newCands.add(c);
      }
    }
    out.candidates = newCands;
    out.baseline =
        new ArrayList<>(
            newCands.subList(0, Math.min(settings.getMaxMerchants(), newCands.size())));
    out.candidateSetVersion = out.candidateSetVersion + "_supp";
    return out;
  }

  private Map<String, CandidateMerchant> mergeRecalls(
      UserSnapshot snapshot, Scene scene, int radius) {
    Map<String, List<String>> buckets = new LinkedHashMap<>();
    for (String mid : recallSimilar(snapshot)) {
      buckets.computeIfAbsent(mid, k -> new ArrayList<>()).add("similar");
    }
    for (String mid : recallPreference(snapshot)) {
      buckets.computeIfAbsent(mid, k -> new ArrayList<>()).add("preference");
    }
    for (String mid : recallScene(scene, radius)) {
      buckets.computeIfAbsent(mid, k -> new ArrayList<>()).add("scene");
    }
    for (String mid : recallTrending()) {
      buckets.computeIfAbsent(mid, k -> new ArrayList<>()).add("trending");
    }
    Map<String, CandidateMerchant> merged = new LinkedHashMap<>();
    for (Map.Entry<String, List<String>> e : buckets.entrySet()) {
      Merchant m = catalog.getMerchant(e.getKey());
      if (m == null) {
        continue;
      }
      List<String> sources = new ArrayList<>(new LinkedHashSet<>(e.getValue()));
      merged.put(e.getKey(), toCandidate(m, scene, sources));
    }
    return merged;
  }

  private List<String> recallSimilar(UserSnapshot snapshot) {
    List<String> ids = new ArrayList<>(snapshot.frequentMerchants);
    for (Merchant m : catalog.allMerchants()) {
      if (snapshot.longTermCategories.contains(m.category) && !ids.contains(m.merchantId)) {
        ids.add(m.merchantId);
      }
    }
    return ids.subList(0, Math.min(settings.getRecallLimit(), ids.size()));
  }

  private List<String> recallPreference(UserSnapshot snapshot) {
    List<String> ids = new ArrayList<>();
    for (Merchant m : catalog.allMerchants()) {
      boolean tagHit = m.tags.stream().anyMatch(snapshot.preferredTags::contains);
      if (tagHit || snapshot.preferredTags.contains(m.category)) {
        ids.add(m.merchantId);
      }
    }
    for (ShortTermIntent intent : snapshot.shortTerm) {
      for (Merchant m : catalog.allMerchants()) {
        if (intent.topic.equals(m.category) || m.tags.contains(intent.topic)) {
          if (!ids.contains(m.merchantId)) {
            ids.add(m.merchantId);
          }
        }
      }
    }
    return ids.subList(0, Math.min(settings.getRecallLimit(), ids.size()));
  }

  private List<String> recallScene(Scene scene, int radius) {
    List<String> ids = new ArrayList<>();
    for (Merchant m : catalog.allMerchants()) {
      double dist =
          haversineM(scene.location().lng(), scene.location().lat(), m.lng, m.lat);
      if (dist <= radius && m.isOpen) {
        ids.add(m.merchantId);
      }
    }
    return ids.subList(0, Math.min(settings.getRecallLimit(), ids.size()));
  }

  private List<String> recallTrending() {
    List<Merchant> ranked = new ArrayList<>(catalog.allMerchants());
    ranked.sort(Comparator.comparingDouble((Merchant m) -> m.score).reversed());
    List<String> ids = new ArrayList<>();
    for (Merchant m : ranked) {
      ids.add(m.merchantId);
      if (ids.size() >= settings.getRecallLimit()) {
        break;
      }
    }
    return ids;
  }

  private CandidateMerchant toCandidate(Merchant m, Scene scene, List<String> sources) {
    double dist = haversineM(scene.location().lng(), scene.location().lat(), m.lng, m.lat);
    CandidateMerchant c = new CandidateMerchant();
    c.merchantId = m.merchantId;
    c.name = m.name;
    c.category = m.category;
    c.avgPrice = m.avgPrice;
    c.distanceM = Math.round(dist);
    c.isOpen = m.isOpen;
    c.score = m.score;
    c.tags = new ArrayList<>(m.tags);
    c.recallSources = sources;
    List<CandidateProduct> products = new ArrayList<>();
    for (Product p : m.products) {
      CandidateProduct cp = new CandidateProduct();
      cp.productId = p.productId;
      cp.name = p.name;
      cp.price = p.price;
      cp.inStock = p.inStock;
      cp.dietaryTags = new ArrayList<>(p.dietaryTags);
      products.add(cp);
    }
    c.products = products;
    return c;
  }

  private FilterResult hardFilter(
      Map<String, CandidateMerchant> recalled,
      UserSnapshot snapshot,
      Scene scene,
      int radius) {
    List<CandidateMerchant> kept = new ArrayList<>();
    List<DroppedItem> dropped = new ArrayList<>();
    Map<String, Integer> stats = new HashMap<>();
    HardConstraints hard = snapshot.hard;

    for (Map.Entry<String, CandidateMerchant> e : recalled.entrySet()) {
      String mid = e.getKey();
      CandidateMerchant cand = e.getValue();
      Merchant m = catalog.getMerchant(mid);
      if (m == null) {
        continue;
      }
      String reason = null;
      if (!"online".equals(m.status)) {
        reason = "offline";
      } else if (!m.isOpen) {
        reason = "closed";
      } else if (cand.distanceM > radius || cand.distanceM > m.deliveryRadiusM) {
        reason = "out_of_range";
      } else if (hard.blockedMerchants.contains(mid)) {
        reason = "blocked";
      } else if (hard.notInterestedMerchants.contains(mid)) {
        reason = "not_interested";
      } else if (hard.budgetMax != null && m.avgPrice > hard.budgetMax) {
        reason = "price_band";
      } else if (hard.budgetMin != null && m.avgPrice < hard.budgetMin) {
        reason = "price_band";
      } else if (m.tags.stream().anyMatch(hard.dietaryForbid::contains)) {
        reason = "dietary";
      } else {
        List<CandidateProduct> okProducts = new ArrayList<>();
        for (CandidateProduct p : cand.products) {
          if (!p.inStock) {
            continue;
          }
          if (p.dietaryTags.stream().anyMatch(hard.dietaryForbid::contains)) {
            continue;
          }
          if (hard.budgetMax != null && p.price > hard.budgetMax) {
            continue;
          }
          okProducts.add(p);
        }
        if (okProducts.isEmpty()) {
          reason = "out_of_stock";
        } else {
          cand.products = okProducts;
        }
      }
      if (reason != null) {
        dropped.add(new DroppedItem(mid, reason));
        stats.merge(reason, 1, Integer::sum);
      } else {
        kept.add(cand);
      }
    }
    return new FilterResult(kept, dropped, stats);
  }

  private List<CandidateMerchant> rank(
      List<CandidateMerchant> kept, UserSnapshot snapshot, Scene scene) {
    Set<String> shortTopics = new LinkedHashSet<>();
    for (ShortTermIntent i : snapshot.shortTerm) {
      if (i.confidence >= 0.5) {
        shortTopics.add(i.topic);
      }
    }
    List<CandidateMerchant> ranked = new ArrayList<>(kept);
    ranked.sort(
        (a, b) -> Double.compare(scoreOf(b, snapshot, shortTopics), scoreOf(a, snapshot, shortTopics)));
    return ranked;
  }

  private double scoreOf(CandidateMerchant c, UserSnapshot snapshot, Set<String> shortTopics) {
    double s = c.score * 10;
    if (snapshot.frequentMerchants.contains(c.merchantId)) {
      s += 8;
    }
    if (snapshot.longTermCategories.contains(c.category)) {
      s += 5;
    }
    if (shortTopics.contains(c.category)) {
      s += 3;
    }
    if (c.tags.stream().anyMatch(snapshot.preferredTags::contains)) {
      s += 4;
    }
    s += Math.max(0, 5 - c.distanceM / 1000.0);
    if (c.recallSources.contains("trending") && "new".equals(snapshot.lifecycle)) {
      s += 6;
    }
    if (c.recallSources.contains("scene")) {
      s += 2;
    }
    c.score = Math.round(s * 1000.0) / 1000.0;
    return s;
  }

  private List<CandidateMerchant> diversityCapPool(List<CandidateMerchant> ranked) {
    if (ranked.isEmpty()) {
      return ranked;
    }
    int limit = settings.getCandidateLimit();
    int maxPerCat = Math.max(1, limit / 2);
    Map<String, Integer> counts = new HashMap<>();
    List<CandidateMerchant> out = new ArrayList<>();
    List<CandidateMerchant> deferred = new ArrayList<>();
    for (CandidateMerchant c : ranked) {
      int count = counts.getOrDefault(c.category, 0);
      if (count < maxPerCat) {
        out.add(c);
        counts.put(c.category, count + 1);
      } else {
        deferred.add(c);
      }
      if (out.size() >= limit) {
        break;
      }
    }
    if (out.size() < limit) {
      for (CandidateMerchant c : deferred) {
        out.add(c);
        if (out.size() >= limit) {
          break;
        }
      }
    }
    return out;
  }

  private boolean intentConflict(UserSnapshot snapshot, List<CandidateMerchant> candidates) {
    if (candidates.isEmpty() || snapshot.shortTerm == null || snapshot.shortTerm.isEmpty()) {
      return false;
    }
    ShortTermIntent top =
        snapshot.shortTerm.stream().max(Comparator.comparingDouble(s -> s.confidence)).orElse(null);
    if (top == null || top.confidence < 0.8) {
      return false;
    }
    return !top.topic.equals(candidates.get(0).category);
  }

  private boolean needCompare(
      UserSnapshot snapshot, List<CandidateMerchant> candidates, boolean intentConflict) {
    if (!intentConflict
        || snapshot.shortTerm == null
        || snapshot.shortTerm.isEmpty()
        || candidates.size() < 2) {
      return false;
    }
    String topic =
        snapshot.shortTerm.stream()
            .max(Comparator.comparingDouble(s -> s.confidence))
            .map(s -> s.topic)
            .orElse("");
    String head = candidates.get(0).merchantId;
    return candidates.stream()
        .anyMatch(c -> topic.equals(c.category) && !c.merchantId.equals(head));
  }

  private boolean softRerank(
      UserSnapshot snapshot, List<CandidateMerchant> candidates, boolean intentConflict) {
    if (intentConflict || candidates.isEmpty()) {
      return false;
    }
    CandidateMerchant top = candidates.get(0);
    if (snapshot.preferredTags != null && !snapshot.preferredTags.isEmpty()) {
      boolean topMatch =
          top.tags.stream().anyMatch(snapshot.preferredTags::contains)
              || snapshot.preferredTags.contains(top.category);
      if (!topMatch) {
        boolean later =
            candidates.subList(1, candidates.size()).stream()
                .anyMatch(
                    c ->
                        c.tags.stream().anyMatch(snapshot.preferredTags::contains)
                            || snapshot.preferredTags.contains(c.category));
        if (later) {
          return true;
        }
      }
    }
    if ("new".equals(snapshot.lifecycle)) {
      return true;
    }
    if ("gold".equals(snapshot.memberLevel)
        && snapshot.frequentMerchants != null
        && !snapshot.frequentMerchants.isEmpty()) {
      return true;
    }
    return false;
  }

  private static String nowIso() {
    return Utc.nowIso();
  }

  private static String expiresIso(int hours) {
    return Utc.toIso(Utc.now().plus(Duration.ofHours(hours)));
  }

  static double haversineM(double lng1, double lat1, double lng2, double lat2) {
    double r = 6_371_000;
    double p1 = Math.toRadians(lat1);
    double p2 = Math.toRadians(lat2);
    double dphi = Math.toRadians(lat2 - lat1);
    double dlmb = Math.toRadians(lng2 - lng1);
    double a =
        Math.sin(dphi / 2) * Math.sin(dphi / 2)
            + Math.cos(p1) * Math.cos(p2) * Math.sin(dlmb / 2) * Math.sin(dlmb / 2);
    return 2 * r * Math.asin(Math.sqrt(a));
  }

  private record FilterResult(
      List<CandidateMerchant> kept, List<DroppedItem> dropped, Map<String, Integer> stats) {}
}
