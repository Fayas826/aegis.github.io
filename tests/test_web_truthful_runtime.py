"""Offline contracts for the model-driven web runtime's failure boundaries."""

import asyncio
import os
import time

from core.web.model_driven_web_agent import ModelDrivenWebAgent


class FakeRuntime:
    mode = "FAKE"

    def __init__(self, init_status="LAUNCHED", nav_status="SUCCESS", action_status="SUCCESS", evidence_path=None):
        self.init_status = init_status
        self.nav_status = nav_status
        self.action_status = action_status
        self.evidence_path = evidence_path
        self.page = None

    async def initialize(self, headless=False):
        return {"status": self.init_status, "mode": self.mode}

    async def navigate(self, url):
        return {
            "status": self.nav_status,
            "url": url,
            "title": "Example Domain" if self.nav_status == "SUCCESS" else "",
            "error": "navigation failed" if self.nav_status != "SUCCESS" else None,
        }

    async def observe_state(self):
        return {
            "status": "OK",
            "observed_at": time.time(),
            "url": "https://example.com/",
            "title": "Example Domain",
            "text_snippet": "Example Domain content available for inspection.",
            "interactive_elements": [],
            "semantic_elements": [],
        }

    async def click(self, target):
        return {"status": self.action_status, "target": target}

    async def type_text(self, field, text, press_enter=False):
        return {"status": self.action_status, "field": field}

    async def scroll(self, direction="down", amount=450):
        return {"status": self.action_status, "direction": direction}

    async def capture_evidence(self, label):
        return self.evidence_path or ""


def run(coro):
    return asyncio.run(coro)


async def no_search_results(query):
    return []


async def example_search_result(query):
    return [{"title": "Example Domain", "url": "https://example.com/", "snippet": "Example", "verified": True}]


def test_browser_initialization_failure_is_not_completed():
    agent = ModelDrivenWebAgent()
    agent.browser_runtime = FakeRuntime(init_status="ERROR")
    result = run(agent.execute_task("Open the example page", headless=True, max_steps=1))
    assert result["status"] == "UNAVAILABLE"
    assert result["verification_status"] == "UNAVAILABLE"


def test_navigation_failure_is_not_completed():
    agent = ModelDrivenWebAgent()
    agent.browser_runtime = FakeRuntime(nav_status="FAILED")
    agent.search_web = example_search_result
    result = run(agent.execute_task("Open the example page", headless=True, max_steps=1))
    assert result["status"] == "FAILED"
    assert result["verification_status"] == "UNVERIFIED"


def test_success_requires_evidence_and_fresh_page_state(tmp_path):
    evidence = tmp_path / "evidence.png"
    evidence.write_bytes(b"png")
    agent = ModelDrivenWebAgent()
    agent.browser_runtime = FakeRuntime(evidence_path=str(evidence))
    agent.search_web = example_search_result
    agent._get_synaptic_memory = lambda: None
    agent._get_central_brain = lambda: None

    result = run(agent.execute_task("Open the example page and summarize the information", headless=True, max_steps=1))

    assert result["status"] == "COMPLETED"
    assert result["verification_status"] == "VERIFIED"
    assert os.path.exists(result["evidence_screenshot"])


def test_action_failure_cannot_become_completed():
    agent = ModelDrivenWebAgent()
    agent.browser_runtime = FakeRuntime(action_status="FAILED")
    agent.search_web = example_search_result
    agent._get_synaptic_memory = lambda: None
    agent._get_central_brain = lambda: None

    result = run(agent.execute_task("Search for public events", headless=True, max_steps=3))

    assert result["status"] == "UNVERIFIED"
    assert result["verification_status"] == "UNVERIFIED"
    assert result["action_failures"]
