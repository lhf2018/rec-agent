package com.recagent.time;

import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;

/** All storage and window comparisons use UTC (ISO-8601). */
public final class Utc {
  private static final DateTimeFormatter FMT = DateTimeFormatter.ISO_OFFSET_DATE_TIME;

  private Utc() {}

  public static Instant now() {
    return Instant.now();
  }

  public static String nowIso() {
    return OffsetDateTime.now(ZoneOffset.UTC).format(FMT).replace("+00:00", "Z");
  }

  public static Instant parse(String value) {
    if (value == null || value.isBlank()) {
      return null;
    }
    String text = value.trim();
    if (text.endsWith("Z")) {
      text = text.substring(0, text.length() - 1) + "+00:00";
    }
    try {
      return OffsetDateTime.parse(text, FMT).toInstant();
    } catch (DateTimeParseException ex) {
      try {
        return Instant.parse(value);
      } catch (DateTimeParseException ex2) {
        return null;
      }
    }
  }

  public static String toIso(Instant instant) {
    if (instant == null) {
      return nowIso();
    }
    return OffsetDateTime.ofInstant(instant, ZoneOffset.UTC).format(FMT).replace("+00:00", "Z");
  }

  /** Wall-clock hour in the timestamp's own offset (for DND). */
  public static int wallClockHour(String occurredAt) {
    Instant instant = parse(occurredAt);
    if (instant == null) {
      return 12;
    }
    String text = occurredAt.trim();
    if (text.endsWith("Z")) {
      text = text.substring(0, text.length() - 1) + "+00:00";
    }
    try {
      return OffsetDateTime.parse(text, FMT).getHour();
    } catch (DateTimeParseException ex) {
      return OffsetDateTime.ofInstant(instant, ZoneOffset.UTC).getHour();
    }
  }
}
