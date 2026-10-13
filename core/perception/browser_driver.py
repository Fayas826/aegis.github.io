"""
A.E.G.I.S. Playwright Browser Driver & Automation Infrastructure
================================================================
Phase 4: Unified Browser Controller for Semantic Web Interaction.
Bridges cross-agent sessions, DOM inspection, semantic locators,
and verified navigation state across the AEGIS ecosystem.
"""

import os
import asyncio
import logging
from typing import Dict, Any, List, Optional

try:
    from playwright.async_api import async_playwright, Page, Browser, BrowserContext, ElementHandle
except ImportError:
    async_playwright = None
    Page = None
    Browser = None
    BrowserContext = None
    ElementHandle = None

logger = logging.getLogger("AEGIS.BrowserDriver")


class PlaywrightBrowserDriver:
    """Manages active Playwright browser instance context and page element boundaries."""

    def __init__(self):
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None

    def get_page(self) -> Optional[Page]:
        """Returns the active Playwright Page instance, checking session manager if needed."""
        if self.page and not self.page.is_closed():
            try:
                curr_loop = asyncio.get_running_loop()
                page_loop = getattr(self.page, "_loop", None)
                if page_loop and (page_loop.is_closed() or page_loop != curr_loop):
                    # Stale handle from a previous asyncio.run event loop
                    self.page = None
                    self.context = None
                    self.browser = None
                    self.playwright = None
                    return None
            except RuntimeError:
                pass
            return self.page

        # Check computer_use_session_manager
        try:
            from core.orchestration.computer_use_session import computer_use_session_manager
            shared_page = computer_use_session_manager.get_browser_page()
            if shared_page and not shared_page.is_closed():
                try:
                    curr_loop = asyncio.get_running_loop()
                    page_loop = getattr(shared_page, "_loop", None)
                    if page_loop and (page_loop.is_closed() or page_loop != curr_loop):
                        return None
                except RuntimeError:
                    pass
                self.page = shared_page
                return self.page
        except Exception:
            pass

        return None

    def set_shared_page(self, page: Page, playwright_inst=None, browser_inst=None):
        """Sets the active page and propagates it to computer_use_session_manager."""
        self.page = page
        if playwright_inst:
            self.playwright = playwright_inst
        if browser_inst:
            self.browser = browser_inst
        try:
            from core.orchestration.computer_use_session import computer_use_session_manager
            computer_use_session_manager.set_browser_page(page, playwright_inst, browser_inst)
        except Exception:
            pass

    async def launch_browser(self, headless: bool = False, channel: Optional[str] = None) -> Optional[Page]:
        """Launches or reconnects to an active browser instance."""
        if async_playwright is None:
            logger.error("[BROWSER] Playwright is not installed.")
            return None

        # Check existing live page
        existing_page = self.get_page()
        if existing_page and not existing_page.is_closed():
            logger.info("[BROWSER] Reusing existing live browser session.")
            return existing_page

        if not self.playwright:
            self.playwright = await async_playwright().start()

        launch_args = [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--start-maximized"
        ]

        # Attempt channel launch if requested or fallback to standard chromium
        if not self.browser:
            if channel == "chrome":
                try:
                    self.browser = await self.playwright.chromium.launch(
                        channel="chrome",
                        headless=headless,
                        args=launch_args
                    )
                except Exception as ex:
                    logger.warning(f"[BROWSER] Chrome channel unavailable ({ex}); falling back to bundled chromium.")
                    self.browser = await self.playwright.chromium.launch(
                        headless=headless,
                        args=launch_args
                    )
            else:
                self.browser = await self.playwright.chromium.launch(
                    headless=headless,
                    args=launch_args
                )

        if not self.context:
            self.context = await self.browser.new_context(
                no_viewport=True,
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )

        self.page = await self.context.new_page()
        self.set_shared_page(self.page, self.playwright, self.browser)
        logger.info("[BROWSER] Playwright Chromium online and context registered.")
        return self.page

    async def navigate_to(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30000) -> bool:
        """Navigates active page to target URL with explicit readiness check."""
        page = self.get_page()
        if not page:
            page = await self.launch_browser(headless=False)
            if not page:
                return False

        if not url.startswith("http://") and not url.startswith("https://") and not url.startswith("file://"):
            url = f"https://{url}"

        try:
            logger.info(f"[BROWSER] Navigating to: {url}")
            await page.goto(url, wait_until=wait_until, timeout=timeout)
            await page.wait_for_timeout(1000)
            return True
        except Exception as e:
            logger.error(f"[BROWSER] Navigation failed to '{url}': {e}")
            return False

    async def get_current_url(self) -> str:
        """Returns the current URL of the active page."""
        page = self.get_page()
        return page.url if page else ""

    async def get_title(self) -> str:
        """Returns the current page title."""
        page = self.get_page()
        if not page:
            return ""
        try:
            return await page.title()
        except Exception:
            return ""

    async def get_active_elements(self) -> List[Dict[str, Any]]:
        """Executes JS in the active browser page to fetch interactive element boundaries."""
        page = self.get_page()
        if not page:
            return []

        js_script = """
        () => {
            const elements = [];
            const items = document.querySelectorAll('button, input, textarea, a, select, [role="button"], [role="searchbox"]');
            items.forEach((el, idx) => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    elements.push({
                        id: `dom_${idx}`,
                        text: el.innerText || el.value || el.placeholder || el.getAttribute('aria-label') || '',
                        type: el.tagName.toLowerCase(),
                        role: el.getAttribute('role') || el.tagName.toLowerCase(),
                        bbox: [Math.round(rect.left), Math.round(rect.top), Math.round(rect.right), Math.round(rect.bottom)],
                        center: [Math.round(rect.left + rect.width/2), Math.round(rect.top + rect.height/2)],
                        clickable: true
                    });
                }
            });
            return elements;
        }
        """
        try:
            elements = await page.evaluate(js_script)
            return elements
        except Exception as e:
            logger.error(f"[BROWSER] Failed to parse DOM context elements: {e}")
            return []

    async def click_element(self, selector: str, timeout: int = 10000) -> bool:
        """Clicks an element by CSS, text, or XPath selector."""
        page = self.get_page()
        if not page:
            return False
        try:
            loc = page.locator(selector).first
            await loc.wait_for(state="visible", timeout=timeout)
            await loc.click()
            await page.wait_for_timeout(500)
            return True
        except Exception as e:
            logger.warning(f"[BROWSER] Click failed on '{selector}': {e}")
            return False

    async def fill_element(self, selector: str, text: str, timeout: int = 10000) -> bool:
        """Fills an input field by selector."""
        page = self.get_page()
        if not page:
            return False
        try:
            loc = page.locator(selector).first
            await loc.wait_for(state="visible", timeout=timeout)
            await loc.fill(text)
            await page.wait_for_timeout(300)
            return True
        except Exception as e:
            logger.warning(f"[BROWSER] Fill failed on '{selector}': {e}")
            return False

    async def click_semantic(self, text: Optional[str] = None, role: Optional[str] = None, timeout: int = 10000) -> bool:
        """Clicks an element using semantic Playwright locators (role, accessible text)."""
        page = self.get_page()
        if not page:
            return False
        try:
            if role and text:
                loc = page.get_by_role(role, name=text, exact=False).first
            elif role:
                loc = page.get_by_role(role).first
            elif text:
                loc = page.get_by_text(text, exact=False).first
            else:
                return False

            await loc.wait_for(state="visible", timeout=timeout)
            await loc.click()
            await page.wait_for_timeout(500)
            return True
        except Exception as e:
            logger.warning(f"[BROWSER] Semantic click failed for role='{role}', text='{text}': {e}")
            return False

    async def fill_semantic(self, placeholder: Optional[str] = None, text: str = "", label: Optional[str] = None, timeout: int = 10000) -> bool:
        """Fills an input field using placeholder or label semantic locators."""
        page = self.get_page()
        if not page:
            return False
        try:
            if placeholder:
                loc = page.get_by_placeholder(placeholder, exact=False).first
            elif label:
                loc = page.get_by_label(label, exact=False).first
            else:
                return False

            await loc.wait_for(state="visible", timeout=timeout)
            await loc.fill(text)
            await page.wait_for_timeout(300)
            return True
        except Exception as e:
            logger.warning(f"[BROWSER] Semantic fill failed for placeholder='{placeholder}', label='{label}': {e}")
            return False

    async def take_screenshot(self, output_path: Optional[str] = None) -> bytes:
        """Captures page screenshot bytes and optionally saves to disk."""
        page = self.get_page()
        if not page:
            return b""
        data = await page.screenshot()
        if output_path:
            os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
            with open(output_path, "wb") as f:
                f.write(data)
        return data

    async def close(self):
        """Clean teardown of browser, context, and Playwright session."""
        if self.page:
            try:
                await self.page.close()
            except Exception:
                pass
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass

        self.page = None
        self.context = None
        self.browser = None
        self.playwright = None

        try:
            from core.orchestration.computer_use_session import computer_use_session_manager
            computer_use_session_manager.set_browser_page(None, None, None)
        except Exception:
            pass


browser_driver = PlaywrightBrowserDriver()
