from __future__ import annotations

import time
import uuid
from typing import Any


class TraceRecorder:
    """Structured run trace; required fields per tech design §10.5."""

    def __init__(self, run_id: str | None = None) -> None:
        self.run_id = run_id or f"run_{uuid.uuid4().hex[:10]}"
        self.started = time.perf_counter()
        self.steps: list[dict[str, Any]] = []
        self.route_mode: str | None = None
        self.opportunity_result: str | None = None
        self.decision_reason_code: str | None = None
        self.fallback_reason: str | None = None
        self.deadline_at: str | None = None
        self.budget_exhausted: bool = False
        self.model_calls = 0
        self.guard_results: list[dict[str, Any]] = []

    def set_meta(
        self,
        *,
        route_mode: str | None = None,
        opportunity_result: str | None = None,
        decision_reason_code: str | None = None,
        fallback_reason: str | None = None,
        deadline_at: str | None = None,
        budget_exhausted: bool | None = None,
    ) -> None:
        if route_mode is not None:
            self.route_mode = route_mode
        if opportunity_result is not None:
            self.opportunity_result = opportunity_result
        if decision_reason_code is not None:
            self.decision_reason_code = decision_reason_code
        if fallback_reason is not None:
            self.fallback_reason = fallback_reason
        if deadline_at is not None:
            self.deadline_at = deadline_at
        if budget_exhausted is not None:
            self.budget_exhausted = budget_exhausted

    def add_step(
        self,
        *,
        step_index: int,
        decision_reason_code: str,
        tool_name: str | None = None,
        result_status: str = "ok",
        evidence_ids: list[str] | None = None,
        actions: list[dict] | None = None,
        observations: list[dict] | None = None,
        latency_ms: float | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        row: dict[str, Any] = {
            "run_id": self.run_id,
            "step_index": step_index,
            "route_mode": self.route_mode,
            "opportunity_result": self.opportunity_result,
            "decision_reason_code": decision_reason_code,
            "tool_name": tool_name,
            "result_status": result_status,
            "evidence_ids": evidence_ids or [],
            "guard_results": [],
            "fallback_reason": self.fallback_reason,
            "latency_ms": latency_ms,
            "model_calls": self.model_calls,
            "actions": actions or [],
            "observations": observations or [],
        }
        if extra:
            row.update(extra)
        self.steps.append(row)

    def add_guard(self, result: dict[str, Any]) -> None:
        self.guard_results.append(result)
        if self.steps:
            self.steps[-1]["guard_results"] = list(self.guard_results)

    def bump_model(self, n: int = 1) -> None:
        self.model_calls += n

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "route_mode": self.route_mode,
            "opportunity_result": self.opportunity_result,
            "decision_reason_code": self.decision_reason_code,
            "fallback_reason": self.fallback_reason,
            "deadline_at": self.deadline_at,
            "budget_exhausted": self.budget_exhausted,
            "model_calls": self.model_calls,
            "latency_ms": round((time.perf_counter() - self.started) * 1000, 2),
            "guard_results": self.guard_results,
            "steps": self.steps,
        }
