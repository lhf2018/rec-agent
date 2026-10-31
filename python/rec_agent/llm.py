from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from rec_agent.config import Settings
from rec_agent.runtime.registry import ToolRegistry
from rec_agent.secrets import redact_secret


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class LLMError(RuntimeError):
    pass


class OpenAICompatibleClient:
    """Minimal OpenAI-compatible chat.completions client (stdlib urllib)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.registry = ToolRegistry()

    def configured(self) -> bool:
        return bool(self.settings.llm_api_key and self.settings.llm_base_url and self.settings.llm_model)

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] = "auto",
        timeout_s: float = 30.0,
    ) -> LLMResponse:
        if not self.configured():
            raise LLMError("llm_not_configured")
        base = self.settings.llm_base_url.rstrip("/")
        url = f"{base}/chat/completions"
        body: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.settings.llm_api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            detail = redact_secret(detail, self.settings.llm_api_key)
            raise LLMError(f"llm_http_{e.code}:{detail}") from e
        except Exception as e:  # noqa: BLE001 — surface as LLMError for loop fallback
            raise LLMError(f"llm_transport:{redact_secret(str(e), self.settings.llm_api_key)}") from e

        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {}
            tool_calls.append(
                ToolCall(
                    id=str(tc.get("id") or f"call_{len(tool_calls)}"),
                    name=str(fn.get("name") or ""),
                    arguments=args if isinstance(args, dict) else {},
                )
            )
        return LLMResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            raw=payload,
        )

    def tools_for(self, names: list[str]) -> list[dict[str, Any]]:
        return self.registry.as_openai_tools(names)


def build_system_prompt(*, mode: str, max_merchants: int) -> str:
    return (
        "你是主动推荐 Agent，只能从 CLOSED candidates 里选商家，禁止编造 id。\n"
        f"路由模式 route_mode={mode}。最多推荐 {max_merchants} 家，每家 1–3 个商品。\n"
        "硬约束：必须遵守 snapshot.hard（忌口/拉黑/不感兴趣）；理由中的距离与价格必须来自候选或工具证据中的数字。\n"
        "evidence_ids 分袋：商家 reason 只能引用 cand_/detail_/cmp_/expo_/supp_/snap_history；"
        "商品 reason 只能引用 prod_{product_id}/snap_price_band。禁止把 prod_ 挂在商家上。\n"
        "REACT 时：先用工具补证据（曝光去重、对比、详情、必要时补召回），再 submit；"
        "DIRECT 时：禁止调用除 submit_recommendation 以外的工具。\n"
        "必须通过函数调用 submit_recommendation 结束；decision=skip 且 merchants=[] 表示不推。\n"
        "self_score 填 0~1 的把握分数。理由用中文，包含「距离约 X 米」「人均/价格约 Y 元」。"
    )


def build_user_payload(
    *,
    snapshot_summary: dict[str, Any],
    scene: dict[str, Any],
    candidates: list[dict[str, Any]],
    hints: dict[str, Any] | None = None,
) -> str:
    return json.dumps(
        {
            "snapshot": snapshot_summary,
            "scene": scene,
            "candidates": candidates,
            "hints": hints or {},
        },
        ensure_ascii=False,
    )
