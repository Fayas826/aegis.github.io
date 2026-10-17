"""Offline safety and evidence regressions; these are NOT live GUI acceptance."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from core.execution.universal_desktop_layer import DesktopActionLayer
from core.execution.universal_dispatch import execution_status, result_is_success
from core.orchestration.agent_contract import TaskContext
from core.orchestration.shared_task_junction import SharedTaskJunction
from core.orchestration.verification_planner import VerificationPlanner
from core.perception.universal_desktop_layer import DesktopObservation
from core.runtime.multimodal_runtime import MultimodalRuntimeAdapter
from core.runtime.universal_gui_worker import UniversalGUIWorker
from core.storage.sqlite_store import AegisSQLiteStore


@pytest.fixture
def junction(tmp_path, monkeypatch):
    monkeypatch.setattr(UniversalGUIWorker, "_neural_event", lambda *a, **k: None)
    return SharedTaskJunction(AegisSQLiteStore(str(tmp_path / "runtime.db")))


def observation(window="Unfamiliar Editor", **kwargs):
    return DesktopObservation("SUCCESS", width=120, height=90, active_window=window, **kwargs)


def tool(name, **args):
    return {"choices": [{"message": {"tool_calls": [{"function": {
        "name": name, "arguments": json.dumps(args)}}]}}]}


def worker(junction, replies, actions=None, capture=None):
    replies = iter(replies)
    return UniversalGUIWorker(provider="openai", task_junction=junction,
        openai_request=lambda payload: next(replies), actions=actions,
        perception=SimpleNamespace(observe=capture or observation))


@pytest.mark.parametrize("status", ["CANCELLED", "TIMEOUT", "PENDING_CONFIRMATION", "BLOCKED", "FAILED"])
def test_negative_nested_status_survives_success_wrapper(status):
    value = {"status": "SUCCESS", "verification_status": "UNVERIFIED", "data": {"status": status}}
    assert execution_status(value) == status
    assert not result_is_success(value)
    assert execution_status({"status": "VERIFIED", "actions": [{"status": "FAILED"}]}) == "VERIFIED"


def test_adapter_never_replays_a_typeerror():
    calls = []
    def run(goal):
        calls.append(goal)
        raise TypeError("backend failed after input")
    result = asyncio.run(MultimodalRuntimeAdapter(desktop_worker=run).execute_goal("Click the editor"))
    assert result["status"] == "FAILED"
    assert len(calls) == 1


def test_adapter_forwards_literal_goal_and_owned_context():
    goal = 'Enter "first\nsecond" in the editor'
    context = TaskContext(goal=goal, parameters={"desktop_allowed": False})
    def run(value, task_context):
        assert value == goal and task_context is context
        return {"status": "PENDING_CONFIRMATION", "verification_status": "UNVERIFIED"}
    result = asyncio.run(MultimodalRuntimeAdapter(desktop_worker=run).execute_goal(goal, task_context=context))
    assert result["status"] == "PENDING_CONFIRMATION"


@pytest.mark.parametrize("cancelled,deadline,expected", [(True, None, "CANCELLED"), (False, 1, "TIMEOUT")])
def test_stop_before_perception_or_provider(junction, cancelled, deadline, expected):
    def forbidden():
        pytest.fail("No capture permitted after cancellation/deadline")
    result = worker(junction, [], capture=forbidden).run_task("Create a note", task_context=TaskContext(
        goal="Create a note", cancelled=cancelled, deadline=deadline))
    assert result["status"] == expected
    assert junction.get(result["task_id"])["status"] == expected


def test_cancel_while_provider_is_responding_sends_no_action(junction):
    context = TaskContext(goal="Click the editor")
    def respond(payload):
        context.cancelled = True
        return tool("click_normalized", x=100, y=200)
    runtime = UniversalGUIWorker(provider="openai", task_junction=junction,
        perception=SimpleNamespace(observe=observation), openai_request=respond,
        actions=SimpleNamespace(click_normalized=lambda *a: pytest.fail("Late input")))
    result = runtime.run_task(context.goal, task_context=context)
    assert result["status"] == "CANCELLED" and not result["actions"]


def test_confirmation_does_not_dispatch_and_is_durable(junction):
    runtime = worker(junction, [tool("hotkey", keys=["delete"])], actions=object())
    result = runtime.run_task("Delete the selected draft")
    assert result["status"] == "PENDING_CONFIRMATION"
    assert junction.get(result["task_id"])["status"] == "PENDING_CONFIRMATION"


def test_model_cannot_author_its_verification_contract(junction):
    result = worker(junction, [tool("verify_goal", predicates=[{"kind": "window", "value": "Unfamiliar Editor"}])]).run_task("Create a note")
    assert result["status"] in {"BLOCKED", "FAILED"}
    assert not result_is_success(result)


def test_ineffective_action_budget_is_bounded_and_feedback_is_sent(junction):
    requests, inputs = [], []
    def respond(payload):
        requests.append(payload)
        return tool("click_normalized", x=400, y=300)
    actions = SimpleNamespace(click_normalized=lambda *a: (inputs.append(a) or
        SimpleNamespace(to_dict=lambda: {"status": "SUCCESS"})))
    runtime = worker(junction, [], actions)
    runtime.openai_request = respond
    result = runtime.run_task("Select the unfamiliar control")
    assert result["status"] == "FAILED"
    assert len(inputs) == 2 and len(requests) == 3
    assert "do not replay input blindly" in json.dumps(requests[1])
    assert any(event["type"] == "executed_action" for event in junction.get(result["task_id"])["events"])


def test_planner_image_mime_matches_capture(junction):
    requests = []
    runtime = worker(junction, [], capture=lambda: observation(image_b64="fixture", image_mime="image/png"))
    runtime.openai_request = lambda payload: requests.append(payload) or {"choices": [{"message": {"content": "done"}}]}
    assert runtime.run_task("Create a note")["status"] == "UNVERIFIED"
    assert requests[0]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;")


def test_stale_coordinates_block_before_backend_input():
    current = observation(window_id=12, image_b64="new pixels", origin=(-120, 0))
    calls = []
    layer = DesktopActionLayer(SimpleNamespace(observe=lambda: current),
        backend=SimpleNamespace(click=lambda *args: calls.append(args)))
    layer.bind_observation(observation(window_id=12, image_b64="old pixels", origin=(-120, 0)))
    assert layer.click_normalized(0.5, 0.5).status == "BLOCKED"
    assert calls == []


def test_negative_monitor_origin_is_applied_exactly_once():
    current = observation(window_id=12, image_b64="pixels", origin=(-120, -90))
    calls = []
    layer = DesktopActionLayer(SimpleNamespace(observe=lambda: current),
        backend=SimpleNamespace(click=lambda *args: calls.append(args)))
    layer.bind_observation(current)
    assert layer.click_normalized(0, 0).status == "SUCCESS"
    assert calls == [(-120, -90)]


def test_task_engine_does_not_retry_an_unverified_desktop_mission():
    from core.orchestration.task_engine import Task, TaskEngine, TaskStep
    calls = []
    class Runtime:
        async def execute_goal(self, goal, **kwargs):
            calls.append((goal, kwargs["task_context"]))
            return {"status": "UNVERIFIED", "detail": "Action ran; goal not established"}
    engine = TaskEngine.__new__(TaskEngine)
    engine.universal_runtime = Runtime()
    step = TaskStep(action_type="UNIVERSAL_GUI_TASK", payload={"goal": "Create a note", "desktop_allowed": False})
    task = Task(original_intent="Create and save a note", steps=[step])
    assert asyncio.run(engine._execute_step(task, step)) is False
    assert step.status == "UNVERIFIED" and step.retry_count == 0
    assert len(calls) == 1
    assert calls[0][1].parameters["original_goal"] == task.original_intent
    assert calls[0][1].parameters["desktop_allowed"] is False


def test_preexisting_file_is_not_evidence_of_a_save(tmp_path):
    path = tmp_path / "note.txt"
    path.write_text("exact text", encoding="utf-8")
    contract = {"predicates": [{"kind": "file_exact", "path": str(path), "value": "exact text"}]}
    baseline = VerificationPlanner.desktop_baseline(contract, [str(tmp_path)])
    result = VerificationPlanner.verify_desktop("Create and save a note", [observation().to_dict()],
        contract, [str(tmp_path)], baseline=baseline)
    assert result["status"] == "UNVERIFIED"
    assert result["checks"][0]["changed_since_start"] is False


def test_save_reopen_readback_requires_real_disappearance_not_focus_switch(tmp_path):
    path = tmp_path / "note.txt"
    contract = {"predicates": [{"kind": "file_exact", "path": str(path), "value": "one\ntwo", "require_reopen": True}]}
    baseline = VerificationPlanner.desktop_baseline(contract, [str(tmp_path)])
    path.write_bytes(b"one\ntwo")
    doc = observation("note.txt - Unfamiliar Editor", windows=[{"title": "note.txt - Unfamiliar Editor"}]).to_dict()
    switched = observation("Calculator", windows=[{"title": "note.txt - Unfamiliar Editor"}, {"title": "Calculator"}]).to_dict()
    result = VerificationPlanner.verify_desktop("Save close reopen and verify", [doc, switched, doc],
        contract, [str(tmp_path)], baseline=baseline)
    assert result["status"] == "UNVERIFIED"
    closed = observation("Calculator", windows=[{"title": "Calculator"}]).to_dict()
    result = VerificationPlanner.verify_desktop("Save close reopen and verify", [doc, closed, doc],
        contract, [str(tmp_path)], baseline=baseline)
    assert result["status"] == "VERIFIED"
    assert result["checks"][0]["reopen_observed"]
    assert result["checks"][0]["method"] == "independent_file_readback"


def test_file_verification_outside_roots_is_denied(tmp_path):
    contract = {"predicates": [{"kind": "file_exact", "path": str(tmp_path / "private.txt"), "value": "text"}]}
    assert VerificationPlanner.desktop_baseline(contract, []) == {}
    result = VerificationPlanner.verify_desktop("Read file", [observation().to_dict()], contract, [])
    assert result["status"] == "UNVERIFIED"
    assert "outside authorized roots" in result["checks"][0]["error"]


def test_provider_failure_is_not_fallback_or_secret_leak(junction):
    def reject(payload):
        raise RuntimeError("simulated-private-token")
    runtime = worker(junction, [])
    runtime.openai_request = reject
    result = runtime.run_task("Create a note")
    assert result["status"] == "FAILED"
    assert "simulated-private-token" not in json.dumps(result)
    assert not result["actions"]


def test_planner_payload_cannot_escalate_host_authority():
    from core.orchestration.task_engine import Task, TaskEngine, TaskStep
    captured = []
    class Runtime:
        async def execute_goal(self, goal, **kwargs):
            captured.append(kwargs["task_context"])
            return {"status": "BLOCKED"}
    engine = TaskEngine.__new__(TaskEngine)
    engine.universal_runtime = Runtime()
    task = Task(context={"desktop_allowed": False, "allowed_roots": [], "deadline": 10, "max_steps": 2})
    step = TaskStep(action_type="UNIVERSAL_GUI_TASK", payload={"goal": "Do a task", "desktop_allowed": True,
        "allowed_roots": ["/"], "verification": {"predicates": [{"kind": "window", "value": "Editor"}]},
        "deadline": 100, "max_steps": 40})
    asyncio.run(engine._dispatch_step(task, step))
    assert captured[0].parameters["desktop_allowed"] is False
    assert captured[0].parameters["allowed_roots"] == []
    assert "verification" not in captured[0].parameters
    assert captured[0].deadline == 10 and captured[0].max_steps == 2


def test_explicit_unconfigured_provider_does_not_native_launch(junction, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime = UniversalGUIWorker(provider="openai", task_junction=junction,
        perception=SimpleNamespace(observe=lambda: pytest.fail("Unexpected native fallback")))
    assert runtime.run_task("Open Unfamiliar App")["status"] == "UNAVAILABLE"


@pytest.mark.parametrize("status", ["PENDING_CONFIRMATION", "CANCELLED", "FAILED"])
def test_task_terminal_status_does_not_trigger_legacy_healing_or_rollback(status, monkeypatch):
    import sys
    import core.orchestration.task_engine as module
    from core.orchestration.task_engine import Task, TaskEngine, TaskStep
    def forbidden(*args, **kwargs):
        pytest.fail("No legacy healing/rollback after a terminal desktop attempt")
    monkeypatch.setattr(module.reliability_engine, "analyze_failure", forbidden)
    monkeypatch.setitem(sys.modules, "core.cognition.memory.synaptic_memory",
        SimpleNamespace(synaptic_memory=SimpleNamespace(record_episode=lambda *a: None)))
    class Runtime:
        async def execute_goal(self, goal, **kwargs):
            return {"status": status}
    engine = TaskEngine.__new__(TaskEngine)
    engine.active_tasks, engine.universal_runtime = {}, Runtime()
    engine._archive_task = lambda task: None
    engine._rollback_task = forbidden
    task = Task(steps=[TaskStep(action_type="UNIVERSAL_GUI_TASK", payload={"goal": "Create a note"})])
    assert asyncio.run(engine.execute_task(task)).status == status
