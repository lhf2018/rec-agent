from __future__ import annotations

from typing import Any


TOOL_WHITELIST = frozenset(
    {
        "get_merchant_detail",
        "get_product_detail",
        "compare_candidates",
        "get_exposure_history",
        "supplement_recall",
        "submit_recommendation",
    }
)


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "get_merchant_detail": {
        "description": "Read merchant facts for an id in the closed candidate set.",
        "parameters": {
            "type": "object",
            "required": ["merchant_id"],
            "properties": {"merchant_id": {"type": "string"}},
        },
        "timeout_ms_key": "tool_timeout_ms",
    },
    "get_product_detail": {
        "description": "Read product facts; product must belong to an allowed merchant.",
        "parameters": {
            "type": "object",
            "required": ["product_id"],
            "properties": {"product_id": {"type": "string"}},
        },
        "timeout_ms_key": "tool_timeout_ms",
    },
    "compare_candidates": {
        "description": "Side-by-side compare two allowed merchant candidates.",
        "parameters": {
            "type": "object",
            "required": ["left_id", "right_id"],
            "properties": {
                "left_id": {"type": "string"},
                "right_id": {"type": "string"},
                "aspects": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": ["price", "distance", "category", "open_now", "score"],
                    },
                },
            },
        },
        "timeout_ms_key": "tool_timeout_ms",
    },
    "get_exposure_history": {
        "description": "Recent impression / not_interested summary for dedupe.",
        "parameters": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
        },
        "timeout_ms_key": "tool_timeout_ms",
    },
    "supplement_recall": {
        "description": "At most once per run; re-hard-filters; bumps candidate version.",
        "parameters": {
            "type": "object",
            "properties": {
                "strategy": {
                    "type": "string",
                    "enum": ["widen_radius", "adjacent_category", "trending"],
                }
            },
        },
        "timeout_ms_key": "supplement_timeout_ms",
        "max_per_run": 1,
    },
    "submit_recommendation": {
        "description": "Terminate with structured merchants; Guard validates.",
        "parameters": {
            "type": "object",
            "required": ["merchants"],
            "properties": {
                "decision": {"type": "string", "enum": ["recommend", "skip"]},
                "merchants": {"type": "array"},
                "self_score": {"type": "number"},
            },
        },
        "timeout_ms_key": "tool_timeout_ms",
    },
}


class ToolRegistry:
    def names(self) -> list[str]:
        return sorted(TOOL_WHITELIST)

    def schema(self, name: str) -> dict[str, Any] | None:
        return TOOL_SCHEMAS.get(name)

    def is_allowed(self, name: str) -> bool:
        return name in TOOL_WHITELIST

    def validate_args(self, name: str, args: dict[str, Any]) -> str | None:
        if name not in TOOL_WHITELIST:
            return "unknown_tool"
        schema = TOOL_SCHEMAS[name]["parameters"]
        required = schema.get("required") or []
        for key in required:
            if key not in args or args[key] in (None, ""):
                return f"missing_argument:{key}"
        if name == "supplement_recall":
            strategy = args.get("strategy") or "widen_radius"
            if strategy not in {"widen_radius", "adjacent_category", "trending"}:
                return "invalid_argument:strategy"
        if name == "compare_candidates":
            aspects = args.get("aspects")
            if aspects is not None:
                allowed = {"price", "distance", "category", "open_now", "score"}
                if not isinstance(aspects, list) or any(a not in allowed for a in aspects):
                    return "invalid_argument:aspects"
        return None
