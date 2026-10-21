package com.recagent.config;

import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

@Component
@ConfigurationProperties(prefix = "rec-agent")
public class AppSettings {
  private boolean useMockLlm = true;
  private String llmApiKey = "";
  private String llmBaseUrl = "";
  private String llmModel = "mock";
  private int taskTimeoutMs = 20_000;
  private int modelMinRemainingMs = 500;
  private boolean enableReact = true;
  private boolean enableLlm = true;
  /** SKIP|FALLBACK|RULE_ENGINE|RULE_RERANK|DIRECT|REACT for eval */
  private String forceRoute = "";

  private int freqCapPerDay = 10;
  private int freqWindowHours = 24;
  private int cooldownMinutes = 0;
  private int similarSuppressMinutes = 120;
  private int sceneDedupeMinutes = 60;
  private int exposureHistoryWindowHours = 72;
  private int notInterestedTtlDays = 30;
  private int dndStartHour = 23;
  private int dndEndHour = 7;

  private int maxMerchants = 3;
  private int maxProductsPerMerchant = 3;
  private int maxSameCategory = 2;
  private int candidateLimit = 30;
  private int recallLimit = 20;
  private int reactMaxSteps = 4;
  private int toolTimeoutMs = 2000;
  private int supplementTimeoutMs = 5000;
  private int supplementMin = 3;

  private String dbPath = "data/rec_agent_java.db";

  public boolean isUseMockLlm() {
    return useMockLlm;
  }

  public void setUseMockLlm(boolean useMockLlm) {
    this.useMockLlm = useMockLlm;
  }

  public String getLlmApiKey() {
    return llmApiKey;
  }

  public void setLlmApiKey(String llmApiKey) {
    this.llmApiKey = llmApiKey;
  }

  public String getLlmBaseUrl() {
    return llmBaseUrl;
  }

  public void setLlmBaseUrl(String llmBaseUrl) {
    this.llmBaseUrl = llmBaseUrl;
  }

  public String getLlmModel() {
    return llmModel;
  }

  public void setLlmModel(String llmModel) {
    this.llmModel = llmModel;
  }

  public int getTaskTimeoutMs() {
    return taskTimeoutMs;
  }

  public void setTaskTimeoutMs(int taskTimeoutMs) {
    this.taskTimeoutMs = taskTimeoutMs;
  }

  public int getModelMinRemainingMs() {
    return modelMinRemainingMs;
  }

  public void setModelMinRemainingMs(int modelMinRemainingMs) {
    this.modelMinRemainingMs = modelMinRemainingMs;
  }

  public boolean isEnableReact() {
    return enableReact;
  }

  public void setEnableReact(boolean enableReact) {
    this.enableReact = enableReact;
  }

  public boolean isEnableLlm() {
    return enableLlm;
  }

  public void setEnableLlm(boolean enableLlm) {
    this.enableLlm = enableLlm;
  }

  public String getForceRoute() {
    return forceRoute;
  }

  public void setForceRoute(String forceRoute) {
    this.forceRoute = forceRoute == null ? "" : forceRoute;
  }

  public int getFreqCapPerDay() {
    return freqCapPerDay;
  }

  public void setFreqCapPerDay(int freqCapPerDay) {
    this.freqCapPerDay = freqCapPerDay;
  }

  public int getFreqWindowHours() {
    return freqWindowHours;
  }

  public void setFreqWindowHours(int freqWindowHours) {
    this.freqWindowHours = freqWindowHours;
  }

  public int getCooldownMinutes() {
    return cooldownMinutes;
  }

  public void setCooldownMinutes(int cooldownMinutes) {
    this.cooldownMinutes = cooldownMinutes;
  }

  public int getSimilarSuppressMinutes() {
    return similarSuppressMinutes;
  }

  public void setSimilarSuppressMinutes(int similarSuppressMinutes) {
    this.similarSuppressMinutes = similarSuppressMinutes;
  }

  public int getSceneDedupeMinutes() {
    return sceneDedupeMinutes;
  }

  public void setSceneDedupeMinutes(int sceneDedupeMinutes) {
    this.sceneDedupeMinutes = sceneDedupeMinutes;
  }

  public int getExposureHistoryWindowHours() {
    return exposureHistoryWindowHours;
  }

  public void setExposureHistoryWindowHours(int exposureHistoryWindowHours) {
    this.exposureHistoryWindowHours = exposureHistoryWindowHours;
  }

  public int getNotInterestedTtlDays() {
    return notInterestedTtlDays;
  }

  public void setNotInterestedTtlDays(int notInterestedTtlDays) {
    this.notInterestedTtlDays = notInterestedTtlDays;
  }

  public int getDndStartHour() {
    return dndStartHour;
  }

  public void setDndStartHour(int dndStartHour) {
    this.dndStartHour = dndStartHour;
  }

  public int getDndEndHour() {
    return dndEndHour;
  }

  public void setDndEndHour(int dndEndHour) {
    this.dndEndHour = dndEndHour;
  }

  public int getMaxMerchants() {
    return maxMerchants;
  }

  public void setMaxMerchants(int maxMerchants) {
    this.maxMerchants = maxMerchants;
  }

  public int getMaxProductsPerMerchant() {
    return maxProductsPerMerchant;
  }

  public void setMaxProductsPerMerchant(int maxProductsPerMerchant) {
    this.maxProductsPerMerchant = maxProductsPerMerchant;
  }

  public int getMaxSameCategory() {
    return maxSameCategory;
  }

  public void setMaxSameCategory(int maxSameCategory) {
    this.maxSameCategory = maxSameCategory;
  }

  public int getCandidateLimit() {
    return candidateLimit;
  }

  public void setCandidateLimit(int candidateLimit) {
    this.candidateLimit = candidateLimit;
  }

  public int getRecallLimit() {
    return recallLimit;
  }

  public void setRecallLimit(int recallLimit) {
    this.recallLimit = recallLimit;
  }

  public int getReactMaxSteps() {
    return reactMaxSteps;
  }

  public void setReactMaxSteps(int reactMaxSteps) {
    this.reactMaxSteps = reactMaxSteps;
  }

  public int getToolTimeoutMs() {
    return toolTimeoutMs;
  }

  public void setToolTimeoutMs(int toolTimeoutMs) {
    this.toolTimeoutMs = toolTimeoutMs;
  }

  public int getSupplementTimeoutMs() {
    return supplementTimeoutMs;
  }

  public void setSupplementTimeoutMs(int supplementTimeoutMs) {
    this.supplementTimeoutMs = supplementTimeoutMs;
  }

  public int getSupplementMin() {
    return supplementMin;
  }

  public void setSupplementMin(int supplementMin) {
    this.supplementMin = supplementMin;
  }

  public String getDbPath() {
    return dbPath;
  }

  public void setDbPath(String dbPath) {
    this.dbPath = dbPath;
  }
}
