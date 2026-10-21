# rec-agent

场景触发的**主动推荐 Agent**（技术方案 v1.6）。同一仓库内提供两种语言实现，设计文档共用。

远程仓库：<https://github.com/lhf2018/rec-agent>

## 仓库结构

```text
docs/       # 技术方案 · 架构图 · 评审（两种实现共用）
python/     # 当前 Python 实现（FastAPI + Mock LLM · MVP 可运行）
java/       # Java 实现（Maven · 与方案同构的包结构）
```

| 目录 | 说明 | 入门 |
| --- | --- | --- |
| [`python/`](./python/) | 完整 MVP：资格/机会、引擎、DIRECT/REACT、Guard、Store、评测、演示页 | 见 [`python/README.md`](./python/README.md) |
| [`java/`](./java/) | Java 版同构实现（Spring Boot API + 核心领域模块） | 见 [`java/README.md`](./java/README.md) |
| [`docs/`](./docs/) | 设计真源 | [`docs/技术方案.md`](./docs/技术方案.md) |

## 快速开始（Python）

```bash
cd python
python -m venv .venv
.\.venv\Scripts\activate          # Windows
pip install -e ".[dev]"
pytest -q
uvicorn rec_agent.api:app --reload --port 8000
```

## 快速开始（Java）

```bash
cd java
mvn -q test
mvn -q spring-boot:run
```

默认端口 `8080`，健康检查：`GET /health`。
