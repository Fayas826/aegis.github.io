"""Offline tests for model-driven universal desktop dispatch."""

from __future__ import annotations

from types import SimpleNamespace

from core.perception.universal_desktop_layer import DesktopObservation, SUCCESS
from core.runtime.universal_gui_worker import UniversalGUIWorker, _verified_open_goal


class FakePerception:
    def __init__(self, window="Notepad"):
        self.window = window

    def observe(self):
        return DesktopObservation(SUCCESS, width=100, height=100, active_window=self.window, active_process="notepad.exe")


class FakeModels:
    def __init__(self, responses):
        self.responses = iter(responses)

    def generate_content(self, **_kwargs):
        return next(self.responses)


class FakeClient:
    def __init__(self, responses):
        self.models = FakeModels(responses)


def response_without_tool_call():
    return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[SimpleNamespace(text="done", function_call=None)]))])


def test_open_goal_verification_uses_observed_window():
    observation = FakePerception().observe()
    assert _verified_open_goal("open Notepad", observation) is True
    assert _verified_open_goal("open Calculator", observation) is False


def test_worker_reports_unavailable_without_gemini_key():
    worker = UniversalGUIWorker(perception=FakePerception(), api_key_env="MISSING_AEGIS_KEY")
    result = worker.run_task("open Notepad")
    assert result["status"] == "UNAVAILABLE"


def test_worker_verifies_observable_open_goal_with_injected_client():
    worker = UniversalGUIWorker(
        perception=FakePerception(),
        client=FakeClient([response_without_tool_call()]),
        model="test-model",
    )
    result = worker.run_task("open Notepad")
    assert result["status"] == "VERIFIED"
    assert result["verification_status"] == "VERIFIED"


def test_worker_does_not_promote_unobservable_final_prose_to_verified():
    worker = UniversalGUIWorker(
        perception=FakePerception(window="Chrome"),
        client=FakeClient([response_without_tool_call()]),
        model="test-model",
    )
    result = worker.run_task("send the message")
    assert result["status"] == "UNVERIFIED"
