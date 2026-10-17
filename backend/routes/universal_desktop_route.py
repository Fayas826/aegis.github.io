"""Canonical Windows desktop request seam.

This module keeps the HTTP route small and independent from the legacy desktop
controller. It delegates the actual observe/plan/act/verify lifecycle to the
existing TaskEngine and universal GUI worker.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional


_CANONICAL_COMPUTER_USE = {"MULTI_STEP_TASK", "CONTEXTUAL_DESKTOP", "SESSION_CONTINUE"}
_TERMINAL = {
    "VERIFIED", "SUCCESS", "BLOCKED", "UNAVAILABLE", "CANCELLED", "TIMEOUT", "FAILED",
    "UNVERIFIED", "VERIFICATION_FAILED", "PENDING_CONFIRMATION", "PERMISSION_REQUIRED",
    "CLARIFICATION_REQUIRED",
}


def _is_canonical_desktop(classification: Any) -> bool:
    return classification.category == "DESKTOP_ACTION" or (
        classification.category == "COMPUTER_USE" and
        classification.action_type in _CANONICAL_COMPUTER_USE
    )


async def execute_if_canonical_desktop_goal(
    message: str, metadata: Optional[Dict[str, Any]] = None
) -> Optional[dict[str, Any]]:
    """Return a truthful response for actionable Windows GUI requests.

    ``None`` means this request belongs to another route (browser, filesystem,
    chat, or an existing specialized workflow). No application-specific launch
    table is used here.
    """
    from core.orchestration.desktop_command_router import desktop_command_router
    from core.orchestration.computer_use_session import computer_use_session_manager
    from core.orchestration.task_engine import Task, TaskStep, get_task_engine

    classification = desktop_command_router.classify(
        message, computer_use_session_manager.get_session_context()
    )
    if not desktop_command_router.is_actionable(classification) or not _is_canonical_desktop(classification):
        return None

    metadata = metadata if isinstance(metadata, dict) else {}
    context = {
        key: metadata[key]
        for key in (
            "allowed_roots", "evidence_dir", "verification", "desktop_allowed",
            "deadline", "max_steps", "session_id",
        )
        if key in metadata
    }
    if "deadline" not in context:
        context["deadline"] = time.time() + min(180.0, float(metadata.get("time_budget_s", 120.0)))
    context["max_steps"] = max(1, min(40, int(context.get("max_steps", 20))))
    step = TaskStep(
        description=message,
        action_type="UNIVERSAL_GUI_TASK",
        payload={"goal": message, "surface": "desktop", "max_steps": context["max_steps"]},
    )
    task = Task(original_intent=message, steps=[step], context=context)
    started = time.monotonic()
    try:
        executed = await get_task_engine().execute_task(task)
    except Exception as exc:
        return {
            "type": "TASK_EXECUTION",
            "status": "FAILED",
            "response": "The universal desktop runtime failed before completion.",
            "speakable_text": "The universal desktop runtime failed before completion.",
            "task_id": task.task_id,
            "source": "UNIVERSAL_GUI_TASK",
            "error_type": type(exc).__name__,
            "performance": {"execution_time": round(time.monotonic() - started, 3), "success_rate": 0.0},
            "steps": [{"description": message, "status": "FAILED"}],
            "optimizations": [],
        }

    result = step.result if isinstance(step.result, dict) else {}
    raw_status = str(result.get("status") or result.get("verification_status") or executed.status).upper()
    status = raw_status if raw_status in _TERMINAL else "UNVERIFIED"
    verification = result.get("verification") if isinstance(result.get("verification"), dict) else {}
    if status == "VERIFIED":
        response = f"Verified completion: {message}"
    elif status == "SUCCESS":
        response = f"The desktop action completed, but the goal is not independently verified: {message}"
    elif status == "PENDING_CONFIRMATION":
        response = f"Confirmation is required before continuing: {message}"
    else:
        response = f"Desktop goal ended with status {status}: {message}"
    return {
        "type": "TASK_EXECUTION",
        "status": status,
        "response": response,
        "speakable_text": response,
        "task_id": executed.task_id,
        "source": "UNIVERSAL_GUI_TASK",
        "provider": result.get("provider"),
        "model": result.get("model"),
        "verified": status == "VERIFIED",
        "verification": verification,
        "performance": {
            "execution_time": round(time.monotonic() - started, 3),
            "success_rate": 1.0 if status == "VERIFIED" else 0.0,
        },
        "steps": [{
            "description": message,
            "status": status,
            "verification": verification.get("status", "UNKNOWN"),
        }],
        "optimizations": [],
    }


__all__ = ["execute_if_canonical_desktop_goal"]
