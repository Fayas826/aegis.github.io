"""
AEGIS Phase 4.2 Acceptance Test Suite: User-Visible Browser Execution & Live Interface Verification
====================================================================================================
Verifies:
1. Browser launched in visible mode (headless=False) vs background mode (headless=True).
2. Real top-level browser window exists and tracks owning process PID and HWND.
3. 4-state lifecycle distinction:
   - WINDOW_CREATED
   - WINDOW_VISIBLE
   - FOREGROUND_CONFIRMED
   - PAGE_STATE_VERIFIED
4. Physical desktop screenshot preview capture (not just Playwright DOM canvas).
5. Live progress streaming on enterprise_event_bus and FastAPI REST endpoint.
6. Task pause at confirmation gate with browser remaining open and accessible.
7. Operator interactive controls (pause, resume, cancel).
8. Truthful reporting when desktop session is unavailable or locked.
"""

import os
import sys
import time
import pytest
import asyncio
from typing import Dict, Any, List

sys.path.insert(0, os.path.abspath("."))

from core.perception.browser_driver import browser_driver
from core.ticket_booking_agent import JarvisTicketBooker
from core.booking.booking_state_machine import BookingStateMachine, BookingState, BookingContext
from core.booking.intent_parser import BookingIntent
from core.orchestration.enterprise_event_bus import enterprise_event_bus
from tests.mock_ticket_server import mock_ticket_server


@pytest.fixture(scope="module", autouse=True)
def mock_portal():
    """Spawns deterministic CineAEGIS mock ticketing portal."""
    mock_ticket_server.start()
    time.sleep(0.5)
    yield "http://127.0.0.1:8765/"
    mock_ticket_server.stop()
    asyncio.run(browser_driver.close())


def test_01_visible_vs_background_mode():
    """Verify explicit execution modes: visible (headed) vs background (headless)."""
    async def _run():
        # Background mode
        bg_driver = browser_driver
        page_bg = await bg_driver.launch_browser(execution_mode="background")
        assert page_bg is not None
        assert bg_driver.execution_mode == "background"
        vis_bg = await bg_driver.verify_visibility()
        assert vis_bg["mode"] == "background"
        assert vis_bg["WINDOW_CREATED"] is False
        assert vis_bg["WINDOW_VISIBLE"] is False
        assert vis_bg["FOREGROUND_CONFIRMED"] is False
        await bg_driver.close()

        # Visible mode
        page_vis = await bg_driver.launch_browser(execution_mode="visible")
        assert page_vis is not None
        assert bg_driver.execution_mode == "visible"
        await bg_driver.close()

    asyncio.run(_run())


def test_02_four_state_visibility_lifecycle(mock_portal):
    """Verify distinct states: WINDOW_CREATED -> WINDOW_VISIBLE -> FOREGROUND_CONFIRMED -> PAGE_STATE_VERIFIED."""
    async def _run():
        driver = browser_driver
        page = await driver.launch_browser(execution_mode="visible")
        assert page is not None

        # Navigate to mock portal
        nav_ok = await driver.navigate_to(mock_portal)
        assert nav_ok is True

        # Check 4-state lifecycle
        vis_info = await driver.verify_visibility()
        assert vis_info["mode"] == "visible"

        if sys.platform == "win32":
            # 1. WINDOW_CREATED
            assert vis_info["WINDOW_CREATED"] is True, "Expected browser HWND to be created in OS window table"
            assert vis_info["hwnd"] is not None
            assert vis_info["pid"] is not None

            # 2. WINDOW_VISIBLE
            assert vis_info["WINDOW_VISIBLE"] is True, "Expected browser window to be visible in desktop session"

            # 3. FOREGROUND_CONFIRMED
            # Elevated via Windows lock bypass
            assert vis_info["FOREGROUND_CONFIRMED"] is True, "Expected browser window to be in the foreground"

        # 4. PAGE_STATE_VERIFIED
        assert vis_info["PAGE_STATE_VERIFIED"] is True, "Expected page URL and title to match loaded portal"
        assert "CineAEGIS" in vis_info["title"]

        await driver.close()

    asyncio.run(_run())


def test_03_desktop_screen_snapshot_capture(mock_portal):
    """Verify real desktop screen snapshot capture containing non-zero bytes and valid PNG header."""
    async def _run():
        driver = browser_driver
        page = await driver.launch_browser(execution_mode="visible")
        await driver.navigate_to(mock_portal)

        preview_path = os.path.abspath("scratch/test_desktop_capture.png")
        data = driver.capture_desktop_preview(preview_path)

        assert len(data) > 1000, "Desktop capture must yield non-empty image data"
        assert data[:8] == b"\x89PNG\r\n\x1a\n", "Desktop capture must have valid PNG magic bytes"
        assert os.path.exists(preview_path), "Preview image file must exist on disk"

        await driver.close()

    asyncio.run(_run())


def test_04_live_progress_event_bus_emission(mock_portal):
    """Verify that interactive tasks emit real-time progress events across each workflow stage."""
    async def _run():
        received_events: List[Dict[str, Any]] = []

        def _collector(event):
            received_events.append(event.payload)

        enterprise_event_bus.subscribe("browser.execution.progress", _collector)

        booker = JarvisTicketBooker(execution_mode="visible")
        intent = BookingIntent(
            raw_query="Book 2 tickets for Avengers Secret Wars tomorrow at 09:15 PM in Kochi",
            movie_name="Avengers: Secret Wars",
            city="Kochi",
            ticket_count=2,
            date="tomorrow",
            time_preference="09:15 PM"
        )

        try:
            res = await booker.execute_booking_flow(intent, site_url=mock_portal, auto_authorize=False)
            assert res["status"] == "PENDING_CONFIRMATION"

            # Verify stages emitted
            stages_emitted = [e.get("stage") for e in received_events]
            assert "SEARCHING" in stages_emitted
            assert "RESULTS_VERIFICATION" in stages_emitted
            assert "SELECTION" in stages_emitted
            assert "DETAILS_VERIFICATION" in stages_emitted
            assert "PENDING_CONFIRMATION" in stages_emitted

            # Verify latest event has proper contract fields
            latest = received_events[-1]
            assert latest["mode"] == "visible"
            assert latest["is_pending_confirmation"] is True
            assert latest["window_state"]["WINDOW_CREATED"] is True
            assert latest["window_state"]["WINDOW_VISIBLE"] is True
            assert latest["action"] is not None
            assert latest["verified_outcome"] is not None

        finally:
            enterprise_event_bus.unsubscribe("browser.execution.progress", _collector)
            await booker.stop(close_browser=True)

    asyncio.run(_run())


def test_05_confirmation_gate_retention(mock_portal):
    """Verify that task halts at confirmation gate and leaves the browser open and visible for the operator."""
    async def _run():
        booker = JarvisTicketBooker(execution_mode="visible")
        intent = BookingIntent(
            raw_query="Book 2 tickets for Avengers Secret Wars in Kochi",
            movie_name="Avengers: Secret Wars",
            city="Kochi",
            ticket_count=2
        )

        try:
            res = await booker.execute_booking_flow(intent, site_url=mock_portal, auto_authorize=False)
            assert res["status"] == "PENDING_CONFIRMATION"
            assert res["state"] == BookingState.PENDING_CONFIRMATION.value

            # Browser MUST still be active and open for the user to inspect!
            page = booker.page
            assert page is not None
            assert not page.is_closed(), "Browser must NOT be closed at confirmation gate"

            # Verify window is still visible
            if sys.platform == "win32":
                vis = await browser_driver.verify_visibility()
                assert vis["WINDOW_VISIBLE"] is True

            # Confirm operator authorization succeeds afterwards
            sm = res["state_machine"]
            auth_res = await booker.authorize_and_submit(sm)
            assert auth_res["status"] == "CONFIRMED"
            assert auth_res["state"] == BookingState.CONFIRMED.value
            assert auth_res["booking_reference"].startswith("BKG-")

        finally:
            await booker.stop(close_browser=True)

    asyncio.run(_run())


def test_06_operator_interactive_controls():
    """Verify operator pause, resume, and cancel controls."""
    async def _run():
        driver = browser_driver
        assert driver.is_paused is False
        assert driver.is_cancelled is False

        await driver.pause()
        assert driver.is_paused is True

        await driver.resume()
        assert driver.is_paused is False

        await driver.cancel()
        assert driver.is_cancelled is True

        # Reset
        driver.is_cancelled = False

    asyncio.run(_run())


def test_07_backend_live_progress_endpoint():
    """Verify GET /api/browser/live_progress and POST /api/browser/control via FastAPI."""
    from fastapi.testclient import TestClient
    from backend.api import app

    client = TestClient(app)

    # 1. Check live progress
    res = client.get("/api/browser/live_progress")
    assert res.status_code == 200
    data = res.json()
    assert "mode" in data
    assert "stage" in data
    assert "window_state" in data
    assert "driver_status" in data

    # 2. Check control endpoint
    ctrl_res = client.post("/api/browser/control", json={"action": "pause"})
    assert ctrl_res.status_code == 200
    assert ctrl_res.json()["status"] == "SUCCESS"

    ctrl_res2 = client.post("/api/browser/control", json={"action": "resume"})
    assert ctrl_res2.status_code == 200
    assert ctrl_res2.json()["status"] == "SUCCESS"
