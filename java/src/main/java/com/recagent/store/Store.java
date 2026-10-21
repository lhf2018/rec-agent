package com.recagent.store;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.recagent.config.AppSettings;
import com.recagent.time.Utc;
import jakarta.annotation.PostConstruct;
import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.Statement;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import org.springframework.stereotype.Component;

@Component
public class Store {
  private final ObjectMapper mapper;
  private final String dbPath;
  private String jdbcUrl;
  private boolean initialized;

  public Store(AppSettings settings, ObjectMapper mapper) {
    this.mapper = mapper;
    this.dbPath = settings.getDbPath();
  }

  /** Test / manual constructor with explicit SQLite path. */
  public Store(Path path, ObjectMapper mapper) {
    this.mapper = mapper;
    this.dbPath = path.toString();
    try {
      init();
    } catch (Exception e) {
      throw new IllegalStateException("Failed to init store at " + path, e);
    }
  }

  @PostConstruct
  public void init() throws Exception {
    if (initialized) {
      return;
    }
    Path path = Path.of(dbPath).toAbsolutePath();
    if (path.getParent() != null) {
      Files.createDirectories(path.getParent());
    }
    jdbcUrl = "jdbc:sqlite:" + path;
    try (Connection conn = connect();
        Statement st = conn.createStatement()) {
      st.execute(
          """
          CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            tenant_id TEXT,
            user_id TEXT,
            idempotency_key TEXT UNIQUE,
            status TEXT,
            payload_json TEXT,
            created_at TEXT
          )
          """);
      st.execute(
          """
          CREATE TABLE IF NOT EXISTS steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            step_index INTEGER,
            event_json TEXT
          )
          """);
      st.execute(
          """
          CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            user_id TEXT,
            merchant_id TEXT,
            event_type TEXT,
            event_time TEXT
          )
          """);
      st.execute(
          """
          CREATE TABLE IF NOT EXISTS not_interested (
            user_id TEXT NOT NULL,
            merchant_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            PRIMARY KEY (user_id, merchant_id)
          )
          """);
      st.execute(
          """
          CREATE TABLE IF NOT EXISTS deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            task_id TEXT,
            delivered_at TEXT NOT NULL,
            merchant_ids_json TEXT NOT NULL,
            categories_json TEXT NOT NULL
          )
          """);
      st.execute(
          """
          CREATE TABLE IF NOT EXISTS scene_seen (
            user_id TEXT NOT NULL,
            scene_key TEXT NOT NULL,
            seen_at TEXT NOT NULL,
            PRIMARY KEY (user_id, scene_key)
          )
          """);
      st.execute(
          """
          CREATE TABLE IF NOT EXISTS exposures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            merchant_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_time TEXT NOT NULL,
            task_id TEXT
          )
          """);
      st.execute(
          "CREATE INDEX IF NOT EXISTS idx_deliveries_user_time ON deliveries(user_id, delivered_at)");
      st.execute(
          "CREATE INDEX IF NOT EXISTS idx_exposures_user_time ON exposures(user_id, event_time)");
    }
    initialized = true;
  }

  private Connection connect() throws Exception {
    return DriverManager.getConnection(jdbcUrl);
  }

  public String newTaskId() {
    return "task_" + UUID.randomUUID().toString().replace("-", "").substring(0, 10);
  }

  public Map<String, Object> getByIdempotency(String key) throws Exception {
    try (Connection conn = connect();
        PreparedStatement ps =
            conn.prepareStatement("SELECT payload_json FROM tasks WHERE idempotency_key = ?")) {
      ps.setString(1, key);
      try (ResultSet rs = ps.executeQuery()) {
        if (!rs.next()) {
          return null;
        }
        return mapper.readValue(rs.getString(1), new TypeReference<>() {});
      }
    }
  }

  public void saveTask(
      String taskId, String tenantId, String userId, String key, String status, Object payload)
      throws Exception {
    String json = mapper.writeValueAsString(payload);
    try (Connection conn = connect();
        PreparedStatement ps =
            conn.prepareStatement(
                """
                INSERT INTO tasks(task_id, tenant_id, user_id, idempotency_key, status, payload_json, created_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET status=excluded.status, payload_json=excluded.payload_json
                """)) {
      ps.setString(1, taskId);
      ps.setString(2, tenantId);
      ps.setString(3, userId);
      ps.setString(4, key);
      ps.setString(5, status);
      ps.setString(6, json);
      ps.setString(7, Utc.nowIso());
      ps.executeUpdate();
    }
  }

  public Map<String, Object> getTask(String taskId) throws Exception {
    try (Connection conn = connect();
        PreparedStatement ps =
            conn.prepareStatement("SELECT payload_json FROM tasks WHERE task_id = ?")) {
      ps.setString(1, taskId);
      try (ResultSet rs = ps.executeQuery()) {
        if (!rs.next()) {
          return null;
        }
        return mapper.readValue(rs.getString(1), new TypeReference<>() {});
      }
    }
  }

  public void saveSteps(String taskId, List<Map<String, Object>> steps) throws Exception {
    try (Connection conn = connect()) {
      try (PreparedStatement del = conn.prepareStatement("DELETE FROM steps WHERE task_id = ?")) {
        del.setString(1, taskId);
        del.executeUpdate();
      }
      try (PreparedStatement ins =
          conn.prepareStatement(
              "INSERT INTO steps(task_id, step_index, event_json) VALUES(?,?,?)")) {
        for (Map<String, Object> s : steps) {
          Object idx = s.getOrDefault("step_index", 0);
          int stepIndex = idx instanceof Number n ? n.intValue() : 0;
          ins.setString(1, taskId);
          ins.setInt(2, stepIndex);
          ins.setString(3, mapper.writeValueAsString(s));
          ins.addBatch();
        }
        ins.executeBatch();
      }
    }
  }

  public List<Map<String, Object>> getSteps(String taskId) throws Exception {
    List<Map<String, Object>> out = new ArrayList<>();
    try (Connection conn = connect();
        PreparedStatement ps =
            conn.prepareStatement(
                "SELECT event_json FROM steps WHERE task_id = ? ORDER BY step_index")) {
      ps.setString(1, taskId);
      try (ResultSet rs = ps.executeQuery()) {
        while (rs.next()) {
          out.add(mapper.readValue(rs.getString(1), new TypeReference<>() {}));
        }
      }
    }
    return out;
  }

  public void addFeedback(
      String taskId, String userId, String merchantId, String eventType, String eventTime)
      throws Exception {
    try (Connection conn = connect();
        PreparedStatement ps =
            conn.prepareStatement(
                "INSERT INTO feedback(task_id, user_id, merchant_id, event_type, event_time) VALUES(?,?,?,?,?)")) {
      ps.setString(1, taskId);
      ps.setString(2, userId);
      ps.setString(3, merchantId);
      ps.setString(4, eventType);
      ps.setString(5, eventTime);
      ps.executeUpdate();
    }
  }

  public void upsertNotInterested(
      String userId, String merchantId, String expiresAt, String createdAt) throws Exception {
    Instant createdInstant = Utc.parse(createdAt);
    String created = createdInstant != null ? Utc.toIso(createdInstant) : Utc.nowIso();
    Instant expInstant = Utc.parse(expiresAt);
    String exp = expInstant != null ? Utc.toIso(expInstant) : Utc.nowIso();
    try (Connection conn = connect();
        PreparedStatement ps =
            conn.prepareStatement(
                """
                INSERT INTO not_interested(user_id, merchant_id, created_at, expires_at)
                VALUES(?,?,?,?)
                ON CONFLICT(user_id, merchant_id) DO UPDATE SET
                  expires_at=excluded.expires_at,
                  created_at=excluded.created_at
                """)) {
      ps.setString(1, userId);
      ps.setString(2, merchantId);
      ps.setString(3, created);
      ps.setString(4, exp);
      ps.executeUpdate();
    }
  }

  public List<String> listNotInterested(String userId, Instant now) throws Exception {
    Instant clock = now != null ? now : Utc.now();
    String clockIso = Utc.toIso(clock);
    List<String> out = new ArrayList<>();
    try (Connection conn = connect();
        PreparedStatement ps =
            conn.prepareStatement(
                """
                SELECT merchant_id FROM not_interested
                WHERE user_id = ? AND expires_at > ?
                """)) {
      ps.setString(1, userId);
      ps.setString(2, clockIso);
      try (ResultSet rs = ps.executeQuery()) {
        while (rs.next()) {
          out.add(rs.getString(1));
        }
      }
    }
    return out;
  }

  public void recordDelivery(
      String userId,
      String taskId,
      List<String> merchantIds,
      List<String> categories,
      String deliveredAt)
      throws Exception {
    String at = deliveredAt != null && !deliveredAt.isBlank() ? Utc.toIso(Utc.parse(deliveredAt)) : Utc.nowIso();
    if (deliveredAt != null && Utc.parse(deliveredAt) == null) {
      at = Utc.nowIso();
    } else if (deliveredAt != null) {
      at = Utc.toIso(Utc.parse(deliveredAt));
    }
    try (Connection conn = connect()) {
      try (PreparedStatement ps =
          conn.prepareStatement(
              """
              INSERT INTO deliveries(user_id, task_id, delivered_at, merchant_ids_json, categories_json)
              VALUES(?,?,?,?,?)
              """)) {
        ps.setString(1, userId);
        ps.setString(2, taskId);
        ps.setString(3, at);
        ps.setString(4, mapper.writeValueAsString(merchantIds));
        ps.setString(5, mapper.writeValueAsString(categories));
        ps.executeUpdate();
      }
      try (PreparedStatement ps =
          conn.prepareStatement(
              """
              INSERT INTO exposures(user_id, merchant_id, event_type, event_time, task_id)
              VALUES(?,?,?,?,?)
              """)) {
        for (String mid : merchantIds) {
          ps.setString(1, userId);
          ps.setString(2, mid);
          ps.setString(3, "impression");
          ps.setString(4, at);
          ps.setString(5, taskId);
          ps.addBatch();
        }
        ps.executeBatch();
      }
    }
  }

  public int countDeliveriesSince(String userId, Instant since) throws Exception {
    String sinceIso = Utc.toIso(since);
    try (Connection conn = connect();
        PreparedStatement ps =
            conn.prepareStatement(
                """
                SELECT COUNT(*) AS c FROM deliveries
                WHERE user_id = ? AND delivered_at >= ?
                """)) {
      ps.setString(1, userId);
      ps.setString(2, sinceIso);
      try (ResultSet rs = ps.executeQuery()) {
        if (!rs.next()) {
          return 0;
        }
        return rs.getInt(1);
      }
    }
  }

  public DeliverySummary getDeliverySummary(String userId) throws Exception {
    try (Connection conn = connect();
        PreparedStatement ps =
            conn.prepareStatement(
                """
                SELECT delivered_at, merchant_ids_json, categories_json FROM deliveries
                WHERE user_id = ?
                ORDER BY delivered_at DESC LIMIT 1
                """)) {
      ps.setString(1, userId);
      try (ResultSet rs = ps.executeQuery()) {
        if (!rs.next()) {
          return new DeliverySummary(null, List.of(), List.of());
        }
        List<String> mids = mapper.readValue(rs.getString(2), new TypeReference<>() {});
        List<String> cats = mapper.readValue(rs.getString(3), new TypeReference<>() {});
        return new DeliverySummary(rs.getString(1), mids, cats);
      }
    }
  }

  public void recordSceneSeen(String userId, String sceneKey, String seenAt) throws Exception {
    String at =
        seenAt != null && Utc.parse(seenAt) != null ? Utc.toIso(Utc.parse(seenAt)) : Utc.nowIso();
    try (Connection conn = connect();
        PreparedStatement ps =
            conn.prepareStatement(
                """
                INSERT INTO scene_seen(user_id, scene_key, seen_at)
                VALUES(?,?,?)
                ON CONFLICT(user_id, scene_key) DO UPDATE SET seen_at=excluded.seen_at
                """)) {
      ps.setString(1, userId);
      ps.setString(2, sceneKey);
      ps.setString(3, at);
      ps.executeUpdate();
    }
  }

  public boolean sceneSeenWithin(String userId, String sceneKey, Duration window, Instant now)
      throws Exception {
    Instant clock = now != null ? now : Utc.now();
    try (Connection conn = connect();
        PreparedStatement ps =
            conn.prepareStatement(
                "SELECT seen_at FROM scene_seen WHERE user_id = ? AND scene_key = ?")) {
      ps.setString(1, userId);
      ps.setString(2, sceneKey);
      try (ResultSet rs = ps.executeQuery()) {
        if (!rs.next()) {
          return false;
        }
        Instant seen = Utc.parse(rs.getString(1));
        if (seen == null) {
          return false;
        }
        return Duration.between(seen, clock).compareTo(window) < 0;
      }
    }
  }

  public void recordExposure(
      String userId, String merchantId, String eventType, String eventTime, String taskId)
      throws Exception {
    String at =
        eventTime != null && Utc.parse(eventTime) != null
            ? Utc.toIso(Utc.parse(eventTime))
            : Utc.nowIso();
    try (Connection conn = connect();
        PreparedStatement ps =
            conn.prepareStatement(
                """
                INSERT INTO exposures(user_id, merchant_id, event_type, event_time, task_id)
                VALUES(?,?,?,?,?)
                """)) {
      ps.setString(1, userId);
      ps.setString(2, merchantId);
      ps.setString(3, eventType);
      ps.setString(4, at);
      ps.setString(5, taskId);
      ps.executeUpdate();
    }
  }

  public List<ExposureItem> listExposureMerchants(
      String userId, Instant since, int limit, List<String> eventTypes) throws Exception {
    String sinceIso = Utc.toIso(since);
    List<String> types =
        eventTypes == null || eventTypes.isEmpty()
            ? List.of("impression", "click", "not_interested")
            : eventTypes;
    String placeholders = String.join(",", types.stream().map(t -> "?").toList());
    String sql =
        """
        SELECT merchant_id, event_type, event_time FROM exposures
        WHERE user_id = ? AND event_time >= ? AND event_type IN (%s)
          AND merchant_id IS NOT NULL AND merchant_id != ''
        ORDER BY event_time DESC
        LIMIT ?
        """
            .formatted(placeholders);
    List<ExposureItem> out = new ArrayList<>();
    try (Connection conn = connect();
        PreparedStatement ps = conn.prepareStatement(sql)) {
      int i = 1;
      ps.setString(i++, userId);
      ps.setString(i++, sinceIso);
      for (String t : types) {
        ps.setString(i++, t);
      }
      ps.setInt(i, limit);
      try (ResultSet rs = ps.executeQuery()) {
        while (rs.next()) {
          out.add(new ExposureItem(rs.getString(1), rs.getString(2), rs.getString(3)));
        }
      }
    }
    return out;
  }

  public record ExposureItem(String merchantId, String eventType, String eventTime) {}

  public record DeliverySummary(
      String lastSuccessAt, List<String> merchantIds, List<String> categories) {}
}
