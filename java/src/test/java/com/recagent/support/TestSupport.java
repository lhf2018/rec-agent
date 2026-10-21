package com.recagent.support;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.recagent.catalog.Catalog;
import com.recagent.config.AppSettings;
import com.recagent.model.Domain.Location;
import com.recagent.model.Domain.Scene;
import com.recagent.model.Enums.TriggerType;
import com.recagent.service.RecommendationService;
import com.recagent.store.Store;
import java.nio.file.Path;

public final class TestSupport {
  private TestSupport() {}

  public static ObjectMapper mapper() {
    return new ObjectMapper()
        .setPropertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
        .registerModule(new JavaTimeModule());
  }

  public static Catalog catalog() {
    return new Catalog(mapper()).loadNow();
  }

  public static AppSettings settings() {
    AppSettings s = new AppSettings();
    s.setCooldownMinutes(0);
    s.setSimilarSuppressMinutes(0);
    return s;
  }

  public static RecommendationService service(Path dbPath) {
    ObjectMapper mapper = mapper();
    Catalog catalog = new Catalog(mapper).loadNow();
    Store store = new Store(dbPath, mapper);
    return new RecommendationService(catalog, store, settings(), mapper);
  }

  public static RecommendationService service(Path dbPath, AppSettings settings) {
    ObjectMapper mapper = mapper();
    Catalog catalog = new Catalog(mapper).loadNow();
    Store store = new Store(dbPath, mapper);
    return new RecommendationService(catalog, store, settings, mapper);
  }

  public static Scene scene(String sceneId) {
    return scene(sceneId, "2026-10-20T12:10:00+08:00");
  }

  public static Scene scene(String sceneId, String occurredAt) {
    return new Scene(
        sceneId,
        TriggerType.impression,
        "nearby",
        occurredAt,
        new Location(121.47, 31.23, "南京西路"),
        3000,
        "lunch");
  }
}
