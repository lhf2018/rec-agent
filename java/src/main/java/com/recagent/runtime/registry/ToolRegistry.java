package com.recagent.runtime.registry;

import java.util.List;
import java.util.Map;
import java.util.Set;

public class ToolRegistry {
  public static final Set<String> TOOL_WHITELIST =
      Set.of(
          "get_merchant_detail",
          "get_product_detail",
          "compare_candidates",
          "get_exposure_history",
          "supplement_recall",
          "submit_recommendation");

  private static final Set<String> COMPARE_ASPECTS =
      Set.of("price", "distance", "category", "open_now", "score");
  private static final Set<String> SUPPLEMENT_STRATEGIES =
      Set.of("widen_radius", "adjacent_category", "trending");

  public boolean isAllowed(String name) {
    return TOOL_WHITELIST.contains(name);
  }

  public String validateArgs(String name, Map<String, Object> args) {
    if (!TOOL_WHITELIST.contains(name)) {
      return "unknown_tool";
    }
    if ("get_merchant_detail".equals(name) && missing(args, "merchant_id")) {
      return "missing_argument:merchant_id";
    }
    if ("get_product_detail".equals(name) && missing(args, "product_id")) {
      return "missing_argument:product_id";
    }
    if ("compare_candidates".equals(name)) {
      if (missing(args, "left_id")) {
        return "missing_argument:left_id";
      }
      if (missing(args, "right_id")) {
        return "missing_argument:right_id";
      }
      Object aspects = args.get("aspects");
      if (aspects != null) {
        if (!(aspects instanceof List<?> list)
            || list.stream().anyMatch(a -> !COMPARE_ASPECTS.contains(String.valueOf(a)))) {
          return "invalid_argument:aspects";
        }
      }
    }
    if ("supplement_recall".equals(name)) {
      Object strategy = args.get("strategy");
      String s = strategy == null ? "widen_radius" : String.valueOf(strategy);
      if (!SUPPLEMENT_STRATEGIES.contains(s)) {
        return "invalid_argument:strategy";
      }
    }
    if ("submit_recommendation".equals(name) && !args.containsKey("merchants")) {
      return "missing_argument:merchants";
    }
    return null;
  }

  private static boolean missing(Map<String, Object> args, String key) {
    if (!args.containsKey(key)) {
      return true;
    }
    Object v = args.get(key);
    return v == null || "".equals(v);
  }
}
