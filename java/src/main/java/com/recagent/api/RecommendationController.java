package com.recagent.api;

import com.fasterxml.jackson.databind.JsonNode;
import com.recagent.model.Domain.CreateTaskRequest;
import com.recagent.model.Domain.FeedbackRequest;
import com.recagent.model.Domain.Location;
import com.recagent.model.Domain.RecommendationResult;
import com.recagent.model.Domain.Scene;
import com.recagent.model.Enums.TriggerType;
import com.recagent.service.RecommendationService;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
public class RecommendationController {
  private final RecommendationService service;

  public RecommendationController(RecommendationService service) {
    this.service = service;
  }

  @GetMapping("/health")
  public Map<String, Object> health() {
    return Map.of("ok", true);
  }

  @PostMapping("/v1/recommendation-tasks")
  public RecommendationResult createTask(@RequestBody JsonNode body) {
    try {
      String tenantId = text(body, "tenant_id", "t_demo");
      String userId = text(body, "user_id", null);
      if (userId == null || userId.isBlank()) {
        throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "user_id required");
      }
      JsonNode sceneNode = body.path("scene");
      JsonNode loc = sceneNode.path("location");
      Scene scene =
          new Scene(
              text(sceneNode, "scene_id", "unknown"),
              TriggerType.valueOf(text(sceneNode, "trigger", "impression")),
              text(sceneNode, "channel", "nearby"),
              text(sceneNode, "occurred_at", null),
              new Location(
                  loc.path("lng").asDouble(),
                  loc.path("lat").asDouble(),
                  text(loc, "district", "")),
              sceneNode.path("radius_m").asInt(3000),
              text(sceneNode, "meal_period", "lunch"));
      return service.createTask(new CreateTaskRequest(tenantId, userId, scene));
    } catch (ResponseStatusException ex) {
      throw ex;
    } catch (Exception ex) {
      throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, ex.getMessage(), ex);
    }
  }

  @GetMapping("/v1/recommendation-tasks/{taskId}")
  public Map<String, Object> getTask(@PathVariable String taskId) {
    try {
      Map<String, Object> task = service.getTask(taskId);
      if (task == null) {
        throw new ResponseStatusException(HttpStatus.NOT_FOUND, "task not found");
      }
      return task;
    } catch (ResponseStatusException ex) {
      throw ex;
    } catch (Exception ex) {
      throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, ex.getMessage(), ex);
    }
  }

  @GetMapping("/v1/recommendation-tasks/{taskId}/trace")
  public Map<String, Object> getTrace(@PathVariable String taskId) {
    try {
      Map<String, Object> trace = service.getTrace(taskId);
      if (trace.get("task") == null) {
        throw new ResponseStatusException(HttpStatus.NOT_FOUND, "task not found");
      }
      return trace;
    } catch (ResponseStatusException ex) {
      throw ex;
    } catch (Exception ex) {
      throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, ex.getMessage(), ex);
    }
  }

  @PostMapping("/v1/recommendations/{taskId}/feedback")
  public Map<String, Object> feedback(@PathVariable String taskId, @RequestBody JsonNode body) {
    try {
      FeedbackRequest req =
          new FeedbackRequest(
              text(body, "event_type", null),
              text(body, "merchant_id", null),
              text(body, "event_time", null));
      Map<String, Object> result = service.feedback(taskId, req);
      if (!Boolean.TRUE.equals(result.get("ok"))) {
        throw new ResponseStatusException(
            HttpStatus.BAD_REQUEST, String.valueOf(result.getOrDefault("error", "bad request")));
      }
      return result;
    } catch (ResponseStatusException ex) {
      throw ex;
    } catch (Exception ex) {
      throw new ResponseStatusException(HttpStatus.INTERNAL_SERVER_ERROR, ex.getMessage(), ex);
    }
  }

  private static String text(JsonNode n, String field, String def) {
    JsonNode v = n.get(field);
    if (v == null || v.isNull() || v.asText().isBlank()) {
      return def;
    }
    return v.asText();
  }
}
