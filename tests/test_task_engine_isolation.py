"""Regression tests for the universal/legacy task-engine seam."""

from __future__ import annotations

import asyncio

import core.orchestration.task_engine as task_engine_module
from core.orchestration.task_engine import Task, TaskEngine, TaskStep


def test_lazy_factory_returns_engine_with_explicit_legacy_controller():
    original = task_engine_module.task_engine
    task_engine_module.task_engine = None
    try:
        engine = task_engine_module.get_task_engine()
        assert isinstance(engine, TaskEngine)
        assert engine.controller.__module__ == "core.execution.legacy_action_controller"
    finally:
        task_engine_module.task_engine = original


def test_universal_step_never_calls_legacy_controller(monkeypatch):
    legacy_calls = []

    async def legacy_controller(action, payload):
        legacy_calls.append((action, payload))
        return {"status": "SUCCESS"}

    universal_calls = []

    async def universal_dispatch(goal, **kwargs):
        universal_calls.append((goal, kwargs))
        return {"status": "VERIFIED", "verification_status": "VERIFIED"}

    monkeypatch.setattr(task_engine_module, "execute_universal_goal", universal_dispatch)
    engine = TaskEngine(legacy_controller)
    task = Task(original_intent="open calculator", context={"max_steps": 2})
    step = TaskStep(action_type="UNIVERSAL_GUI_TASK", description="Open Calculator", payload={"goal": "Open Calculator"})

    result = asyncio.run(engine._dispatch_step(task, step))

    assert result["status"] == "VERIFIED"
    assert universal_calls and universal_calls[0][0] == "Open Calculator"
    assert legacy_calls == []


def test_legacy_controller_requires_explicit_execution_mode():
    calls = []

    async def legacy_controller(action, payload):
        calls.append((action, payload))
        return {"status": "SUCCESS"}

    engine = TaskEngine(legacy_controller)
    task = Task(original_intent="legacy action")
    step = TaskStep(action_type="APP_OPEN", payload={"execution_mode": "legacy", "app_name": "notepad"})

    result = asyncio.run(engine._dispatch_step(task, step))

    assert result["status"] == "SUCCESS"
    assert calls == [("APP_OPEN", {"execution_mode": "legacy", "app_name": "notepad"})]
