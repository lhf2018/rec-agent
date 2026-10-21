package com.recagent.model;

public final class Enums {
  private Enums() {}

  public enum TriggerType {
    impression,
    refresh,
    location_change,
    time_slot
  }

  public enum TaskStatus {
    pending,
    running,
    succeeded,
    degraded,
    suppressed,
    skipped,
    empty,
    failed
  }

  public enum RouteMode {
    SKIP,
    FALLBACK,
    RULE_ENGINE,
    DIRECT,
    REACT
  }

  public enum OpportunityResult {
    recommend_now,
    suppress,
    skip
  }
}
