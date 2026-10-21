package com.recagent.runtime.budget;

import com.recagent.config.AppSettings;
import com.recagent.time.Utc;
import java.time.Duration;
import java.time.Instant;

/** UTC deadline gate checked before every model / tool call. */
public class DeadlineBudget {
  private final Instant deadlineAt;
  private final AppSettings settings;

  public DeadlineBudget(Instant deadlineAt, AppSettings settings) {
    this.settings = settings;
    Instant dl = deadlineAt;
    if (dl == null) {
      dl = Utc.now();
    }
    this.deadlineAt = dl;
  }

  public double remainingMs(Instant now) {
    Instant clock = now != null ? now : Utc.now();
    return Duration.between(clock, deadlineAt).toMillis();
  }

  public boolean allowModel(Instant now) {
    return remainingMs(now) > (double) settings.getModelMinRemainingMs();
  }

  public boolean allowModel() {
    return allowModel(null);
  }

  public boolean allowTool(Integer toolTimeoutMs, Instant now) {
    double need =
        toolTimeoutMs != null ? toolTimeoutMs.doubleValue() : settings.getToolTimeoutMs();
    return remainingMs(now) > need;
  }

  public boolean allowTool(int toolTimeoutMs) {
    return allowTool(toolTimeoutMs, null);
  }
}
