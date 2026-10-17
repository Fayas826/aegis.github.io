"""Focused tests for the standalone canonical HTTP desktop seam."""
import asyncio
from types import SimpleNamespace


def test_route_seam_delegates_desktop_goal_and_preserves_unverified(monkeypatch):
    import backend.routes.universal_desktop_route as route

    calls = []

    class Engine:
        async def execute_task(self, task):
            calls.append(task)
            task.status = "UNVERIFIED"
            task.steps[0].status = "UNVERIFIED"
            task.steps[0].result = {
                "status": "UNVERIFIED",
                "provider": "local",
                "model": "aegis-sovereign:latest",
                "verification": {"status": "UNVERIFIED"},
            }
            return task

    monkeypatch.setattr(route, "time", SimpleNamespace(time=lambda: 100.0, monotonic=lambda: 10.0))
    monkeypatch.setattr(route, "_is_canonical_desktop", lambda classification: True)
    import core.orchestration.desktop_command_router as router
    monkeypatch.setattr(router.desktop_command_router, "classify", lambda *args: SimpleNamespace(
        category="DESKTOP_ACTION", action_type="OPEN_APP"))
    monkeypatch.setattr(router.desktop_command_router, "is_actionable", lambda classification: True)
    import core.orchestration.task_engine as engine_module
    monkeypatch.setattr(engine_module, "get_task_engine", lambda: Engine())
    import core.orchestration.computer_use_session as session_module
    monkeypatch.setattr(session_module.computer_use_session_manager, "get_session_context", lambda: {})

    result = asyncio.run(route.execute_if_canonical_desktop_goal(
        "Open an unfamiliar editor", {"desktop_allowed": True, "max_steps": 3}))
    assert calls[0].steps[0].action_type == "UNIVERSAL_GUI_TASK"
    assert calls[0].context["desktop_allowed"] is True
    assert result["status"] == "UNVERIFIED"
    assert result["verified"] is False


def test_route_seam_returns_none_for_non_desktop(monkeypatch):
    import backend.routes.universal_desktop_route as route
    import core.orchestration.desktop_command_router as router
    import core.orchestration.computer_use_session as session_module
    monkeypatch.setattr(router.desktop_command_router, "classify", lambda *args: SimpleNamespace(
        category="CHAT", action_type="CONVERSATIONAL"))
    monkeypatch.setattr(router.desktop_command_router, "is_actionable", lambda classification: False)
    monkeypatch.setattr(session_module.computer_use_session_manager, "get_session_context", lambda: {})
    assert asyncio.run(route.execute_if_canonical_desktop_goal("Explain this")) is None
