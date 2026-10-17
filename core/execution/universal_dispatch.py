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
    "UNIVERSAL_GUI_TASK",
    "AGENTIC_WEB_TASK",
    "MODEL_DRIVEN_WEB",
    "BROWSER_NAVIGATE_OR_PLAY",
    "WEB_EXECUTION",
})

NON_SUCCESS_STATUSES = frozenset({
    "FAILED", "BLOCKED", "UNAVAILABLE", "UNVERIFIED", "VERIFICATION_FAILED",
    "CANCELLED", "TIMEOUT", "ERROR", "SAFETY_BLOCK", "SAFETY_BLOCKED",
})


def universal_mode(payload: Optional[Mapping[str, Any]]) -> str:
    """Return the explicit mode, defaulting safely to universal."""

    value = str((payload or {}).get("execution_mode", "universal") or "universal").strip().lower()
    return value if value in {"universal", "legacy"} else "universal"


def result_is_success(result: Any) -> bool:
    """Accept only a real success, never a failed nested verification."""

    if not isinstance(result, Mapping):
        return False
    status = str(result.get("status", "")).strip().upper()
    verification = str(result.get("verification_status", "")).strip().upper()
    if status not in {"SUCCESS", "VERIFIED"}:
        return False
    if verification and verification not in {"SUCCESS", "VERIFIED", "VERIFIED_VALID"}:
        return False
    return True


def goal_for_action(action_type: str, payload: Optional[Mapping[str, Any]] = None,
                    description: str = "") -> str:
    """Build a concrete natural-language goal for a legacy task node."""

    data = payload or {}
    explicit = data.get("goal") or data.get("directive")
    if isinstance(explicit, str) and explicit.strip():
        return " ".join(explicit.split())

    action = str(action_type or "").upper()
    if action == "APP_OPEN":
        target = data.get("app_name") or data.get("application") or data.get("target")
        return f"Open {target}" if target else str(description or "Open the requested application")
    if action in {"UI_AUTOMATION", "DESKTOP_CONTROL"}:
        sub_action = str(data.get("action", "interact")).strip().lower()
        target = data.get("target") or data.get("element") or data.get("text")
        window = data.get("window")
        suffix = f" in {window}" if window else ""
        if target:
            return f"{sub_action.title()} {target}{suffix}"
        return str(description or f"Perform {sub_action} on the active desktop")
    return str(description or data.get("description") or action or "Complete the desktop goal")


async def execute_universal_goal(goal: str, *, runtime: Any = None,
                                surface: Optional[str] = None) -> dict[str, Any]:
    """Execute one bounded goal through the universal runtime."""

    if runtime is None:
        from core.runtime.multimodal_runtime import MultimodalRuntimeAdapter

        runtime = MultimodalRuntimeAdapter()
    return await runtime.execute_goal(goal, surface=surface)


__all__ = [
    "NON_SUCCESS_STATUSES", "UNIVERSAL_ACTION_TYPES", "execute_universal_goal",
    "goal_for_action", "result_is_success", "universal_mode",
]
