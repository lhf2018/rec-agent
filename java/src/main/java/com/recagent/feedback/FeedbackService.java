package com.recagent.feedback;

import com.recagent.catalog.Catalog;
import com.recagent.config.AppSettings;
import com.recagent.model.Domain.FeedbackRequest;
import com.recagent.store.Store;
import com.recagent.time.Utc;
import java.time.Duration;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import org.springframework.stereotype.Component;

@Component
public class FeedbackService {
  private final Store store;
  private final AppSettings settings;

  public FeedbackService(Catalog catalog, Store store, AppSettings settings) {
    this.store = store;
    this.settings = settings;
  }

  @SuppressWarnings("unchecked")
  public Map<String, Object> apply(String taskId, FeedbackRequest body) throws Exception {
    Map<String, Object> task = store.getTask(taskId);
    if (task == null) {
      return Map.of("ok", false, "error", "task_not_found");
    }
    Object reqObj = task.get("request");
    String userId = null;
    if (reqObj instanceof Map<?, ?> req) {
      Object uid = req.get("user_id");
      if (uid == null) {
        uid = req.get("userId");
      }
      userId = uid == null ? null : String.valueOf(uid);
    }
    if (userId == null || userId.isBlank()) {
      return Map.of("ok", false, "error", "user_missing");
    }

    String eventTime =
        body.eventTime() != null && !body.eventTime().isBlank()
            ? Utc.toIso(Utc.parse(body.eventTime()) != null ? Utc.parse(body.eventTime()) : Utc.now())
            : Utc.nowIso();
    if (body.eventTime() != null && Utc.parse(body.eventTime()) != null) {
      eventTime = Utc.toIso(Utc.parse(body.eventTime()));
    } else {
      eventTime = Utc.nowIso();
    }

    if ("not_interested".equals(body.eventType())) {
      if (body.merchantId() == null || body.merchantId().isBlank()) {
        return Map.of("ok", false, "error", "merchant_id_required");
      }
      Set<String> returned = Set.of();
      Object merchantsObj = task.get("merchants");
      if (merchantsObj instanceof List<?> list) {
        returned =
            list.stream()
                .filter(m -> m instanceof Map<?, ?>)
                .map(
                    m -> {
                      Map<?, ?> mm = (Map<?, ?>) m;
                      Object mid = mm.get("merchant_id");
                      if (mid == null) {
                        mid = mm.get("merchantId");
                      }
                      return mid == null ? null : String.valueOf(mid);
                    })
                .filter(id -> id != null && !id.isBlank())
                .collect(Collectors.toSet());
      }
      if (!returned.contains(body.merchantId())) {
        return Map.of("ok", false, "error", "merchant_not_in_task");
      }
      String expires =
          Utc.toIso(Utc.now().plus(Duration.ofDays(settings.getNotInterestedTtlDays())));
      store.upsertNotInterested(userId, body.merchantId(), expires, eventTime);
      store.recordExposure(userId, body.merchantId(), "not_interested", eventTime, taskId);
    } else if ("impression".equals(body.eventType()) || "click".equals(body.eventType())) {
      if (body.merchantId() == null || body.merchantId().isBlank()) {
        return Map.of("ok", false, "error", "merchant_id_required");
      }
      store.recordExposure(userId, body.merchantId(), body.eventType(), eventTime, taskId);
    } else {
      return Map.of("ok", false, "error", "unsupported_event");
    }

    store.addFeedback(taskId, userId, body.merchantId(), body.eventType(), eventTime);
    Map<String, Object> ok = new HashMap<>();
    ok.put("ok", true);
    return ok;
  }
}
