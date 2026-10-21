package com.recagent.catalog;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.recagent.model.Domain.HardConstraints;
import com.recagent.model.Domain.Merchant;
import com.recagent.model.Domain.Product;
import com.recagent.model.Domain.ShortTermIntent;
import com.recagent.model.Domain.UserSnapshot;
import jakarta.annotation.PostConstruct;
import java.io.InputStream;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.stereotype.Component;

@Component
public class Catalog {
  private final ObjectMapper mapper;
  private final Map<String, UserSnapshot> users = new LinkedHashMap<>();
  private final Map<String, Merchant> merchants = new LinkedHashMap<>();
  private String tenantId = "t_demo";
  private boolean loaded;

  public Catalog(ObjectMapper mapper) {
    this.mapper = mapper;
  }

  /** Load fixtures immediately (for unit tests without Spring lifecycle). */
  public Catalog loadNow() {
    try {
      load();
    } catch (Exception e) {
      throw new IllegalStateException("Failed to load catalog fixtures", e);
    }
    return this;
  }

  @PostConstruct
  public void load() throws Exception {
    if (loaded) {
      return;
    }
    try (InputStream usersIn = getClass().getResourceAsStream("/fixtures/users.json")) {
      if (usersIn == null) {
        throw new IllegalStateException("fixtures/users.json missing");
      }
      JsonNode root = mapper.readTree(usersIn);
      for (JsonNode raw : root.path("users")) {
        UserSnapshot u = mapUser(raw);
        users.put(u.userId, u);
      }
    }
    try (InputStream catalogIn = getClass().getResourceAsStream("/fixtures/catalog.json")) {
      if (catalogIn == null) {
        throw new IllegalStateException("fixtures/catalog.json missing");
      }
      JsonNode root = mapper.readTree(catalogIn);
      if (root.hasNonNull("tenant_id")) {
        tenantId = root.get("tenant_id").asText();
      }
      for (JsonNode raw : root.path("merchants")) {
        Merchant m = mapMerchant(raw);
        merchants.put(m.merchantId, m);
      }
    }
    loaded = true;
  }

  private Merchant mapMerchant(JsonNode raw) {
    Merchant m = new Merchant();
    m.merchantId = text(raw, "merchant_id");
    m.name = text(raw, "name", "");
    m.category = text(raw, "category", "");
    m.tags = stringList(raw.path("tags"));
    m.lng = raw.path("lng").asDouble();
    m.lat = raw.path("lat").asDouble();
    m.isOpen = raw.path("is_open").asBoolean(true);
    m.deliveryRadiusM = raw.path("delivery_radius_m").asInt(5000);
    m.avgPrice = raw.path("avg_price").asDouble(30.0);
    m.status = text(raw, "status", "online");
    m.score = raw.path("score").asDouble(4.5);
    List<Product> products = new ArrayList<>();
    for (JsonNode p : raw.path("products")) {
      Product prod = new Product();
      prod.productId = text(p, "product_id");
      prod.merchantId = text(p, "merchant_id", m.merchantId);
      prod.name = text(p, "name", "");
      prod.price = p.path("price").asDouble();
      prod.inStock = p.path("in_stock").asBoolean(true);
      prod.dietaryTags = stringList(p.path("dietary_tags"));
      prod.category = text(p, "category", m.category);
      prod.description = text(p, "description", "");
      products.add(prod);
    }
    m.products = products;
    return m;
  }

  private UserSnapshot mapUser(JsonNode raw) {
    UserSnapshot u = new UserSnapshot();
    u.userId = text(raw, "user_id");
    u.tenantId = text(raw, "tenant_id", "t_demo");
    u.snapshotVersion = text(raw, "snapshot_version", "v1");
    u.builtAt = text(raw, "built_at", "");
    u.city = text(raw, "city", "");
    u.memberLevel = text(raw, "member_level", "normal");
    u.lifecycle = text(raw, "lifecycle", "active");
    u.homeDistrict = text(raw, "home_district", "");
    u.longTermCategories = stringList(raw.path("long_term_categories"));
    u.avgOrderMin = raw.path("avg_order_min").asDouble(0);
    u.avgOrderMax = raw.path("avg_order_max").asDouble(999);
    u.frequentMerchants = stringList(raw.path("frequent_merchants"));
    u.preferredTags = stringList(raw.path("preferred_tags"));
    u.allowPersonalized = raw.path("allow_personalized").asBoolean(true);
    u.categoryOptOut = stringList(raw.path("category_opt_out"));
    u.recentConversions = stringList(raw.path("recent_conversions"));
    JsonNode hard = raw.path("hard");
    HardConstraints hc = new HardConstraints();
    hc.dietaryForbid = stringList(hard.path("dietary_forbid"));
    hc.blockedMerchants = stringList(hard.path("blocked_merchants"));
    hc.notInterestedMerchants = stringList(hard.path("not_interested_merchants"));
    if (!hard.path("budget_min").isNull() && !hard.path("budget_min").isMissingNode()) {
      hc.budgetMin = hard.path("budget_min").asDouble();
    }
    if (!hard.path("budget_max").isNull() && !hard.path("budget_max").isMissingNode()) {
      hc.budgetMax = hard.path("budget_max").asDouble();
    }
    u.hard = hc;
    List<ShortTermIntent> shorts = new ArrayList<>();
    for (JsonNode s : raw.path("short_term")) {
      ShortTermIntent intent = new ShortTermIntent();
      intent.topic = text(s, "topic");
      intent.confidence = s.path("confidence").asDouble();
      intent.updatedAt = text(s, "updated_at", "");
      intent.expiresAt = text(s, "expires_at", "");
      intent.source = text(s, "source", "browse_burst");
      shorts.add(intent);
    }
    u.shortTerm = shorts;
    return u;
  }

  private static String text(JsonNode n, String field) {
    return text(n, field, null);
  }

  private static String text(JsonNode n, String field, String def) {
    JsonNode v = n.get(field);
    if (v == null || v.isNull()) {
      return def;
    }
    return v.asText();
  }

  private static List<String> stringList(JsonNode arr) {
    List<String> out = new ArrayList<>();
    if (arr == null || !arr.isArray()) {
      return out;
    }
    for (JsonNode n : arr) {
      out.add(n.asText());
    }
    return out;
  }

  public UserSnapshot getUser(String userId) {
    return users.get(userId);
  }

  public Merchant getMerchant(String merchantId) {
    return merchants.get(merchantId);
  }

  public List<Merchant> allMerchants() {
    return new ArrayList<>(merchants.values());
  }

  public String getTenantId() {
    return tenantId;
  }
}
