# 人工抽查清单（离线 / 小流量）

用于对照四臂评测与 Trace，确认 Agent 增益而非「漂亮文案」。

## 每条抽查必看

1. **路由是否合理**：无工具缺口不应进 REACT；能 RULE 不强制 DIRECT。
2. **工具观察是否参与决策**：REACT Trace 应出现 `get_exposure_history` / 对比或详情，且最终 `evidence_ids` 引用工具证据。
3. **硬约束**：忌口、拉黑、关店、预算外商家不得出现。
4. **理由可核验**：距离 / 价格数字必须能在证据中找到；禁止无单位裸数字绕过 Guard。
5. **提交协议**：必须以 `submit_recommendation` 结束；连续 Guard / 置信度失败应 `submit_arbitration` 降级，而不是硬推。
6. **机会决策**：suppress / skip 应写入 `opportunity_events`，原因码可读。

## 通过标准（建议）

| 项 | 标准 |
| --- | --- |
| selection_change | conflict 类用例 REACT 相对引擎应改变头名或类目 |
| quality_gain | REACT 理由引用 `cmp_` / `supp_` / `detail_` / `expo_` 之一 |
| 误杀 | 抽查 20 条交付结果，硬约束违规 = 0 |
| 成本 | DIRECT ≤ 1 model call；REACT 中位步数 ≤ `react_max_steps` |

## 小流量实验（有真实行为后）

- 臂：Engine vs RuleRerank vs Agent（DIRECT+REACT 按路由）
- 主指标：点击 / 转化；辅：跳过率、不感兴趣率、延迟、model_calls
- 成功线（建议默认，产品可改）：见下表与 `docs/待确认.md`

| 指标 | 建议成功线 | 说明 |
| --- | --- | --- |
| CTR 相对 Engine | **≥ +5% 相对提升** | 主指标；置信区间覆盖 0 则不宣称成功 |
| 转化相对 Engine | ≥ +3% 或持平且 CTR 达标 | 辅；样本不足时只报 CTR |
| 不感兴趣率 | ≤ Engine **+1pp** | 防打扰恶化 |
| 硬约束违规 | **= 0** | 一票否决 |
| p50 端到端延迟 | ≤ 2000ms | 含 Agent 臂 |
| REACT model_calls p95 | ≤ 4 | 成本闸 |
| 机会 suppress 占比 | 记录基线，不设成败 | 看 `opportunity_events.score` 分布 |

LLM 冒烟：`python eval/smoke_llm.py`（FakeOpenAI）；真模型加 `--real` 与 `REC_AGENT_LLM_*`。
