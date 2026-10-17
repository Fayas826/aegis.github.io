import time
"""Local planner integration with fake inference and input, never live acceptance."""
import asyncio
import json
import threading
import time

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


def test_windows_shell_file_explorer_is_discovered_as_structured_executable(monkeypatch):
    import core.execution.universal_desktop_layer as module
    monkeypatch.setattr(module.sys, "platform", "win32")
    monkeypatch.setattr(module.os, "environ", {"SystemRoot": "C:\\Windows"})
    monkeypatch.setattr(module, "_safe_executable", lambda path: path.casefold().endswith("explorer.exe"))
    monkeypatch.setattr(module, "_shortcut_details", lambda path: None)
    monkeypatch.setattr(module.os, "walk", lambda root, followlinks=False: iter(()))
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""))
    items, _ = module._installed_applications()
    assert any(item["name"] == "File Explorer" and item["kind"] == "windows_shell" and item["launchable"]
               for item in items)


def test_same_windows_app_family_prefers_packaged_identity(monkeypatch):
    layer = DesktopActionLayer()
    monkeypatch.setattr(layer, "discover_applications", lambda query: {"items": [
        {"name": "Notepad", "path": "C:/WindowsApps/Notepad.exe", "kind": "executable", "launchable": True},
        {"name": "Notepad", "aumid": "Microsoft.Notepad_123!App", "kind": "packaged", "launchable": True},
        {"name": "notepad", "path": "C:/Windows/system32/notepad.exe", "kind": "executable", "launchable": True},
    ]})
    selected = layer._resolve_application("Notepad")
    assert selected["kind"] == "packaged" and selected["aumid"] == "Microsoft.Notepad_123!App"


def test_explorer_alias_can_only_resolve_to_trusted_windows_shell(monkeypatch):
    layer = DesktopActionLayer()
    shell = {"name": "File Explorer", "path": "C:/Windows/explorer.exe", "kind": "windows_shell", "launchable": True}
    monkeypatch.setattr(layer, "discover_applications", lambda query: {"items": [shell]})
    assert layer._resolve_application("explorer.exe")["kind"] == "windows_shell"


def test_file_explorer_launch_verification_accepts_windows_shell_process():
    from core.orchestration.verification_planner import VerificationPlanner
    now = time.time()
    result = VerificationPlanner.verify_desktop("Open File Explorer", [{
        "status": "SUCCESS", "timestamp": now, "active_window": "Program Manager",
        "active_process": "explorer.exe", "window_id": 1,
    }])
    assert result["status"] == "VERIFIED"


def test_goal_preflight_seeds_real_inventory_without_desktop_input(junction):
    screen = Screen()
    local = Local([{"done": True}])
    worker = make_worker(junction, local, screen)
    result = worker.run_task("Open NovelEditor")
    assert result["status"] == "UNVERIFIED"
    assert local.requests and local.requests[0][0]["progress"][0]["action"] == "discover_applications"
    assert screen.inputs == []


def test_hung_native_uia_read_is_bounded_and_never_reused(monkeypatch):
    import core.perception.universal_desktop_layer as module
    with module._UIA_SCAN_LOCK:
        module._UIA_SCAN_PENDING = None
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
        assert not elements and any(token in error for token in ("time budget", "failed", "pending"))
        elements, error = module._bounded_default_uia(timeout=0.01)
        assert not elements and any(token in error for token in ("time budget", "failed", "pending"))
        assert len(calls) <= 1
    finally:
        release.set()
        if calls:
            assert finished.wait(2)


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
