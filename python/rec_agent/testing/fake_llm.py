from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse


def _extract_json_blob(content: str) -> dict[str, Any]:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(content[start : end + 1])
            except json.JSONDecodeError:
                return {}
        return {}


def _candidates_from_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content") or ""
        if isinstance(content, list):
            continue
        data = _extract_json_blob(str(content))
        cands = data.get("candidates")
        if isinstance(cands, list) and cands:
            return cands
    return []


def _tool_names_called(messages: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for msg in messages:
        for tc in msg.get("tool_calls") or []:
            fn = (tc.get("function") or {}).get("name")
            if fn:
                names.add(fn)
        if msg.get("role") == "tool":
            # no name on tool role in openai; infer from prior assistant if needed
            pass
    return names


def _build_submit(cands: list[dict[str, Any]], *, self_score: float = 0.86) -> dict[str, Any]:
    merchants = []
    for c in cands[:3]:
        products = c.get("products") or []
        if not products:
            continue
        p = products[0]
        mid = c["merchant_id"]
        pid = p["product_id"]
        merchants.append(
            {
                "merchant_id": mid,
                "name": c.get("name") or mid,
                "reason": (
                    f"模型选择，距离约 {int(c.get('distance_m') or 0)} 米，"
                    f"人均约 {int(c.get('avg_price') or 0)} 元"
                ),
                "evidence_ids": [c.get("evidence_id") or f"cand_{mid}", "snap_history"],
                "products": [
                    {
                        "product_id": pid,
                        "name": p.get("name") or pid,
                        "reason": f"有库存，价格 {int(p.get('price') or 0)} 元落在可接受区间",
                        "evidence_ids": [f"prod_{pid}", "snap_price_band"],
                    }
                ],
            }
        )
    return {
        "decision": "recommend" if merchants else "skip",
        "merchants": merchants,
        "self_score": self_score,
    }


def plan_tool_calls(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Deterministic planner for smoke tests (OpenAI tool_calls shape)."""
    tool_names = set()
    for t in tools or []:
        fn = (t.get("function") or {}).get("name")
        if fn:
            tool_names.add(fn)

    called = _tool_names_called(messages)
    # Also count tool results already present by scanning assistant tool_calls history
    for msg in messages:
        if msg.get("role") == "assistant":
            for tc in msg.get("tool_calls") or []:
                fn = (tc.get("function") or {}).get("name")
                if fn:
                    called.add(fn)

    cands = _candidates_from_messages(messages)

    # DIRECT-style: only submit allowed
    if tool_names == {"submit_recommendation"} or (
        tools and len(tool_names) == 1 and "submit_recommendation" in tool_names
    ):
        args = _build_submit(cands)
        return [
            {
                "id": "call_submit",
                "type": "function",
                "function": {
                    "name": "submit_recommendation",
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            }
        ]

    # REACT path
    if "get_exposure_history" in tool_names and "get_exposure_history" not in called:
        return [
            {
                "id": "call_expo",
                "type": "function",
                "function": {
                    "name": "get_exposure_history",
                    "arguments": json.dumps({"limit": 10}),
                },
            }
        ]

    if "get_product_detail" in tool_names and "get_product_detail" not in called and cands:
        prods = cands[0].get("products") or []
        if prods:
            return [
                {
                    "id": "call_prod",
                    "type": "function",
                    "function": {
                        "name": "get_product_detail",
                        "arguments": json.dumps({"product_id": prods[0]["product_id"]}),
                    },
                }
            ]

    args = _build_submit(cands, self_score=0.9)
    # Prefer short-term 面食 if present in candidates (conflict smoke)
    noodle = [c for c in cands if c.get("category") == "面食"]
    if noodle:
        args = _build_submit(noodle + [c for c in cands if c not in noodle], self_score=0.9)

    return [
        {
            "id": "call_submit",
            "type": "function",
            "function": {
                "name": "submit_recommendation",
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        }
    ]


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    server_version = "FakeOpenAI/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if not parsed.path.endswith("/chat/completions"):
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            body = {}
        messages = body.get("messages") or []
        tools = body.get("tools")
        tool_calls = plan_tool_calls(messages, tools)
        payload = {
            "id": "chatcmpl_fake",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": tool_calls,
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class FakeLLMServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._httpd = HTTPServer((host, port), FakeOpenAIHandler)
        self.host, self.port = self._httpd.server_address[0], self._httpd.server_address[1]
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}/v1"

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=2)
