from core.execution.universal_desktop_layer import DesktopActionLayer
import pytest
from core.execution.universal_dispatch import (
    execute_universal_goal,
    goal_for_action,
    result_is_success,
    universal_mode,
)
from core.perception.universal_desktop_layer import (
    BLOCKED,
    SUCCESS,
    DesktopPerception,
    DesktopObservation,
    build_manifest,
    denormalize_coordinates,
    fuse_elements,
    normalize_coordinates,
)


@pytest.fixture(autouse=True)
def isolated_worker_receipts(monkeypatch, tmp_path):
    import core.orchestration.shared_task_junction as junction_module
    from core.storage.sqlite_store import AegisSQLiteStore
    from core.runtime.universal_gui_worker import UniversalGUIWorker
    monkeypatch.setattr(junction_module, "task_junction", junction_module.SharedTaskJunction(AegisSQLiteStore(str(tmp_path / "worker.db"))))
    monkeypatch.setattr(UniversalGUIWorker, "_neural_event", lambda *args, **kwargs: None)


def _capture():
    return {
        "width": 100,
        "height": 80,
        "image_b64": "fixture",
        "elements": [{"id": "button", "text": "Open", "bbox": [10, 10, 30, 30], "clickable": True}],
    }


def test_normalized_coordinates_round_trip_and_reject_out_of_range():
    normalized = normalize_coordinates(99, 79, 100, 80)
    assert normalized == (1.0, 1.0)
    assert denormalize_coordinates(*normalized, 100, 80) == (99, 79)
    try:
        denormalize_coordinates(1.01, 0.5, 100, 80)
    except ValueError:
        pass
    else:
        raise AssertionError("out-of-range normalized coordinate was accepted")


def test_uia_elements_are_fused_and_manifest_numbers_are_one_based():
    elements = fuse_elements(
        [{"id": "visual", "text": "", "bbox": [10, 10, 30, 30]}],
        [{"id": "uia", "text": "Open", "role": "Button", "bbox": [11, 11, 29, 29], "source": "uia"}],
    )
    manifest = build_manifest(elements, 100, 80, "fixture-manifest")
    assert manifest.manifest_id == "fixture-manifest"
    assert manifest.marks[0].number == 1
    assert manifest.marks[0].label == "Open"


def test_actions_capture_fresh_before_and_after_and_bound_invalid_click():
    perception = DesktopPerception(capture=_capture, uia_provider=lambda: [])
    calls = []

    class Backend:
        name = "fixture"

        def click(self, x, y):
            calls.append((x, y))

    layer = DesktopActionLayer(perception=perception, backend=Backend())
    result = layer.click_element(1)
    assert result.status == SUCCESS
    assert result.before and result.after
    assert calls == [(20, 20)]

    blocked = layer.click_normalized(1.2, 0.5)
    assert blocked.status == BLOCKED
    assert not blocked.success


def test_unavailable_capture_is_truthful():
    perception = DesktopPerception(capture=lambda: (_ for _ in ()).throw(RuntimeError("no display")))
    result = DesktopActionLayer(perception=perception, backend=object()).scroll(1)
    assert result.status == "UNAVAILABLE"
    assert result.before and "no display" in (result.before.error or "")


def test_universal_dispatch_defaults_and_supports_legacy_mode():
    assert universal_mode({}) == "universal"
    assert universal_mode({"execution_mode": "legacy"}) == "legacy"
    assert goal_for_action("APP_OPEN", {"app_name": "Notepad"}) == "Open Notepad"


def test_nested_failure_statuses_cannot_be_promoted_to_success():
    assert result_is_success({"status": "SUCCESS"}) is True
    assert result_is_success({"status": "VERIFIED", "verification_status": "VERIFIED"}) is True
    for status in ("FAILED", "BLOCKED", "UNAVAILABLE", "UNVERIFIED", "VERIFICATION_FAILED"):
        assert result_is_success({"status": "SUCCESS", "verification_status": status}) is False
        assert result_is_success({"status": status}) is False
    assert result_is_success({"status": "SUCCESS", "verification_status": "UNKNOWN"}) is False


def test_universal_dispatch_uses_injected_runtime():
    class Runtime:
        async def execute_goal(self, goal, surface=None):
            return {"status": "VERIFIED", "detail": goal, "surface": surface}

    async def run():
        return await execute_universal_goal("Open Notepad", runtime=Runtime(), surface="desktop")

    import asyncio
    result = asyncio.run(run())
    assert result["status"] == "VERIFIED"
    assert result["detail"] == "Open Notepad"
    assert result["surface"] == "desktop"


def test_task_engine_defaults_to_universal_and_preserves_legacy_opt_out(monkeypatch):
    from core.orchestration.task_engine import Task, TaskEngine, TaskStep
    import core.orchestration.task_engine as task_engine_module

    calls = []

    async def universal(goal, *, surface=None, task_context=None, runtime=None):
        assert task_context.parameters["original_goal"] == "Open the editor and prepare it"
        assert task_context.goal == "Open Notepad"
        calls.append(("universal", goal, surface))
        return {"status": "VERIFIED", "goal": goal}

    async def legacy(action, payload):
        calls.append(("legacy", action, payload))
        return {"status": "SUCCESS"}

    monkeypatch.setattr(task_engine_module, "execute_universal_goal", universal)
    engine = TaskEngine.__new__(TaskEngine)
    engine.controller = legacy

    import asyncio
    task = Task(original_intent="Open the editor and prepare it")
    asyncio.run(engine._dispatch_step(task, TaskStep(action_type="APP_OPEN", payload={"app_name": "Notepad"})))
    asyncio.run(engine._dispatch_step(task, TaskStep(action_type="APP_OPEN", payload={"app_name": "Notepad", "execution_mode": "legacy"})))
    assert calls[0] == ("universal", "Open Notepad", None)
    assert calls[1][0] == "legacy"


def test_chatgpt_provider_uses_bounded_actions_and_fresh_observations():
    from core.runtime.universal_gui_worker import UniversalGUIWorker

    class Perception:
        def observe(self):
            return DesktopObservation(SUCCESS, width=100, height=80, image_b64="", active_window="Editor")

    class ActionResult:
        status = SUCCESS
        detail = "clicked"
        error = None

        def to_dict(self):
            return {"status": self.status, "detail": self.detail}

    class Actions:
        def click_normalized(self, x, y):
            self.last = (x, y)
            return ActionResult()

    responses = iter([
        {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "click_normalized", "arguments": '{"x": 500, "y": 500}'}}]}}]},
        {"choices": [{"message": {"role": "assistant", "content": "I cannot independently verify the requested goal."}}]},
    ])
    worker = UniversalGUIWorker(
        perception=Perception(), actions=Actions(), provider="chatgpt",
        openai_request=lambda payload: next(responses), max_steps=2,
    )
    result = worker.run_task("click the editor")
    assert result["status"] == "UNVERIFIED"
    assert result["verification_status"] == "UNVERIFIED"
    assert len(result["actions"]) == 1
    assert len(result["observations"]) == 2


def test_canonical_tool_registry_exposes_universal_goal():
    from core.tool_registry.registry import tool_registry

    assert tool_registry.has_tool("desktop.universal_goal")


def test_universal_worker_honors_cancelled_task_context_and_junction_evidence():
    from core.orchestration.agent_contract import TaskContext
    from core.runtime.universal_gui_worker import UniversalGUIWorker

    class Perception:
        def observe(self):
            return DesktopObservation(SUCCESS, width=20, height=20, active_window="Editor")

    class Junction:
        def __init__(self):
            self.completed = None

        def start(self, context, agent, parameters):
            return {"task_id": context.task_id}

        def get(self, task_id):
            return {"status": "RUNNING"}

        def record_event(self, *args, **kwargs):
            return {}

        def complete(self, task_id, status, result, evidence):
            self.completed = (status, evidence)

    junction = Junction()
    context = TaskContext(goal="type text", cancelled=True)
    worker = UniversalGUIWorker(
        perception=Perception(), provider="chatgpt", openai_request=lambda payload: {},
        task_junction=junction,
    )
    result = worker.run_task("type text", task_context=context)
    assert result["status"] == "CANCELLED"
    assert junction.completed[0] == "CANCELLED"
    assert "observations" in junction.completed[1]
