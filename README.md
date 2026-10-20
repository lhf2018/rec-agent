# rec-agent

场景触发的**主动推荐 Agent** MVP：用户不搜索、不对话时，系统根据场景与用户状态判断是否值得推荐；由推荐引擎产出封闭候选，再按路由走规则 / 一次模型决策 / ReAct；经独立校验与交付治理后站内展示，并支持反馈闭环。

远程仓库：<https://github.com/lhf2018/rec-agent>

## 它解决什么问题

| 决策 | 问题 | 谁负责 |
| --- | --- | --- |
| 主动决策 | 现在能不能推、值不值得推？ | 资格 Eligibility + 机会 Opportunity |
| 推荐决策 | 推哪些商家/商品、理由是什么？ | 引擎 + RULE / DIRECT / REACT |
| 交付决策 | 算出来的结果能不能给用户看？ | Guard + 交付治理 |

推荐形态：以**商家**为主，每家关联 **1–3** 个商品。第一版使用本地种子数据 + **Mock LLM**（无需 API Key）。

## 仓库结构

```text
rec_agent/          # 核心代码
  policy.py          # 资格 / 机会 / 路由 / 交付
  snapshot.py        # 只读用户快照（过期过滤）
  engine.py          # 召回 · 硬过滤 · 排序
  feedback.py        # 负向反馈（不经 Agent）
  runtime/           # loop · guard · registry · trace
  api.py             # FastAPI
  service.py         # 任务编排
fixtures/            # 商家与用户种子
tests/               # 单测
eval/                # 三臂离线评测
web/                 # 本地演示页
docs/                # 技术方案 · 架构图 · 评审 · 待确认 · 迭代记录
```

## 环境要求

- Python ≥ 3.10
- Windows / macOS / Linux

## 快速开始

```bash
git clone https://github.com/lhf2018/rec-agent.git
cd rec-agent

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

演示页可发起推荐、查看 Trace，并对第一家点「不感兴趣」（影响下次硬过滤）。

## 主要接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/v1/recommendation-tasks` | 创建推荐任务（幂等） |
| GET | `/v1/recommendation-tasks/{id}` | 查询结果 |
| GET | `/v1/recommendation-tasks/{id}/trace` | 结构化步骤（内部） |
| POST | `/v1/recommendations/{id}/feedback` | 曝光 / 点击 / 不感兴趣 |
| GET | `/health` | 健康检查 |

示例：

```bash
curl -X POST http://127.0.0.1:8000/v1/recommendation-tasks ^
  -H "Content-Type: application/json" ^
  -d "{\"tenant_id\":\"t_demo\",\"user_id\":\"u_noodle_regular\",\"scene\":{\"scene_id\":\"demo_1\",\"trigger\":\"impression\",\"channel\":\"nearby\",\"occurred_at\":\"2026-10-20T12:10:00+08:00\",\"location\":{\"lng\":121.47,\"lat\":31.23,\"district\":\"南京西路\"},\"radius_m\":3000,\"meal_period\":\"lunch\"}}"
```

## 配置

见 `.env.example`。常用环境变量前缀 `REC_AGENT_`：

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `USE_MOCK_LLM` | `true` | Mock 决策，无需 Key |
| `ENABLE_LLM` | `true` | 允许 DIRECT/REACT |
| `ENABLE_REACT` | `true` | 允许多步 ReAct |
| `TASK_TIMEOUT_MS` | `20000` | 任务超时（待验证） |
| `FREQ_CAP_PER_DAY` 等 | 见 `docs/待确认.md` | 频控等业务假设 |

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [`docs/技术方案.md`](docs/技术方案.md) | 系统设计 v1.5 |
| [`docs/架构图.html`](docs/架构图.html) | 跨团队架构图（浏览器打开） |
| [`docs/架构评审-v1.5.md`](docs/架构评审-v1.5.md) | 架构评审结论 |
| [`docs/待确认.md`](docs/待确认.md) | 未拍板参数 |
| [`docs/迭代记录.md`](docs/迭代记录.md) | 本地迭代与里程碑 |

## 里程碑（MVP）

- [x] M0 引擎硬过滤 + 单测  
- [x] M1 资格 / 机会 / 路由  
- [x] M2/M3 DIRECT / REACT（mock）  
- [x] M4 三臂评测（Engine / DIRECT / REACT）  
- [x] M5 站内演示页  
- [ ] 接入真实 LLM  
- [ ] 线上小流量实验设计  

## 许可证

未单独声明时，以仓库所有者约定为准。
