"""Shared universal-goal dispatch helpers.

This adapter is the seam between legacy task/DAG nodes and the universal
observe-plan-act-verify runtime. Legacy callers can opt out per node with
``execution_mode='legacy'``; the default is now the universal path.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


UNIVERSAL_ACTION_TYPES = frozenset({
    "APP_OPEN",
    "UI_AUTOMATION",
    "DESKTOP_CONTROL",
    "COMPUTER_USE",
    "AUTONOMOUS_DESKTOP",
    "UNIVERSAL_GUI_TASK",
    "AGENTIC_WEB_TASK",
    "MODEL_DRIVEN_WEB",
    "BROWSER_NAVIGATE_OR_PLAY",
    "WEB_EXECUTION",
})

NON_SUCCESS_STATUSES = frozenset({
    "FAILED", "BLOCKED", "UNAVAILABLE", "UNVERIFIED", "VERIFICATION_FAILED",
    "CANCELLED", "TIMEOUT", "ERROR", "SAFETY_BLOCK", "SAFETY_BLOCKED",
    "PENDING_CONFIRMATION", "PERMISSION_REQUIRED", "CLARIFICATION_REQUIRED",
})


def execution_status(result: Any) -> str:
    """Normalize known result envelopes without hiding a negative inner verdict.

    Action histories are deliberately not traversed: recovered attempts are not
    terminal goal results. Only canonical nested result/verification envelopes
    participate. Unknown or absent statuses never establish success.
    """
    if not isinstance(result, Mapping):
        return "FAILED"
    statuses = []
    stack = [result]
    seen = set()
    while stack and len(seen) < 32:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        for key in ("status", "verification_status", "adapter_status", "worker_status"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                statuses.append(value.strip().upper())
        for key in ("report", "details", "result", "data", "verification"):
            nested = item.get(key)
            if isinstance(nested, Mapping):
                stack.append(nested)
    for status in ("CANCELLED", "TIMEOUT", "PENDING_CONFIRMATION", "PERMISSION_REQUIRED", "BLOCKED",
                   "SAFETY_BLOCK", "SAFETY_BLOCKED", "FAILED", "ERROR", "UNAVAILABLE",
                   "CLARIFICATION_REQUIRED", "VERIFICATION_FAILED", "UNVERIFIED"):
        if status in statuses:
            return status
    if stack or not statuses or any(s not in {"SUCCESS", "VERIFIED", "VERIFIED_VALID", "COMPLETED"} for s in statuses):
        return "UNVERIFIED"
    return "VERIFIED" if any(s in {"VERIFIED", "VERIFIED_VALID"} for s in statuses) else (
        "SUCCESS" if statuses[0] == "SUCCESS" else "UNVERIFIED")


def universal_mode(payload: Optional[Mapping[str, Any]]) -> str:
    """Return the explicit mode, defaulting safely to universal."""

    value = str((payload or {}).get("execution_mode", "universal") or "universal").strip().lower()
    return value if value in {"universal", "legacy"} else "universal"


def result_is_success(result: Any) -> bool:
    """Accept only a real success, never a failed nested verification."""

    if not isinstance(result, Mapping):
        return False
    return execution_status(result) in {"SUCCESS", "VERIFIED"}


def goal_for_action(action_type: str, payload: Optional[Mapping[str, Any]] = None,
                    description: str = "") -> str:
    """Build a concrete natural-language goal for a legacy task node."""

    data = payload or {}
    explicit = data.get("goal") or data.get("directive") or data.get("task") or data.get("objective")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    action = str(action_type or "").upper()
    if action == "APP_OPEN":
        target = data.get("app_name") or data.get("application") or data.get("target")
        return f"Open {target}" if target else str(description or "Open the requested application")
    if action in {"UI_AUTOMATION", "DESKTOP_CONTROL"}:
        sub_action = str(data.get("action", "interact")).strip().lower()
        target = data.get("target") or data.get("element")
        window = data.get("window")
        suffix = f" in {window}" if window else ""
        if sub_action in {"type", "write", "paste"} and isinstance(data.get("text"), str):
            import json
            return f"Enter exactly {json.dumps(data['text'])}" + (f" into {target}" if target else " into the focused field") + suffix
        if target:
            return f"{sub_action.title()} {target}{suffix}"
        if sub_action in {"press", "hotkey"} and data.get("key"):
            return f"Press {data['key']}{suffix}"
        return str(description or f"Perform {sub_action} on the active desktop")
    return str(description or data.get("description") or action or "Complete the desktop goal")


async def execute_universal_goal(goal: str, *, runtime: Any = None,
                                surface: Optional[str] = None, task_context: Any = None) -> dict[str, Any]:
    """Execute one bounded goal through the universal runtime."""

    if runtime is None:
        from core.runtime.multimodal_runtime import MultimodalRuntimeAdapter

        runtime = MultimodalRuntimeAdapter()
    kwargs = {"surface": surface}
    if task_context is not None:
        kwargs["task_context"] = task_context
    return await runtime.execute_goal(goal, **kwargs)


__all__ = [
    "NON_SUCCESS_STATUSES", "UNIVERSAL_ACTION_TYPES", "execute_universal_goal",
    "execution_status", "goal_for_action", "result_is_success", "universal_mode",
]
