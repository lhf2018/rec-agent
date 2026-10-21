# rec-agent · Java

与 [`docs/技术方案.md`](../docs/技术方案.md) v1.6 同构的 Java MVP（Spring Boot 3.3 / Java 17），对齐 Python `rec_agent/`：资格与机会 → 快照 → 引擎 → 路由（RULE/DIRECT/REACT/FALLBACK + RULE_RERANK）→ Mock Agent → Guard → 交付 → 反馈 TTL → SQLite。

## 要求

- JDK 17+（本机可用 `JAVA_HOME=C:\Program Files\Microsoft\jdk-17.0.18.8-hotspot`）
- Maven 3.9+（可选本地：`java/.mvn-tools/apache-maven-3.9.9/bin/mvn.cmd`）

## 构建与测试

```bash
cd java
mvn -q test
mvn -q spring-boot:run
```

Windows（显式 JDK / 本地 Maven）：

```powershell
$env:JAVA_HOME = "C:\Program Files\Microsoft\jdk-17.0.18.8-hotspot"
.\.mvn-tools\apache-maven-3.9.9\bin\mvn.cmd -f pom.xml test
```

- 健康检查：`GET http://127.0.0.1:8080/health`
- 创建任务：`POST /v1/recommendation-tasks`
- 查任务 / 轨迹：`GET /v1/recommendation-tasks/{id}` · `GET .../trace`
- 反馈：`POST /v1/recommendations/{id}/feedback`

种子数据：`src/main/resources/fixtures/`（与 Python `fixtures/` 同源）。

## 包结构

```text
com.recagent
  api          # REST（snake_case JSON）
  service      # 任务编排
  policy       # 资格 / 机会 / 路由 / 交付
  engine       # 四路召回 · 硬过滤 · 排序
  runtime      # loop / guard / budget / trace
  tools        # 白名单工具
  feedback     # 反馈 → Store TTL
  snapshot     # Catalog 静态 ⊕ Store 动态
  store        # SQLite 动态状态
  catalog      # 静态种子
  model        # DTO / 领域对象
  time         # UTC
  config       # AppSettings
```
