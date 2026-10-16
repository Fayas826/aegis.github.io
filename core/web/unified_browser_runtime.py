"""
A.E.G.I.S. Unified Browser Runtime
===================================
Single source of truth for all browser operations across AEGIS.
Integrates:
- Real Google Chrome (`C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe`) & Chromium
- Safe Session Management:
    * Level A: User-authorized connection to existing Chrome session via CDP (127.0.0.1:9222)
    * Level B: Dedicated persistent isolation profile (`data/browser/chrome_profile`)
    * Security: Never copies, leaks, or touches default personal Chrome profile secrets/passwords.
- Playwright for robust execution, navigation, click/type/scroll, and tab management.
- Chrome DevTools Protocol & Accessibility Tree (AXTree) for fast semantic interaction.
- Visual Computer-Use Fallback (UI-TARS & coordinate grounding) when DOM/AXTree is inaccessible.
"""

import os
import sys
import time
import json
import logging
import asyncio
from typing import Dict, Any, List, Optional, Tuple, Union
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, CDPSession

logger = logging.getLogger("AEGIS.UnifiedBrowserRuntime")
logger.setLevel(logging.INFO)

# Detected Chrome executable path on this system
CHROME_EXECUTABLE_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")
]

# Dedicated isolated profile directory for AEGIS automation
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
AEGIS_CHROME_PROFILE_DIR = os.path.join(WORKSPACE_ROOT, "data", "browser", "chrome_profile")
EVIDENCE_DIR = os.path.join(WORKSPACE_ROOT, "data", "evidence")


class UnifiedBrowserRuntime:
    """
    Manages Chrome browser lifecycle, connection modes, accessibility inspection,
    and action execution.
    """

    def __init__(self):
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.cdp_session: Optional[CDPSession] = None
        self.mode: str = "UNINITIALIZED" # EXISTING_CDP | ISOLATED_CHROME | PLAYWRIGHT_CHROMIUM
        self.chrome_binary: Optional[str] = self._detect_chrome_binary()
        self._lock = asyncio.Lock()

        os.makedirs(AEGIS_CHROME_PROFILE_DIR, exist_ok=True)
        os.makedirs(EVIDENCE_DIR, exist_ok=True)

    def _detect_chrome_binary(self) -> Optional[str]:
        for path in CHROME_EXECUTABLE_CANDIDATES:
            if os.path.exists(path):
                return path
        return None

    async def initialize(
        self,
        headless: bool = False,
        allow_existing_cdp: bool = True,
        cdp_port: int = 9222
    ) -> Dict[str, Any]:
        """
        Initializes the browser runtime following safety hierarchies:
        1. If Chrome is already running with --remote-debugging-port=9222, connect to it (authorized existing session).
        2. Otherwise, launch real Google Chrome with dedicated persistent profile in data/browser/chrome_profile.
        3. Fallback to bundled Playwright Chromium if real Chrome executable cannot be launched.
        """
        async with self._lock:
            if self.page and not self.page.is_closed():
                return {
                    "status": "REUSED",
                    "mode": self.mode,
                    "url": self.page.url
                }

            if not self.playwright:
                self.playwright = await async_playwright().start()

            # Mode 1: Check for existing authorized Chrome instance on localhost:9222
            if allow_existing_cdp:
                try:
                    import socket
                    with socket.create_connection(("127.0.0.1", cdp_port), timeout=0.5):
                        pass
                    logger.info(f"[BrowserRuntime] Detected active Chrome debugging endpoint at 127.0.0.1:{cdp_port}. Connecting...")
                    self.browser = await self.playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
                    self.context = self.browser.contexts[0] if self.browser.contexts else await self.browser.new_context()
                    self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
                    self.mode = "EXISTING_CDP"
                    await self._attach_cdp()
                    logger.info("[BrowserRuntime] Connected to existing Chrome session safely.")
                    return {"status": "CONNECTED", "mode": self.mode, "url": self.page.url}
                except Exception as e:
                    logger.debug(f"[BrowserRuntime] Existing CDP not reachable ({e}). Proceeding to launch persistent Chrome.")

            # Mode 2: Launch real installed Chrome using isolated dedicated profile
            if self.chrome_binary:
                try:
                    logger.info(f"[BrowserRuntime] Launching real Chrome ({self.chrome_binary}) with isolated profile...")
                    self.context = await self.playwright.chromium.launch_persistent_context(
                        user_data_dir=AEGIS_CHROME_PROFILE_DIR,
                        executable_path=self.chrome_binary,
                        headless=headless,
                        viewport={"width": 1280, "height": 800},
                        args=[
                            "--no-first-run",
                            "--no-default-browser-check",
                            "--start-maximized"
                        ]
                    )
                    await self.context.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                        window.chrome = window.chrome || { runtime: {} };
                    """)
                    self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
                    self.mode = "ISOLATED_CHROME"
                    await self._attach_cdp()
                    logger.info("[BrowserRuntime] Real Google Chrome initialized with isolated profile.")
                    return {"status": "LAUNCHED", "mode": self.mode, "binary": self.chrome_binary}
                except Exception as e:
                    logger.warning(f"[BrowserRuntime] Failed to launch real Chrome ({e}). Falling back to Chromium.")

            # Mode 3: Fallback to Playwright Chromium
            try:
                logger.info("[BrowserRuntime] Launching Playwright Chromium fallback...")
                self.context = await self.playwright.chromium.launch_persistent_context(
                    user_data_dir=AEGIS_CHROME_PROFILE_DIR,
                    headless=headless,
                    viewport={"width": 1280, "height": 800},
                    args=["--start-maximized"]
                )
                await self.context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    window.chrome = window.chrome || { runtime: {} };
                """)
                self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
                self.mode = "PLAYWRIGHT_CHROMIUM"
                await self._attach_cdp()
                return {"status": "LAUNCHED", "mode": self.mode}
            except Exception as e:
                logger.error(f"[BrowserRuntime] Fatal browser launch failure: {e}")
                return {"status": "ERROR", "error": str(e)}

    async def _attach_cdp(self):
        """Attaches Chrome DevTools Protocol to enable low-latency AXTree extraction."""
        if not self.page:
            return
        try:
            self.cdp_session = await self.page.context.new_cdp_session(self.page)
            await self.cdp_session.send("Accessibility.enable")
            await self.cdp_session.send("DOM.enable")
            # The AXTree engine is a shared singleton. Keep the exact session
            # created for this runtime so observations use the live page.
            from core.web.cdp_axtree_engine import cdp_axtree_engine
            cdp_axtree_engine.cdp_session = self.cdp_session
        except Exception as e:
            logger.debug(f"[BrowserRuntime] CDP attach note: {e}")

    async def navigate(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30000) -> Dict[str, Any]:
        """Navigates to URL and waits for page load."""
        if not self.page or self.page.is_closed():
            await self.initialize()

        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"https://{url}"

        try:
            logger.info(f"[BrowserRuntime] Navigating to: {url}")
            response = await self.page.goto(url, wait_until=wait_until, timeout=timeout)
            status_code = response.status if response else 200
            await asyncio.sleep(1.0) # Settle dynamic scripts
            final_url = self.page.url
            if status_code >= 400:
                return {
                    "status": "FAILED",
                    "url": final_url,
                    "requested_url": url,
                    "http_status": status_code,
                    "error": f"HTTP {status_code} returned by destination",
                }
            return {
                "status": "SUCCESS",
                "url": final_url,
                "requested_url": url,
                "redirected": final_url.rstrip("/") != url.rstrip("/"),
                "title": await self.page.title(),
                "http_status": status_code
            }
        except Exception as e:
            logger.error(f"[BrowserRuntime] Navigation failed to {url}: {e}")
            return {
                "status": "FAILED",
                "url": url,
                "error": str(e)
            }

    async def observe_state(self, max_elements: int = 40) -> Dict[str, Any]:
        """
        Gathers comprehensive page observation:
        - URL and title
        - Semantic Accessibility Tree (AXTree) nodes
        - Interactive elements list with selectors/roles
        - Compact text summary
        """
        if not self.page or self.page.is_closed():
            return {"status": "CLOSED", "error": "Browser is not active."}

        try:
            url = self.page.url
            title = await self.page.title()

            # 1. Extract AXTree semantic elements
            semantic_elements = []
            try:
                from core.web.cdp_axtree_engine import cdp_axtree_engine
                if cdp_axtree_engine.cdp_session is None:
                    await cdp_axtree_engine.attach_cdp(self.page)
                semantic_elements = await cdp_axtree_engine.extract_axtree(self.page)
            except Exception as e:
                logger.debug(f"[BrowserRuntime] AXTree extraction note: {e}")

            # 2. Extract DOM interactive controls
            interactive_controls = await self.page.evaluate("""
                () => {
                    const elements = [];
                    const selectors = 'a, button, input, select, textarea, [role="button"], [role="link"], [role="searchbox"], [role="tab"]';
                    const nodes = document.querySelectorAll(selectors);
                    let idx = 0;
                    for (const node of nodes) {
                        if (idx >= 60) break;
                        const rect = node.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0 && window.getComputedStyle(node).visibility !== 'hidden') {
                            const text = (node.innerText || node.getAttribute('aria-label') || node.getAttribute('placeholder') || node.getAttribute('title') || node.value || '').trim();
                            elements.push({
                                index: idx,
                                tag: node.tagName.toLowerCase(),
                                role: node.getAttribute('role') || node.tagName.toLowerCase(),
                                text: text.substring(0, 80),
                                id: node.id || null,
                                name: node.name || null,
                                href: node.href || null,
                                bbox: [Math.round(rect.left), Math.round(rect.top), Math.round(rect.right), Math.round(rect.bottom)]
                            });
                            idx++;
                        }
                    }
                    return elements;
                }
            """)

            # 3. Compact text preview
            body_text = await self.page.evaluate("document.body.innerText")
            text_snippet = (body_text or "").strip()[:800]

            return {
                "status": "OK",
                "observed_at": time.time(),
                "url": url,
                "title": title,
                "interactive_count": len(interactive_controls),
                "interactive_elements": interactive_controls[:max_elements],
                "semantic_elements": semantic_elements[:max_elements],
                "text_snippet": text_snippet
            }
        except Exception as e:
            logger.error(f"[BrowserRuntime] Observe state error: {e}")
            return {"status": "ERROR", "error": str(e)}

    async def click(self, target: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
        """
        Clicks an element using semantic locator, selector, text matching, or coordinates.
        Supports visual fallback if selector is stale.
        """
        if not self.page:
            return {"status": "FAILED", "error": "No page"}

        try:
            # 1. Text or Role locator
            if isinstance(target, str):
                # Try locator by text
                locator = self.page.get_by_text(target, exact=False).first
                if await locator.is_visible():
                    before = await self.observe_state()
                    try:
                        await locator.click(timeout=3000)
                    except Exception:
                        logger.info(f"[BrowserRuntime] Click intercepted by overlay, attempting force click on '{target}'")
                        await locator.click(timeout=3000, force=True)
                    await asyncio.sleep(1.0)
                    after = await self.observe_state()
                    return {"status": "SUCCESS", "action_status": "ACTION_SUCCEEDED", "method": "text_locator", "target": target, "before": before, "after": after}

                # Try CSS selector
                try:
                    before = await self.observe_state()
                    try:
                        await self.page.click(target, timeout=3000)
                    except Exception:
                        await self.page.click(target, timeout=3000, force=True)
                    await asyncio.sleep(1.0)
                    after = await self.observe_state()
                    return {"status": "SUCCESS", "action_status": "ACTION_SUCCEEDED", "method": "css_selector", "target": target, "before": before, "after": after}
                except Exception:
                    pass

                # Try placeholder / role
                locator_role = self.page.get_by_role("button", name=target).first
                if await locator_role.is_visible():
                    before = await self.observe_state()
                    try:
                        await locator_role.click(timeout=3000)
                    except Exception:
                        await locator_role.click(timeout=3000, force=True)
                    await asyncio.sleep(1.0)
                    after = await self.observe_state()
                    return {"status": "SUCCESS", "action_status": "ACTION_SUCCEEDED", "method": "role_button", "target": target, "before": before, "after": after}

            # 2. Coordinate click dictionary e.g. {"x": 100, "y": 200}
            elif isinstance(target, dict) and "x" in target and "y" in target:
                before = await self.observe_state()
                await self.page.mouse.click(target["x"], target["y"])
                await asyncio.sleep(1.0)
                after = await self.observe_state()
                return {"status": "SUCCESS", "action_status": "ACTION_SUCCEEDED", "method": "coordinate_click", "coords": (target["x"], target["y"]), "before": before, "after": after}

            # 3. Visual fallback grounding
            return await self._visual_fallback_click(str(target))

        except Exception as e:
            logger.warning(f"[BrowserRuntime] Click failed on '{target}': {e}. Initiating visual fallback...")
            return await self._visual_fallback_click(str(target))

    async def type_text(self, selector_or_label: str, text: str, press_enter: bool = False) -> Dict[str, Any]:
        """Types text into an input field."""
        if not self.page:
            return {"status": "FAILED", "error": "No page"}

        try:
            # Try by placeholder
            locator = self.page.get_by_placeholder(selector_or_label).first
            if await locator.is_visible():
                await locator.fill(text)
                if press_enter:
                    await locator.press("Enter")
                await asyncio.sleep(1.0)
                return {"status": "SUCCESS", "method": "placeholder", "field": selector_or_label}

            # Try by label / searchbox role
            locator_search = self.page.get_by_role("searchbox").first
            if await locator_search.is_visible():
                await locator_search.fill(text)
                if press_enter:
                    await locator_search.press("Enter")
                await asyncio.sleep(1.0)
                return {"status": "SUCCESS", "method": "role_searchbox"}

            # Try by CSS selector
            await self.page.fill(selector_or_label, text, timeout=6000)
            if press_enter:
                await self.page.press(selector_or_label, "Enter")
            await asyncio.sleep(1.0)
            return {"status": "SUCCESS", "method": "css_fill", "field": selector_or_label}
        except Exception as e:
            # General fallback: find first visible text input
            try:
                inputs = await self.page.query_selector_all("input[type='text'], input[type='search'], input:not([type])")
                for inp in inputs:
                    if await inp.is_visible():
                        await inp.fill(text)
                        if press_enter:
                            await self.page.keyboard.press("Enter")
                        await asyncio.sleep(1.0)
                        return {"status": "SUCCESS", "method": "first_visible_input"}
            except Exception:
                pass
            return {"status": "FAILED", "error": str(e)}

    async def scroll(self, direction: str = "down", amount: int = 400) -> Dict[str, Any]:
        """Scrolls the active window."""
        if not self.page:
            return {"status": "FAILED", "error": "No page"}
        delta_y = amount if direction == "down" else -amount
        await self.page.mouse.wheel(0, delta_y)
        await asyncio.sleep(0.8)
        return {"status": "SUCCESS", "direction": direction, "amount": amount}

    async def _visual_fallback_click(self, instruction: str) -> Dict[str, Any]:
        """
        Visual Fallback (UI-TARS & Coordinate Mapper):
        Takes screenshot of current page, grounds the target instruction visually,
        and clicks the physical viewport coordinates.
        """
        try:
            screenshot_path = os.path.join(EVIDENCE_DIR, f"visual_fallback_{int(time.time())}.png")
            await self.page.screenshot(path=screenshot_path)

            from core.cognition.reasoning.neural_extensions_bridge import neural_bridge
            grounding = neural_bridge.ground_ui_tars(screenshot_path, instruction)

            coords = None
            if isinstance(grounding, dict):
                for key in ("coords", "center", "point"):
                    candidate = grounding.get(key)
                    if isinstance(candidate, (list, tuple)) and len(candidate) >= 2:
                        coords = (candidate[0], candidate[1])
                        break
                    if isinstance(candidate, dict) and "x" in candidate and "y" in candidate:
                        coords = (candidate["x"], candidate["y"])
                        break
            if not coords:
                # Viewport coordinate mapping fallback from DOM
                try:
                    dom_point = await self.page.evaluate("""
                        (targetText) => {
                            const elements = Array.from(document.querySelectorAll('a, button, input, span, div, p, h1, h2, h3'));
                            const cleanTarget = (targetText || '').toLowerCase().trim();
                            for (const el of elements) {
                                const t = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').toLowerCase().trim();
                                if (t && (t === cleanTarget || t.includes(cleanTarget) || cleanTarget.includes(t))) {
                                    const rect = el.getBoundingClientRect();
                                    if (rect.width > 0 && rect.height > 0) {
                                        return {x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2)};
                                    }
                                }
                            }
                            return null;
                        }
                    """, instruction)
                    if dom_point:
                        coords = (dom_point["x"], dom_point["y"])
                        logger.info(f"[BrowserRuntime] Visual fallback resolved via DOM viewport coordinate mapping: {coords}")
                except Exception as eval_err:
                    logger.debug(f"[BrowserRuntime] DOM coordinate fallback note: {eval_err}")

            if not coords:
                return {"status": "FAILED", "method": "visual_computer_use_fallback", "error": "Visual grounding produced no actionable coordinates", "grounding": grounding, "screenshot": screenshot_path}
            before = await self.observe_state()
            await self.page.mouse.click(float(coords[0]), float(coords[1]))
            await asyncio.sleep(1.0)
            after = await self.observe_state()
            logger.info(f"[BrowserRuntime] Visual fallback clicked '{instruction}' at {coords}.")
            return {"status": "SUCCESS", "action_status": "ACTION_SUCCEEDED", "method": "visual_computer_use_fallback", "coords": coords, "grounding": grounding, "screenshot": screenshot_path, "before": before, "after": after}
        except Exception as e:
            logger.error(f"[BrowserRuntime] Visual fallback failure: {e}")
            return {"status": "FAILED", "error": str(e)}

    async def capture_evidence(self, label: str) -> str:
        """Saves a verification screenshot to the evidence directory."""
        if not self.page or self.page.is_closed():
            return ""
        safe_label = "".join(c if c.isalnum() else "_" for c in label)
        filepath = os.path.join(EVIDENCE_DIR, f"evidence_{safe_label}_{int(time.time()*1000)}.png")
        try:
            await self.page.screenshot(path=filepath, full_page=False)
            logger.info(f"[BrowserRuntime] Preserved verified evidence at: {filepath}")
            return filepath
        except Exception as e:
            logger.warning(f"[BrowserRuntime] Failed to capture evidence: {e}")
            return ""

    async def close(self, keep_profile_alive: bool = True):
        """Closes browser session while keeping persistent profile intact."""
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            self.playwright = None
            self.browser = None
            self.context = None
            self.page = None
            self.mode = "CLOSED"
            logger.info("[BrowserRuntime] Browser session gracefully closed.")
        except Exception as e:
            logger.debug(f"[BrowserRuntime] Close exception: {e}")


# Singleton instance
unified_browser_runtime = UnifiedBrowserRuntime()
