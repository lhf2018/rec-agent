package com.recagent.snapshot;

import com.recagent.catalog.Catalog;
import com.recagent.config.AppSettings;
import com.recagent.model.Domain.HardConstraints;
import com.recagent.model.Domain.RuntimeSignals;
import com.recagent.model.Domain.ShortTermIntent;
import com.recagent.model.Domain.UserSnapshot;
import com.recagent.store.Store;
import com.recagent.store.Store.DeliverySummary;
import com.recagent.store.Store.ExposureItem;
import com.recagent.time.Utc;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;
import org.springframework.stereotype.Component;

@Component
public class SnapshotBuilder {
  private final Catalog catalog;
  private final Store store;
  private final AppSettings settings;

  public SnapshotBuilder(Catalog catalog, Store store, AppSettings settings) {
    this.catalog = catalog;
    this.store = store;
    this.settings = settings;
  }

  public UserSnapshot build(String userId, Instant now, String sceneKey) throws Exception {
    UserSnapshot raw = catalog.getUser(userId);
    if (raw == null) {
      return null;
    }
    UserSnapshot snap = raw.deepCopy();
    Instant clock = now != null ? now : Utc.now();

    List<ShortTermIntent> activeShort = new ArrayList<>();
    for (ShortTermIntent intent : snap.shortTerm) {
      if (isExpired(intent.expiresAt, clock)) {
        continue;
      }
      activeShort.add(intent);
    }
    snap.shortTerm = activeShort;

    List<String> seedNi = new ArrayList<>(snap.hard.notInterestedMerchants);
    List<String> dynamicNi = new ArrayList<>();
    RuntimeSignals runtime = new RuntimeSignals();

    if (store != null) {
      dynamicNi = store.listNotInterested(userId, clock);
      Instant sinceFreq = clock.minus(Duration.ofHours(settings.getFreqWindowHours()));
      runtime.deliveryCountInWindow = store.countDeliveriesSince(userId, sinceFreq);
      DeliverySummary summary = store.getDeliverySummary(userId);
      runtime.lastSuccessAt = summary.lastSuccessAt() == null ? "" : summary.lastSuccessAt();
      runtime.recentShownMerchants = new ArrayList<>(summary.merchantIds());
      runtime.recentShownCategories = new ArrayList<>(summary.categories());
      if (sceneKey != null) {
        runtime.sceneSeenRecently =
            store.sceneSeenWithin(
                userId,
                sceneKey,
                Duration.ofMinutes(settings.getSceneDedupeMinutes()),
                clock);
      }
      Instant sinceExpo = clock.minus(Duration.ofHours(settings.getExposureHistoryWindowHours()));
      List<ExposureItem> items = store.listExposureMerchants(userId, sinceExpo, 50, null);
      Set<String> seen = new LinkedHashSet<>();
      List<String> mids = new ArrayList<>();
      for (ExposureItem it : items) {
        if (seen.add(it.merchantId())) {
          mids.add(it.merchantId());
        }
      }
      runtime.exposureMerchantIds = mids;
      runtime.dynamicNotInterested = new ArrayList<>(dynamicNi);
    }

    Set<String> merged = new LinkedHashSet<>();
    merged.addAll(seedNi);
    merged.addAll(dynamicNi);
    HardConstraints hard = new HardConstraints();
    hard.dietaryForbid = new ArrayList<>(snap.hard.dietaryForbid);
    hard.blockedMerchants = new ArrayList<>(snap.hard.blockedMerchants);
    hard.notInterestedMerchants = new ArrayList<>(merged);
    hard.budgetMin = snap.hard.budgetMin;
    hard.budgetMax = snap.hard.budgetMax;
    snap.hard = hard;
    snap.runtime = runtime;
    snap.builtAt = Utc.toIso(clock);
    return snap;
  }

  private static boolean isExpired(String expiresAt, Instant now) {
    Instant exp = Utc.parse(expiresAt);
    if (exp == null) {
      return false;
    }
    return !exp.isAfter(now);
  }
}
