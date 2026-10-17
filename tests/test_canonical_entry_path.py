"""Regression tests for the real HTTP desktop-entry seam."""
import asyncio

from types import SimpleNamespace


def test_canonical_desktop_helper_uses_task_engine_and_preserves_truth(monkeypatch):
    import backend.routes.ai_routes as routes

    calls = []

    class Engine:
        async def execute_task(self, task):
            calls.append(task)
            task.steps[0].status = "UNVERIFIED"
            task.steps[0].result = {
                "status": "UNVERIFIED",
                "verification_status": "UNVERIFIED",
                "provider": "local",
                "model": "aegis-sovereign:latest",
                "verification": {"status": "UNVERIFIED"},
            }
            task.status = "UNVERIFIED"
            return task

    monkeypatch.setattr(routes, "get_task_engine", lambda: Engine())
    result = asyncio.run(routes._execute_canonical_desktop_goal(
        "Open an unfamiliar editor and verify its window", {
            "desktop_allowed": True,
            "allowed_roots": ["C:/workspace"],
            "max_steps": 4,
        }, SimpleNamespace(category="DESKTOP_ACTION", action_type="OPEN_APP")))

    assert calls and calls[0].steps[0].action_type == "UNIVERSAL_GUI_TASK"
    assert calls[0].steps[0].payload["goal"] == "Open an unfamiliar editor and verify its window"
    assert calls[0].context["allowed_roots"] == ["C:/workspace"]
    assert result["status"] == "UNVERIFIED"
    assert result["verified"] is False
    assert result["provider"] == "local"
    assert "Verified completion" not in result["response"]


def test_canonical_desktop_helper_does_not_turn_success_into_verified(monkeypatch):
    import backend.routes.ai_routes as routes

    class Engine:
        async def execute_task(self, task):
            task.status = "SUCCESS"
            task.steps[0].status = "SUCCESS"
            task.steps[0].result = {"status": "SUCCESS", "provider": "local"}
            return task

    monkeypatch.setattr(routes, "get_task_engine", lambda: Engine())
    result = asyncio.run(routes._execute_canonical_desktop_goal(
        "Open Calculator", {}, SimpleNamespace(category="DESKTOP_ACTION")))
    assert result["status"] == "SUCCESS"
    assert result["verified"] is False
    assert "not independently verified" in result["response"]
