package com.recagent.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class Domain {
  private Domain() {}

  public record Location(double lng, double lat, String district) {
    public Location {
      if (district == null) {
        district = "";
      }
    }
  }

  public record Scene(
      String sceneId,
      Enums.TriggerType trigger,
      String channel,
      String occurredAt,
      Location location,
      int radiusM,
      String mealPeriod) {
    public Scene {
      if (channel == null || channel.isBlank()) {
        channel = "nearby";
      }
      if (mealPeriod == null || mealPeriod.isBlank()) {
        mealPeriod = "lunch";
      }
      if (radiusM <= 0) {
        radiusM = 3000;
      }
    }
  }

  @JsonIgnoreProperties(ignoreUnknown = true)
  public static class Product {
    public String productId;
    public String merchantId;
    public String name;
    public double price;
    public boolean inStock = true;
    public List<String> dietaryTags = new ArrayList<>();
    public String category = "";
    public String description = "";
  }

  @JsonIgnoreProperties(ignoreUnknown = true)
  public static class Merchant {
    public String merchantId;
    public String name;
    public String category;
    public List<String> tags = new ArrayList<>();
    public double lng;
    public double lat;
    public boolean isOpen = true;
    public int deliveryRadiusM = 5000;
    public double avgPrice = 30.0;
    public String status = "online";
    public double score = 4.5;
    public List<Product> products = new ArrayList<>();
  }

  @JsonIgnoreProperties(ignoreUnknown = true)
  public static class HardConstraints {
    public List<String> dietaryForbid = new ArrayList<>();
    public List<String> blockedMerchants = new ArrayList<>();
    public List<String> notInterestedMerchants = new ArrayList<>();
    public Double budgetMin;
    public Double budgetMax;

    public HardConstraints copy() {
      HardConstraints c = new HardConstraints();
      c.dietaryForbid = new ArrayList<>(dietaryForbid);
      c.blockedMerchants = new ArrayList<>(blockedMerchants);
      c.notInterestedMerchants = new ArrayList<>(notInterestedMerchants);
      c.budgetMin = budgetMin;
      c.budgetMax = budgetMax;
      return c;
    }
  }

  @JsonIgnoreProperties(ignoreUnknown = true)
  public static class ShortTermIntent {
    public String topic;
    public double confidence;
    public String updatedAt;
    public String expiresAt;
    public String source = "browse_burst";

    public ShortTermIntent copy() {
      ShortTermIntent s = new ShortTermIntent();
      s.topic = topic;
      s.confidence = confidence;
      s.updatedAt = updatedAt;
      s.expiresAt = expiresAt;
      s.source = source;
      return s;
    }
  }

  @JsonIgnoreProperties(ignoreUnknown = true)
  public static class RuntimeSignals {
    public int deliveryCountInWindow;
    public String lastSuccessAt = "";
    public List<String> recentShownMerchants = new ArrayList<>();
    public List<String> recentShownCategories = new ArrayList<>();
    public boolean sceneSeenRecently;
    public List<String> exposureMerchantIds = new ArrayList<>();
    public List<String> dynamicNotInterested = new ArrayList<>();
  }

  @JsonIgnoreProperties(ignoreUnknown = true)
  public static class UserSnapshot {
    public String userId;
    public String tenantId = "t_demo";
    public String snapshotVersion = "v1";
    public String builtAt = "";
    public String city = "";
    public String memberLevel = "normal";
    public String lifecycle = "active";
    public String homeDistrict = "";
    public List<String> longTermCategories = new ArrayList<>();
    public double avgOrderMin;
    public double avgOrderMax = 999;
    public List<String> frequentMerchants = new ArrayList<>();
    public List<ShortTermIntent> shortTerm = new ArrayList<>();
    public HardConstraints hard = new HardConstraints();
    public List<String> preferredTags = new ArrayList<>();
    public boolean allowPersonalized = true;
    public List<String> categoryOptOut = new ArrayList<>();
    public List<String> recentConversions = new ArrayList<>();
    public RuntimeSignals runtime = new RuntimeSignals();

    public UserSnapshot deepCopy() {
      UserSnapshot u = new UserSnapshot();
      u.userId = userId;
      u.tenantId = tenantId;
      u.snapshotVersion = snapshotVersion;
      u.builtAt = builtAt;
      u.city = city;
      u.memberLevel = memberLevel;
      u.lifecycle = lifecycle;
      u.homeDistrict = homeDistrict;
      u.longTermCategories = new ArrayList<>(longTermCategories);
      u.avgOrderMin = avgOrderMin;
      u.avgOrderMax = avgOrderMax;
      u.frequentMerchants = new ArrayList<>(frequentMerchants);
      u.shortTerm = new ArrayList<>();
      for (ShortTermIntent s : shortTerm) {
        u.shortTerm.add(s.copy());
      }
      u.hard = hard == null ? new HardConstraints() : hard.copy();
      u.preferredTags = new ArrayList<>(preferredTags);
      u.allowPersonalized = allowPersonalized;
      u.categoryOptOut = new ArrayList<>(categoryOptOut);
      u.recentConversions = new ArrayList<>(recentConversions);
      u.runtime = new RuntimeSignals();
      return u;
    }
  }

  public static class CandidateProduct {
    public String productId;
    public String name;
    public double price;
    public boolean inStock;
    public List<String> dietaryTags = new ArrayList<>();
    public double score;
  }

  public static class CandidateMerchant {
    public String merchantId;
    public String name;
    public String category;
    public double avgPrice;
    public double distanceM;
    public boolean isOpen;
    public double score;
    public List<String> tags = new ArrayList<>();
    public List<CandidateProduct> products = new ArrayList<>();
    public List<String> recallSources = new ArrayList<>();
    public String evidenceId = "";
    public String provenance = "primary";
    public String observedAt = "";
    public String expiresAt = "";
  }

  public record DroppedItem(String merchantId, String reason) {}

  public static class EngineOutput {
    public List<CandidateMerchant> candidates = new ArrayList<>();
    public List<CandidateMerchant> baseline = new ArrayList<>();
    public List<DroppedItem> dropped = new ArrayList<>();
    public String candidateSetVersion = "";
    public boolean factsComplete = true;
    public boolean intentConflict;
    public boolean needFreshness;
    public boolean needCompare;
    public boolean softRerank;
    public boolean supplementNeeded;
    public Map<String, Integer> filterStats = new LinkedHashMap<>();
  }

  public static class RecommendedProduct {
    public String productId;
    public String name = "";
    public String reason;
    public List<String> evidenceIds = new ArrayList<>();

    public RecommendedProduct() {}

    public RecommendedProduct(
        String productId, String name, String reason, List<String> evidenceIds) {
      this.productId = productId;
      this.name = name == null ? "" : name;
      this.reason = reason;
      this.evidenceIds = evidenceIds == null ? new ArrayList<>() : new ArrayList<>(evidenceIds);
    }
  }

  public static class RecommendedMerchant {
    public String merchantId;
    public String name = "";
    public String reason;
    public List<String> evidenceIds = new ArrayList<>();
    public List<RecommendedProduct> products = new ArrayList<>();

    public RecommendedMerchant() {}

    public RecommendedMerchant(
        String merchantId,
        String name,
        String reason,
        List<String> evidenceIds,
        List<RecommendedProduct> products) {
      this.merchantId = merchantId;
      this.name = name == null ? "" : name;
      this.reason = reason;
      this.evidenceIds = evidenceIds == null ? new ArrayList<>() : new ArrayList<>(evidenceIds);
      this.products = products == null ? new ArrayList<>() : new ArrayList<>(products);
    }
  }

  public static class RecommendationResult {
    public String taskId;
    public Enums.TaskStatus status;
    public Enums.RouteMode routeMode;
    public Enums.OpportunityResult opportunityResult;
    public String decisionReasonCode;
    public String fallbackReason;
    public String source;
    public List<RecommendedMerchant> merchants = new ArrayList<>();
    public Double selfScore;
    public Map<String, Object> metrics = new LinkedHashMap<>();
  }

  public record CreateTaskRequest(String tenantId, String userId, Scene scene) {
    public CreateTaskRequest {
      if (tenantId == null || tenantId.isBlank()) {
        tenantId = "t_demo";
      }
    }
  }

  public record FeedbackRequest(String eventType, String merchantId, String eventTime) {}
}
