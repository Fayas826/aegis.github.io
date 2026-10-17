from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from typing import Optional, Dict
import os

from core.cognition.reasoning.planner import planner
from core.cognition.reasoning.context_engine import context_engine
from core.orchestration.task_engine import get_task_engine
from core.reliability.experience_engine import experience_engine
from core.reliability.reliability_engine import reliability_engine
from perception.voice.sonic_engine_v2 import sonic_engine

# Phase 34.5 — Conversational Autonomy integration
from core.cognition.reasoning.conversational_interpreter import conversational_interpreter
from core.cognition.memory.context_memory import conversational_context

router = APIRouter()

_JARVIS_API_KEY = os.getenv("JARVIS_API_KEY", "")


async def _verify_api_key(x_jarvis_key: Optional[str] = Header(default=None)) -> dict:
    """Enterprise API key guard. Set JARVIS_API_KEY env var to enable protection.
    If env var is empty, auth is open (dev mode). Set a strong key for production."""
    if _JARVIS_API_KEY and x_jarvis_key != _JARVIS_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Jarvis-Key header.")
    return {"identity": "verified"}


class Query(BaseModel):
    message: str
    metadata: Optional[Dict] = None


def _summarize_task(query_text: str, task) -> str:
    if task.status == "SUCCESS":
        return f"Execution complete for '{query_text}'. {len(task.steps)} step(s) finished cleanly."
    if task.status == "BLOCKED":
        return f"I understood '{query_text}', but I blocked execution because the request was not safe enough to run automatically."
    if task.status == "FAILED":
        failed_step = next((step for step in task.steps if step.status == "FAILED"), None)
        detail = failed_step.description if failed_step else "the execution plan"
        return f"Execution for '{query_text}' failed while running {detail}."
    return f"Execution finished with status {task.status} for '{query_text}'."


@router.post("/jarvis")
async def jarvis_endpoint(query: Query, token: dict = Depends(_verify_api_key)):
    context = context_engine.get_context()
    query.metadata = query.metadata or {}
    query.metadata.update(context)

    # Phase 34.5 — Conversational interpretation before planning
    intent = await conversational_interpreter.interpret_async(query.message)

    # Trust boundary: refuse to execute screen-originated text as a command
    if not intent.trusted:
        return {
            "type": "TRUST_BLOCKED",
            "status": "BLOCKED",
            "response": "I detected that this instruction may have originated from on-screen content rather than a direct user command. Please rephrase.",
            "task_id": None,
            "performance": {"execution_time": 0, "success_rate": 0.0, "context_state": context.get("state")},
            "steps": [],
            "optimizations": [],
            "intent": {"type": intent.intent_type, "trusted": intent.trusted, "confidence": intent.confidence},
        }

    # Record the user turn into conversational context (Phase 34.4)
    conversational_context.add_turn("user", query.message)

    # Use the resolved utterance for planning (pronoun/reference substituted)
    effective_message = intent.resolved_utterance
    query.metadata["intent_type"] = intent.intent_type
    query.metadata["is_correction"] = intent.is_correction
    query.metadata["intent_confidence"] = intent.confidence

    # Canonical Windows desktop seam: specialized browser/filesystem/chat routes
    # remain below, but actionable GUI goals use the universal worker.
    from backend.routes.universal_desktop_route import execute_if_canonical_desktop_goal
    canonical_desktop = await execute_if_canonical_desktop_goal(effective_message, query.metadata)
    if canonical_desktop is not None:
        return canonical_desktop
    task = await planner.create_plan(effective_message, context=query.metadata)
    task_engine = get_task_engine()

    if task.status == "BLOCKED" or not task.steps:
        response_text = _summarize_task(query.message, task)
        return {
            "type": "TASK_EXECUTION",
            "status": "BLOCKED",
            "response": response_text,
            "task_id": task.task_id,
            "performance": {"execution_time": 0, "success_rate": 0.0, "context_state": context.get("state")},
            "steps": [],
            "optimizations": [],
        }

    try:
        executed_task = await task_engine.execute_task(task)
    except Exception as exc:
        reliability_engine.analyze_failure("JARVIS_QUERY", str(exc), {"query": query.message})
        executed_task = task
        executed_task.status = "FAILED"

    success_rate = (
        sum(1 for step in executed_task.steps if step.status == "SUCCESS") / len(executed_task.steps)
        if executed_task.steps
        else 0.0
    )

    reliability_engine.verify_repair(
        query.message,
        lambda: executed_task.status == "SUCCESS" or executed_task.status == "BLOCKED",
    )

    response_text = _summarize_task(query.message, executed_task)
    final_status = "NOMINAL" if executed_task.status == "SUCCESS" else executed_task.status

    # Record assistant turn into conversational context (Phase 34.4)
    conversational_context.add_turn("assistant", response_text, metadata={"task_id": executed_task.task_id, "status": final_status})

    if response_text and context.get("state") != "FOCUSED" and executed_task.status != "BLOCKED":
        await sonic_engine.speak(response_text)

    return {
        "type": "TASK_EXECUTION",
        "status": final_status,
        "response": response_text,
        "task_id": executed_task.task_id,
        "performance": {
            "execution_time": (executed_task.completed_at - executed_task.created_at) if executed_task.completed_at else 0,
            "success_rate": success_rate,
            "context_state": context.get("state"),
            "ux_telemetry": experience_engine.get_ux_telemetry(),
        },
        "steps": [
            {"description": step.description, "status": step.status, "retries": step.retry_count, "error": step.error}
            for step in executed_task.steps
        ],
        "optimizations": reliability_engine.get_reliability_report().get("recent_patterns", []),
        # Phase 34.5 intent metadata surfaced in response
        "intent": {
            "type": intent.intent_type,
            "is_correction": intent.is_correction,
            "confidence": intent.confidence,
            "resolution_method": intent.resolution_method,
            "resolved_utterance": intent.resolved_utterance,
        },
    }
