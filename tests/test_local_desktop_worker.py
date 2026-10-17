"""Local planner integration with fake inference and input, never live acceptance."""
import asyncio
import json
import threading

import pytest

from core.execution.universal_desktop_layer import DesktopActionLayer
from core.orchestration.agent_contract import TaskContext
from core.orchestration.shared_task_junction import SharedTaskJunction
from core.perception.universal_desktop_layer import DesktopElement, DesktopObservation
from core.runtime.multimodal_runtime import MultimodalRuntimeAdapter
from core.runtime.universal_gui_worker import UniversalGUIWorker
from core.storage.sqlite_store import AegisSQLiteStore


class Local:
    def __init__(self, plans, *, vision=True, available=True):
        self.plans = iter(plans)
        self.vision, self.available = vision, available
        self.requests = []

    def probe_desktop(self, **kwargs):
        return {"status": "AVAILABLE" if self.available else "UNAVAILABLE", "model": "local-fixture:latest", "supports_vision": self.vision}

    def plan_desktop(self, prompt, system, **kwargs):
        self.requests.append((json.loads(prompt), system, kwargs))
        return {"status": "SUCCESS", "model": "local-fixture:latest",
                "perception_mode": "vision" if self.vision and kwargs.get("image_b64") else "accessibility",
                "response": next(self.plans)}


class Screen:
    def __init__(self, *, image=True, native=True):
        self.label, self.image, self.native = "Ready", image, native
        self.inputs = []

    def observe(self):
        controls = (DesktopElement("button", self.label, "Button", (10, 10, 30, 30), source="uia", clickable=True),) if self.native else ()
        return DesktopObservation("SUCCESS", 120, 90, "fixture" if self.image else "",
            active_window="Unfamiliar Editor", elements=controls, window_id=12)

    def click(self, *args):
        self.inputs.append(args)
        self.label = "Selected"


@pytest.fixture
def junction(tmp_path, monkeypatch):
    monkeypatch.setattr(UniversalGUIWorker, "_neural_event", lambda *a, **k: None)
    return SharedTaskJunction(AegisSQLiteStore(str(tmp_path / "local.db")))


def make_worker(junction, local, screen, **kwargs):
    return UniversalGUIWorker(provider="local", local_provider=local, perception=screen,
        actions=DesktopActionLayer(screen, backend=screen), task_junction=junction, **kwargs)


@pytest.mark.parametrize("vision", [True, False])
def test_task_engine_uses_local_model_then_independent_verification(junction, vision):
    from core.orchestration.task_engine import Task, TaskStep, TaskEngine
    screen = Screen(image=vision)
    local = Local([{"action": {"name": "click_element", "args": {"element_id": "button"}}},
                   {"action": {"name": "hotkey", "args": {"keys": ["delete"]}}}], vision=vision)
    worker = make_worker(junction, local, screen)
    engine = TaskEngine.__new__(TaskEngine)
    engine.universal_runtime = MultimodalRuntimeAdapter(desktop_worker=worker)
    task = Task(original_intent="Select the control in the unfamiliar application", context={
        "verification": {"predicates": [{"kind": "control_text", "value": "Selected"}]}})
    step = TaskStep(action_type="UNIVERSAL_GUI_TASK", payload={"goal": task.original_intent, "surface": "desktop"})
    assert asyncio.run(engine._execute_step(task, step))
    assert step.result["model"] == "local-fixture:latest" and step.result["provider"] == "local"
    assert step.result["status"] == "VERIFIED"
    assert screen.inputs == [(20, 20)]
    assert len(local.requests) == 1  # Stop at the independent postcondition, before any extra proposal.
    assert local.requests[0][0]["original_goal"] == task.original_intent
    receipt = junction.get(step.step_id)
    assert receipt["status"] == "VERIFIED"
    assert any(event["type"] == "local_planner_result" for event in receipt["events"])


def test_auto_local_first_does_not_fall_through_to_cloud(junction, monkeypatch):
    monkeypatch.setenv("AEGIS_LOCAL_FIRST", "true")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-not-a-credential")
    screen = Screen()
    local = Local([], available=False)
    worker = make_worker(junction, local, screen)
    worker.provider = "auto"
    worker._get_client = lambda: pytest.fail("Cloud fallback was not authorized")
    assert worker.run_task("Create and save a note")["status"] == "UNAVAILABLE"
    assert not local.requests and not screen.inputs


def test_default_auto_uses_installed_local_without_keys(junction, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    screen = Screen()
    local = Local([{"done": True}])
    worker = make_worker(junction, local, screen)
    worker.provider = "auto"
    result = worker.run_task("Create a note")
    assert result["status"] == "UNVERIFIED" and result["provider"] == "local"
    assert len(local.requests) == 1


def test_auto_prefers_available_local_even_when_cloud_is_permitted(junction, monkeypatch):
    monkeypatch.setenv("AEGIS_LOCAL_FIRST", "false")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-not-a-credential")
    screen = Screen()
    local = Local([{"done": True}])
    worker = make_worker(junction, local, screen)
    worker.provider = "auto"
    worker._get_client = lambda: pytest.fail("Installed local model should be selected first")
    assert worker.run_task("Open Unfamiliar Editor")["provider"] == "local"


def test_text_model_without_uia_sends_no_ungrounded_action(junction):
    screen = Screen(image=False, native=False)
    local = Local([{"done": True}], vision=False)
    result = make_worker(junction, local, screen).run_task("Click the control")
    assert result["status"] == "UNAVAILABLE"
    assert not local.requests and not screen.inputs


def test_text_planner_cannot_send_visual_coordinates(junction):
    screen = Screen(image=False)
    local = Local([{"action": {"name": "click_normalized", "args": {"x": 100, "y": 100}}}, {"done": True}], vision=False)
    result = make_worker(junction, local, screen).run_task("Select the control")
    assert result["status"] == "UNVERIFIED" and not screen.inputs
    assert result["actions"][0]["result"]["status"] == "BLOCKED"


def test_window_inventory_only_allows_window_planning_not_control_input(junction):
    screen = Screen(image=False, native=False)
    screen.observe = lambda: DesktopObservation("SUCCESS", 120, 90, active_window="Unfamiliar Editor",
        window_id=12, windows=({"window_id": 12, "title": "Unfamiliar Editor"},))
    local = Local([{"action": {"name": "hotkey", "args": {"keys": ["enter"]}}}, {"done": True}], vision=False)
    result = make_worker(junction, local, screen).run_task("Press the control")
    assert result["status"] == "UNVERIFIED" and not screen.inputs
    assert result["actions"][0]["result"]["status"] == "BLOCKED"
    assert "hotkey(" not in local.requests[0][1]


@pytest.mark.parametrize("plan", [{"python": "malicious()"}, {"action": [{"name": "hotkey"}]},
    {"done": "true"}, {"done": True, "action": {"name": "hotkey", "args": {}}}])
def test_local_prose_code_batches_and_ambiguous_plans_never_execute(junction, plan):
    screen = Screen()
    result = make_worker(junction, Local([plan]), screen).run_task("Create a note")
    assert result["status"] == "FAILED" and not screen.inputs


def test_local_inference_failure_preserves_history_without_fallback(junction):
    screen = Screen()
    local = Local([])
    local.plan_desktop = lambda *a, **k: {"status": "FAILED", "detail": "private data must not propagate"}
    result = make_worker(junction, local, screen).run_task("Create a note")
    assert result["status"] == "FAILED" and not screen.inputs
    assert "private data" not in json.dumps(result)


def test_packaged_application_exe_alias_uses_general_inventory(monkeypatch):
    layer = DesktopActionLayer()
    item = {"name": "NovelEditor", "kind": "packaged", "aumid": "Vendor.NovelEditor_123!App", "launchable": True}
    queries = []
    monkeypatch.setattr(layer, "discover_applications", lambda query: queries.append(query) or {"items": [item]})
    assert layer._resolve_application("NovelEditor.exe") == item
    assert queries == ["NovelEditor"]


def test_hung_native_uia_read_is_bounded_and_never_reused(monkeypatch):
    import core.perception.universal_desktop_layer as module
    release, finished = threading.Event(), threading.Event()
    calls = []
    def blocked():
        calls.append(True)
        release.wait(1)
        finished.set()
        return ((), None)
    monkeypatch.setattr(module, "_default_uia", blocked)
    try:
        elements, error = module._bounded_default_uia(timeout=0.01)
        assert not elements and "time budget" in error
        elements, error = module._bounded_default_uia(timeout=0.01)
        assert not elements and "pending" in error
        assert len(calls) == 1
    finally:
        release.set()
        assert finished.wait(1)


def test_inventory_launch_tolerates_pixels_but_not_foreground_identity_changes():
    from types import SimpleNamespace
    snapshots, launches = [], []
    def observe():
        snapshots.append(True)
        return DesktopObservation("SUCCESS", 120, 90, image_b64=str(len(snapshots)),
            window_id=12, active_window="NovelEditor", active_process="NovelEditor.exe")
    layer = DesktopActionLayer(SimpleNamespace(observe=observe), launcher=lambda *args: launches.append(args))
    layer.bind_observation(observe())
    assert layer.launch("NovelEditor.exe", timeout=0).status == "SUCCESS"
    assert len(launches) == 1
    old = observe()
    layer.bind_observation(old)
    layer.observer = lambda: DesktopObservation("SUCCESS", 120, 90,
        window_id=99, active_window="Other App", active_process="other.exe")
    assert layer.launch("NovelEditor.exe", timeout=0).status == "BLOCKED"
    assert len(launches) == 1
