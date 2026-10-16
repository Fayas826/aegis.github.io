"""Offline contract tests for the optional official Gemini browser loop."""

from __future__ import annotations

import asyncio

from core.web.gemini_computer_use_agent import GeminiComputerUseBrowserAgent, _normalized_pixel


class Page:
    def is_closed(self):
        return False


class Runtime:
    page = Page()


def test_coordinates_are_bounded_and_normalized():
    assert _normalized_pixel(0, 100) == 0
    assert _normalized_pixel(1000, 100) == 99


def test_coordinate_out_of_range_is_rejected():
    try:
        _normalized_pixel(1001, 100)
    except ValueError:
        pass
    else:
        raise AssertionError("out-of-range computer-use coordinate was accepted")


def test_missing_gemini_configuration_is_unavailable():
    agent = GeminiComputerUseBrowserAgent(browser_runtime=Runtime(), client=None)
    result = asyncio.run(agent.execute_task("search the web"))
    assert result["status"] == "UNAVAILABLE"


def test_irreversible_browser_goal_requires_confirmation():
    agent = GeminiComputerUseBrowserAgent(browser_runtime=Runtime(), client=object())
    result = asyncio.run(agent.execute_task("delete this account"))
    assert result["status"] == "BLOCKED"
