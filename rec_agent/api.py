from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from rec_agent.models import CreateTaskRequest, FeedbackRequest
from rec_agent.service import RecommendationService

app = FastAPI(title="rec-agent", version="0.1.0")
service = RecommendationService()

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
def index():
    index_path = WEB_DIR / "index.html"
    if index_path.exists():
        return FileResponse(index_path)
    return {"message": "rec-agent API. See /docs"}


@app.post("/v1/recommendation-tasks")
def create_task(body: CreateTaskRequest):
    return service.create_task(body)


@app.get("/v1/recommendation-tasks/{task_id}")
def get_task(task_id: str):
    task = service.get_task(task_id)
    if not task:
        raise HTTPException(404, "task not found")
    return task


@app.get("/v1/recommendation-tasks/{task_id}/trace")
def get_trace(task_id: str):
    trace = service.get_trace(task_id)
    if not trace.get("task"):
        raise HTTPException(404, "task not found")
    return trace


@app.post("/v1/recommendations/{task_id}/feedback")
def feedback(task_id: str, body: FeedbackRequest):
    result = service.feedback(task_id, body)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "bad request"))
    return result


@app.get("/health")
def health():
    return {"ok": True}
