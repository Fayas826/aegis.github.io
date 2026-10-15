"""Regression tests for truthful desktop screenshot delivery."""

import asyncio
import json
from unittest.mock import patch

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
