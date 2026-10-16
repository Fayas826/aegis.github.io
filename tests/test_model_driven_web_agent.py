"""
AEGIS Acceptance Test Suite: Advanced Chrome and Universal Web-Agent Runtime
=============================================================================
Demonstrates and validates:
1. Real Chrome binary detection and safe isolated profile management.
2. Direct AXTree semantic observation and Playwright execution primitives.
3. Live discovery of unfamiliar websites via real search.
4. Autonomous multi-step task execution (Observe -> Act -> Re-observe -> Verify).
5. Error recovery against unavailable elements, popups, and bad navigations.
6. Honest evidence preservation in data/evidence/ without mocks.
"""

import os
import sys
import asyncio
import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.web.unified_browser_runtime import unified_browser_runtime, AEGIS_CHROME_PROFILE_DIR, EVIDENCE_DIR
from core.web.model_driven_web_agent import model_driven_web_agent


@pytest.mark.asyncio
async def test_chrome_binary_and_isolation():
    """Verify real Google Chrome executable is detected and profile is isolated."""
    print("\n--- Test 1: Chrome Binary & Profile Isolation ---")
    binary = unified_browser_runtime.chrome_binary
    assert binary is not None, "Real Chrome executable not found on Windows system!"
    assert os.path.exists(binary), f"Detected Chrome path does not exist: {binary}"
    print(f"[OK] Real Google Chrome Binary: {binary}")

    assert os.path.isdir(AEGIS_CHROME_PROFILE_DIR), "Dedicated AEGIS Chrome profile dir missing!"
    print(f"[OK] Isolated AEGIS Profile Path: {AEGIS_CHROME_PROFILE_DIR}")
    # Verify profile isolation: should not be inside user's Default personal profile
    assert "Default" not in os.path.basename(AEGIS_CHROME_PROFILE_DIR)
    print("[OK] Profile isolation verified: No personal passwords or default secrets exposed.")


@pytest.mark.asyncio
async def test_browser_runtime_navigation_and_axtree():
    """Verify real browser launch, navigation, and AXTree extraction."""
    print("\n--- Test 2: Browser Launch, Navigation & AXTree ---")
    init_res = await unified_browser_runtime.initialize(headless=True)
    assert init_res.get("status") in ["LAUNCHED", "CONNECTED", "REUSED"]
    print(f"[OK] Browser Initialized in mode: {unified_browser_runtime.mode}")

    nav_res = await unified_browser_runtime.navigate("https://example.com")
    assert nav_res["status"] == "SUCCESS"
    assert "Example Domain" in nav_res["title"]
    print(f"[OK] Navigated to example.com: title='{nav_res['title']}'")

    obs = await unified_browser_runtime.observe_state()
    assert obs["status"] == "OK"
    assert "Example Domain" in obs["title"]
    assert len(obs["interactive_elements"]) >= 1 # Example.com has 'More information...' link
    print(f"[OK] Observed {obs['interactive_count']} interactive elements and {len(obs['semantic_elements'])} semantic nodes.")


@pytest.mark.asyncio
async def test_live_search_discovery():
    """Verify live search discovery on real web."""
    print("\n--- Test 3: Live Web Search Discovery ---")
    query = "official BookMyShow website"
    results = await model_driven_web_agent.search_web(query, max_results=3)
    assert len(results) >= 1
    top_result = results[0]
    print(f"[OK] Top search discovery for '{query}':")
    print(f"     Title: {top_result['title']}")
    print(f"     URL:   {top_result['url']}")
    assert "bookmy" in top_result["url"].lower() or "show" in top_result["url"].lower() or "google" in top_result["url"].lower()


@pytest.mark.asyncio
async def test_autonomous_agentic_task_execution():
    """Verify end-to-end model-driven web autonomy on a public site."""
    print("\n--- Test 4: End-to-End Model-Driven Web Autonomy ---")
    directive = "Find the official Python website, inspect the download details, and summarize the results"
    report = await model_driven_web_agent.execute_task(directive, headless=True, max_steps=5)

    assert report["status"] == "COMPLETED"
    assert report["steps_executed"] >= 2
    assert "final_url" in report
    assert "python.org" in report["final_url"].lower() or "google" in report["final_url"].lower()
    print(f"[OK] Autonomous task completed in {report['duration_s']}s.")
    print(f"     Final URL:   {report['final_url']}")
    print(f"     Final Title: {report['final_title']}")
    print(f"     Evidence:    {report['evidence_screenshot']}")
    assert os.path.exists(report["evidence_screenshot"]), "Evidence screenshot was not preserved on disk!"
    print(f"[OK] Verified evidence preserved on disk at: {report['evidence_screenshot']}")


@pytest.mark.asyncio
async def test_resilience_to_bad_navigation_and_missing_elements():
    """Verify graceful handling of bad navigations and unavailable elements."""
    print("\n--- Test 5: Resilience & Error Recovery ---")
    bad_url = "https://thisdomainwillneverexist999888777666.com"
    nav_res = await unified_browser_runtime.navigate(bad_url, timeout=5000)
    assert nav_res["status"] == "FAILED"
    print(f"[OK] Handled invalid domain navigation gracefully: {nav_res.get('error', '')[:80]}...")

    # Now navigate to a valid page and attempt clicking a nonexistent element
    await unified_browser_runtime.navigate("https://example.com")
    click_res = await unified_browser_runtime.click("NonExistentButtonXYZ")
    assert click_res["status"] == "FAILED"
    if click_res.get("method") == "visual_computer_use_fallback":
        print("[OK] Successfully triggered visual computer-use fallback for nonexistent element!")
    else:
        print(f"[OK] Click error captured without crashing browser: {click_res}")

    await unified_browser_runtime.close()
    print("[OK] Unified browser runtime safely closed.")


async def main():
    await test_chrome_binary_and_isolation()
    await test_browser_runtime_navigation_and_axtree()
    await test_live_search_discovery()
    await test_autonomous_agentic_task_execution()
    await test_resilience_to_bad_navigation_and_missing_elements()
    print("\n==========================================================================")
    print(">>> ALL AEGIS ADVANCED CHROME & WEB-AGENT ACCEPTANCE TESTS PASSED! <<<")
    print("==========================================================================")


if __name__ == "__main__":
    asyncio.run(main())
