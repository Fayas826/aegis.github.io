"""Regression tests for truthful desktop screenshot delivery."""

import asyncio
import json
import time
from unittest.mock import patch

from PIL import Image
from backend.routes.system_routes import get_system_screenshot


def test_screenshot_capture_failure_never_returns_stale_or_blank_success():
    """A failed current capture must be an explicit unavailable response."""
    with patch(
        "backend.routes.system_routes.pyautogui.screenshot",
        side_effect=RuntimeError("Display device unavailable"),
    ):
        response = asyncio.run(get_system_screenshot())

    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload["status"] == "VISION_UNAVAILABLE"
    assert payload["verified"] is False
    assert payload["error_code"] == "DESKTOP_SESSION_UNAVAILABLE"
    assert "Display device unavailable" in payload["detail"]


def test_successful_capture_has_freshness_and_source_headers():
    with patch(
        "backend.routes.system_routes.pyautogui.screenshot",
        return_value=Image.new("RGB", (8, 8), color=(1, 2, 3)),
    ):
        response = asyncio.run(get_system_screenshot())

    assert response.status_code == 200
    assert response.headers["cache-control"].startswith("no-store")
    assert response.headers["x-aegis-capture-source"] == "pyautogui.desktop"
    captured_at_ns = int(response.headers["x-aegis-captured-at-ns"])
    assert captured_at_ns <= time.time_ns()
    assert response.body.startswith(b"\x89PNG")


def test_concurrent_captures_return_independent_current_frames():
    colors = iter([(10, 20, 30), (40, 50, 60), (70, 80, 90), (100, 110, 120)])

    def capture():
        return Image.new("RGB", (8, 8), color=next(colors))

    async def run():
        with patch("backend.routes.system_routes.pyautogui.screenshot", side_effect=capture):
            return await asyncio.gather(*(get_system_screenshot() for _ in range(4)))

    responses = asyncio.run(run())
    assert [r.status_code for r in responses] == [200, 200, 200, 200]
    assert all(r.headers["x-aegis-capture-source"] == "pyautogui.desktop" for r in responses)
    assert len({r.body for r in responses}) == 4


def test_capture_timeout_returns_unavailable_without_fallback(monkeypatch):
    monkeypatch.setenv("AEGIS_SCREENSHOT_TIMEOUT_S", "0.1")

    def slow_capture():
        time.sleep(0.5)
        return Image.new("RGB", (8, 8))

    with patch("backend.routes.system_routes.pyautogui.screenshot", side_effect=slow_capture):
        response = asyncio.run(get_system_screenshot())

    assert response.status_code == 503
    payload = json.loads(response.body)
    assert payload["verified"] is False
    assert "timed out" in payload["detail"].lower()
