"""Offline contracts for the focused multimodal runtime adapter."""

import asyncio

from core.runtime.multimodal_runtime import (
    GeminiConfiguration,
    MultimodalRuntimeAdapter,
    universal_gui_tool_contract,
    validate_gemini_configuration,
    validate_gui_goal,
)


def run(coro):
    return asyncio.run(coro)


def test_gemini_validation_never_returns_a_secret():
    result = validate_gemini_configuration({"GEMINI_API_KEY": "secret-value"})
    assert result["key_configured"] is True
    assert "secret-value" not in repr(result)
    assert result["key_env"] == "GEMINI_API_KEY"


def test_missing_gemini_configuration_is_explicitly_unavailable():
    result = GeminiConfiguration().validate({})
    assert result["status"] == "UNAVAILABLE"
    assert "GEMINI_API_KEY" in result["missing"]


def test_tool_contract_is_one_bounded_goal_function():
    contract = universal_gui_tool_contract()
    declarations = contract["function_declarations"]
    assert [item["name"] for item in declarations] == ["execute_universal_gui_task"]
    assert declarations[0]["parameters"]["required"] == ["goal"]
    assert "4000" in declarations[0]["parameters"]["properties"]["goal"]["description"]


def test_goal_validation_rejects_empty_and_oversized_values():
    assert validate_gui_goal("   ") == (None, "A concrete GUI goal is required.")
    goal, error = validate_gui_goal("x" * 4001)
    assert goal is None
    assert "4000" in error


class FakeBrowserAgent:
    def __init__(self, report):
        self.report = report

    async def execute_task(self, goal, headless=False, max_steps=8):
        return dict(self.report, task=goal, headless=headless, max_steps=max_steps)


class FakeDesktopWorker:
    def __init__(self, outcome):
        self.outcome = outcome

    def run_task(self, goal, status_callback=None):
        return dict(self.outcome, goal=goal)


def test_browser_success_requires_real_evidence_artifact(tmp_path):
    evidence = tmp_path / "browser.png"
    evidence.write_bytes(b"png")
    agent = FakeBrowserAgent(
        {
            "status": "COMPLETED",
            "verification_status": "VERIFIED",
            "final_url": "https://example.test/",
            "evidence_screenshot": str(evidence),
        }
    )
    adapter = MultimodalRuntimeAdapter(browser_agent=agent)
    result = run(adapter.execute_browser_goal("Open https://example.test/"))
    assert result["status"] == "VERIFIED"


def test_browser_claim_without_evidence_is_unverified():
    adapter = MultimodalRuntimeAdapter(
        browser_agent=FakeBrowserAgent(
            {"status": "COMPLETED", "verification_status": "VERIFIED", "final_url": "https://example.test/"}
        )
    )
    result = run(adapter.execute_browser_goal("Open https://example.test/"))
    assert result["status"] == "UNVERIFIED"


def test_desktop_worker_success_without_verification_is_unverified():
    adapter = MultimodalRuntimeAdapter(
        desktop_worker=FakeDesktopWorker({"status": "SUCCESS", "detail": "ran"})
    )
    result = run(adapter.execute_desktop_goal("Open the calculator"))
    assert result["status"] == "UNVERIFIED"


def test_desktop_worker_can_report_verified_status():
    adapter = MultimodalRuntimeAdapter(
        desktop_worker=FakeDesktopWorker({"status": "SUCCESS", "verification_status": "VERIFIED", "verified": True})
    )
    result = run(adapter.execute_desktop_goal("Open the calculator"))
    assert result["status"] == "VERIFIED"


def test_barge_in_clears_audio_and_cancels_future_goals():
    adapter = MultimodalRuntimeAdapter()
    adapter.enqueue_audio(b"audio")
    result = adapter.barge_in()
    assert result == {"status": "INTERRUPTED", "audio_cleared": True}
    assert list(adapter.audio_out) == []
    cancelled = run(adapter.execute_desktop_goal("Open the calculator"))
    assert cancelled["status"] == "CANCELLED"


def test_injected_live_session_supports_audio_and_screen_without_sdk():
    class Session:
        def __init__(self):
            self.sent = []

        async def send(self, **kwargs):
            self.sent.append(kwargs)

    session = Session()
    adapter = MultimodalRuntimeAdapter()
    assert run(adapter.start_live(session=session))["status"] == "READY"
    assert run(adapter.send_audio_chunk(b"pcm"))["status"] == "SENT"
    assert run(adapter.send_screen_frame(b"jpg"))["status"] == "SENT"
    assert len(session.sent) == 2
