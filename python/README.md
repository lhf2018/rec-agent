# rec-agent · Python

场景触发的**主动推荐 Agent** MVP（Python / FastAPI）。用户不搜索、不对话时，系统根据场景与用户状态判断是否值得推荐；由推荐引擎产出封闭候选，再按路由走规则 / 一次模型决策 / ReAct；经独立校验与交付治理后站内展示，并支持反馈闭环。

设计文档见仓库根目录 [`docs/技术方案.md`](../docs/技术方案.md)。

## 它解决什么问题

| 决策 | 问题 | 谁负责 |
| --- | --- | --- |
| 主动决策 | 现在能不能推、值不值得推？ | 资格 Eligibility + 机会 Opportunity |
| 推荐决策 | 推哪些商家/商品、理由是什么？ | 引擎 + RULE / DIRECT / REACT |
| 交付决策 | 算出来的结果能不能给用户看？ | Guard + 交付治理 |

推荐形态：以**商家**为主，每家关联 **1–3** 个商品。第一版使用本地种子数据 + **Mock LLM**（无需 API Key）。

## 目录

```text
rec_agent/          # 核心代码
  policy.py          # 资格 / 机会 / 路由 / 交付
  snapshot.py        # 只读用户快照
  engine.py          # 召回 · 硬过滤 · 排序
  feedback.py        # 负向反馈（不经 Agent）
  runtime/           # loop · guard · registry · trace · budget
  api.py             # FastAPI
  service.py         # 任务编排
fixtures/            # 商家与用户种子
tests/               # 单测
eval/                # 离线评测（含规则重排基线）
web/                 # 本地演示页
```

## 环境要求

- Python ≥ 3.10
- Windows / macOS / Linux

## 快速开始

在本目录（`python/`）下：

```bash
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# macOS / Linux
# source .venv/bin/activate

pip install -e ".[dev]"
cp .env.example .env   # 可选；默认已是 mock
```

### 测试与评测

```bash
pytest -q
python eval/runner.py
```

### 启动服务

```bash
uvicorn rec_agent.api:app --reload --port 8000
```

浏览器打开 <http://127.0.0.1:8000/>  
API 文档：<http://127.0.0.1:8000/docs>

## 演示用户（fixtures）

| user_id | 说明 |
| --- | --- |
| `u_noodle_regular` | 面食老客 |
| `u_dietary` | 忌口「辣」+ 拉黑店 |
| `u_newcomer` | 新客（偏场景/热门） |
| `u_budget` | 低客单价 |
| `u_conflict` | 长期汉堡 vs 短期面食 → 易走 REACT |
| `u_opt_out` | 关闭个性化 → `skipped` |

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/v1/recommendation-tasks` | 创建推荐任务（幂等） |
| GET | `/v1/recommendation-tasks/{id}` | 查询结果 |
| GET | `/v1/recommendation-tasks/{id}/trace` | 结构化步骤 |
| POST | `/v1/recommendations/{id}/feedback` | 曝光 / 点击 / 不感兴趣 |
| GET | `/health` | 健康检查 |

## 配置

见 `.env.example`。环境变量前缀 `REC_AGENT_`。

## 里程碑（MVP）

- [x] 引擎硬过滤 + 资格 / 机会 / 路由
- [x] DIRECT / REACT（mock）+ Guard + Store 动态状态
- [x] deadline 预算 + 统一评测（含规则重排）
- [ ] 接入真实 LLM
- [ ] 线上小流量实验设计
