package com.recagent.runtime.guard;

import java.util.LinkedHashMap;
import java.util.Map;

public class Evidence {
  public String evidenceId;
  public Map<String, Object> facts = new LinkedHashMap<>();
  public String source = "";
  public String expiresAt = "";
  public String subjectType = "merchant";
  public String subjectId = "";
  public String candidateSetVersion = "";

  public Evidence() {}

  public Evidence(
      String evidenceId,
      Map<String, Object> facts,
      String source,
      String expiresAt,
      String subjectType,
      String subjectId,
      String candidateSetVersion) {
    this.evidenceId = evidenceId;
    this.facts = facts != null ? facts : new LinkedHashMap<>();
    this.source = source != null ? source : "";
    this.expiresAt = expiresAt != null ? expiresAt : "";
    this.subjectType = subjectType != null ? subjectType : "merchant";
    this.subjectId = subjectId != null ? subjectId : "";
    this.candidateSetVersion = candidateSetVersion != null ? candidateSetVersion : "";
  }
}
