"""Guardrails for real browser execution security boundaries."""

from pathlib import Path

from core.perception.browser_driver import PlaywrightBrowserDriver, detect_security_challenge

ROOT = Path(__file__).resolve().parents[1]


def test_canonical_browser_driver_does_not_use_stealth_or_no_sandbox_flags():
    source = (ROOT / "core" / "perception" / "browser_driver.py").read_text(encoding="utf-8")
    assert "AutomationControlled" not in source
    assert '"--no-sandbox"' not in source


def test_browser_use_does_not_disable_security_or_claim_challenge_bypass():
    source = (ROOT / "core" / "web" / "browser_use_agent.py").read_text(encoding="utf-8")
    assert "disable_security=True" not in source
    assert "bypasses anti-bot" not in source.lower()
    assert "never bypasses CAPTCHA" in source


def test_security_challenge_detection_is_case_insensitive_and_explicit():
    assert detect_security_challenge("Please VERIFY YOU ARE HUMAN to continue") == "verify you are human"
    assert detect_security_challenge("Normal search results") is None


def test_page_state_verification_distinguishes_blocked_and_unverified_results():
    before = {"available": True, "url": "https://example.test", "text": "Search", "observed_at": 1}
    blocked = {
        "available": True,
        "url": "https://example.test",
        "text": "Cloudflare verification",
        "security_challenge": "cloudflare",
        "observed_at": 2,
    }
    unchanged = {**before, "observed_at": 2}

    blocked_result = PlaywrightBrowserDriver.verify_page_state(before, blocked)
    unchanged_result = PlaywrightBrowserDriver.verify_page_state(before, unchanged)

    assert blocked_result["status"] == "BLOCKED"
    assert blocked_result["verified"] is False
    assert unchanged_result["status"] == "UNVERIFIED"
    assert unchanged_result["verified"] is False
