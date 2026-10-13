import os
import sys
import time
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

# Win32 user32/kernel32 handles for foreground and window verification
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32
else:
    _user32 = None
    _kernel32 = None


class PlaywrightBrowserDriver:
    """Manages active Playwright browser instance context and page element boundaries."""

    def __init__(self):
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.execution_mode: str = "visible"  # "visible" (interactive user-attended) or "background" (headless test)
        self.browser_pid: Optional[int] = None
        self.browser_hwnd: Optional[int] = None
        self.is_paused: bool = False
        self.is_cancelled: bool = False
        self.last_action: str = "INITIALIZED"
        self.last_outcome: str = "IDLE"
        self.window_state: Dict[str, Any] = {
            "WINDOW_CREATED": False,
            "WINDOW_VISIBLE": False,
            "FOREGROUND_CONFIRMED": False,
            "PAGE_STATE_VERIFIED": False
        }

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

    async def discover_browser_window(self) -> Optional[int]:
        """Locates the top-level OS window handle (HWND) belonging to the active Chromium instance."""
        if sys.platform != "win32":
            return None

        # 1. Attempt to resolve exact OS PID via CDP SystemInfo
        if self.browser:
            try:
                cdp = await self.browser.new_browser_cdp_session()
                proc = await cdp.send("SystemInfo.getProcessInfo")
                pids = [p["id"] for p in proc.get("processInfo", []) if p.get("type") == "browser"]
                if pids:
                    self.browser_pid = pids[0]
            except Exception:
                pass

        matched_hwnd = None
        try:
            import win32gui, win32process

            def _enum_win(hwnd, _):
                nonlocal matched_hwnd
                if win32gui.IsWindowVisible(hwnd):
                    cls_name = win32gui.GetClassName(hwnd).lower()
                    if "chrome_widgetwin_1" in cls_name:
                        txt = win32gui.GetWindowText(hwnd).lower()
                        if "antigravity" in txt:
                            return True

                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                        if self.browser_pid and pid == self.browser_pid:
                            matched_hwnd = hwnd
                        elif not matched_hwnd and any(k in txt for k in ("cineaegis", "bookmyshow", "ticket", "chrome", "google chrome")):
                            matched_hwnd = hwnd
                return True

            win32gui.EnumWindows(_enum_win, None)
        except Exception as e:
            logger.warning(f"[BROWSER] Window discovery exception: {e}")

        if matched_hwnd:
            self.browser_hwnd = matched_hwnd
            self.window_state["WINDOW_CREATED"] = True
            try:
                import win32gui
                self.window_state["WINDOW_VISIBLE"] = bool(win32gui.IsWindowVisible(matched_hwnd))
            except Exception:
                self.window_state["WINDOW_VISIBLE"] = True
        return matched_hwnd


    def force_foreground(self) -> Dict[str, Any]:
        """Elevates browser window to foreground on the active Windows desktop via system lock bypass."""
        if sys.platform != "win32" or not _user32 or not self.browser_hwnd:
            return {
                "status": "UNAVAILABLE" if sys.platform != "win32" else "NO_WINDOW",
                "WINDOW_CREATED": bool(self.browser_hwnd),
                "WINDOW_VISIBLE": False,
                "FOREGROUND_CONFIRMED": False
            }

        target_hwnd = self.browser_hwnd

        # Check if desktop session is interactive/unlocked
        try:
            import win32api
            desktop = _user32.GetThreadDesktop(_kernel32.GetCurrentThreadId())
            if not desktop:
                return {
                    "status": "BLOCKED",
                    "reason": "DESKTOP_SESSION_UNAVAILABLE",
                    "WINDOW_CREATED": True,
                    "WINDOW_VISIBLE": False,
                    "FOREGROUND_CONFIRMED": False
                }
        except Exception:
            pass

        try:
            # 1. Unlock foreground lock timeout
            SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
            _user32.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, 0, 0x03)
            _user32.AllowSetForegroundWindow(-1)

            # 2. Attach thread input to foreground window
            fg_hwnd = _user32.GetForegroundWindow()
            fg_thread_id = _user32.GetWindowThreadProcessId(fg_hwnd, None)
            cur_thread_id = _kernel32.GetCurrentThreadId()
            attached = False
            if fg_thread_id != cur_thread_id:
                attached = bool(_user32.AttachThreadInput(cur_thread_id, fg_thread_id, True))

            # 3. Pulse Alt key to satisfy Windows focus heuristics
            _user32.keybd_event(0x12, 0, 0, 0)
            _user32.keybd_event(0x12, 0, 2, 0)

            # 4. Restore, Maximize and Bring to Top
            _user32.ShowWindow(target_hwnd, 9)  # SW_RESTORE
            time.sleep(0.05)
            _user32.ShowWindow(target_hwnd, 3)  # SW_SHOWMAXIMIZED
            _user32.BringWindowToTop(target_hwnd)
            _user32.SetForegroundWindow(target_hwnd)

            if attached:
                _user32.AttachThreadInput(cur_thread_id, fg_thread_id, False)

            time.sleep(0.1)
            now_fg = _user32.GetForegroundWindow()
            is_visible = bool(_user32.IsWindowVisible(target_hwnd))
            fg_confirmed = (now_fg == target_hwnd)

            self.window_state.update({
                "WINDOW_CREATED": True,
                "WINDOW_VISIBLE": is_visible,
                "FOREGROUND_CONFIRMED": fg_confirmed
            })

            return {
                "status": "FOREGROUND_CONFIRMED" if fg_confirmed else "WINDOW_VISIBLE",
                "WINDOW_CREATED": True,
                "WINDOW_VISIBLE": is_visible,
                "FOREGROUND_CONFIRMED": fg_confirmed,
                "hwnd": target_hwnd,
                "pid": self.browser_pid
            }
        except Exception as e:
            logger.warning(f"[BROWSER] Error elevating window to foreground: {e}")
            return {
                "status": "ERROR",
                "error": str(e),
                "WINDOW_CREATED": True,
                "WINDOW_VISIBLE": bool(_user32.IsWindowVisible(target_hwnd)) if target_hwnd else False,
                "FOREGROUND_CONFIRMED": False
            }

    async def verify_visibility(self) -> Dict[str, Any]:
        """Verifies full 4-state lifecycle: WINDOW_CREATED, WINDOW_VISIBLE, FOREGROUND_CONFIRMED, PAGE_STATE_VERIFIED."""
        page = self.get_page()
        page_verified = False
        url = ""
        title = ""

        if page and not page.is_closed():
            try:
                url = page.url
                title = await page.title()
                page_verified = bool(url and url != "about:blank")
            except Exception:
                pass

        if self.execution_mode == "background":
            self.window_state = {
                "WINDOW_CREATED": False,
                "WINDOW_VISIBLE": False,
                "FOREGROUND_CONFIRMED": False,
                "PAGE_STATE_VERIFIED": page_verified
            }
            return {
                "mode": "background",
                "status": "BACKGROUND_HEADLESS",
                **self.window_state,
                "url": url,
                "title": title
            }

        await self.discover_browser_window()
        fg_status = self.force_foreground()

        self.window_state["PAGE_STATE_VERIFIED"] = page_verified

        return {
            "mode": "visible",
            "status": "VERIFIED_VISIBLE" if (self.window_state["WINDOW_VISIBLE"] and page_verified) else "PARTIAL",
            **self.window_state,
            "hwnd": self.browser_hwnd,
            "pid": self.browser_pid,
            "url": url,
            "title": title
        }

    def capture_desktop_preview(self, output_path: Optional[str] = None) -> bytes:
        """Captures an actual desktop screen snapshot to verify what is physically displayed on screen."""
        try:
            from PIL import ImageGrab
            import io
            img = ImageGrab.grab()
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            data = buf.getvalue()
            if output_path:
                os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                with open(output_path, "wb") as f:
                    f.write(data)
            return data
        except Exception as e:
            logger.warning(f"[BROWSER] Desktop screenshot capture failed: {e}")
            return b""

    async def pause(self):
        """Pauses interactive execution."""
        self.is_paused = True
        logger.info("[BROWSER] Execution paused by operator.")

    async def resume(self):
        """Resumes interactive execution."""
        self.is_paused = False
        logger.info("[BROWSER] Execution resumed by operator.")

    async def cancel(self):
        """Cancels interactive execution."""
        self.is_cancelled = True
        logger.info("[BROWSER] Execution cancelled by operator.")

    async def wait_if_paused(self):
        """Awaits resumption if paused, or raises cancellation if cancelled."""
        while self.is_paused:
            if self.is_cancelled:
                raise asyncio.CancelledError("Task cancelled by operator.")
            await asyncio.sleep(0.5)
        if self.is_cancelled:
            raise asyncio.CancelledError("Task cancelled by operator.")

    async def launch_browser(
        self,
        headless: Optional[bool] = None,
        execution_mode: str = "visible",
        channel: Optional[str] = None
    ) -> Optional[Page]:
        """Launches or reconnects to an active browser instance with explicit execution modes."""
        if async_playwright is None:
            logger.error("[BROWSER] Playwright is not installed.")
            return None

        self.execution_mode = execution_mode
        if headless is None:
            headless = (execution_mode == "background")

        # Check existing live page
        existing_page = self.get_page()
        if existing_page and not existing_page.is_closed():
            logger.info("[BROWSER] Reusing existing live browser session.")
            if not headless:
                await self.discover_browser_window()
                self.force_foreground()
            return existing_page

        if not self.playwright:
            self.playwright = await async_playwright().start()

        launch_args = [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--start-maximized"
        ]

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
        logger.info(f"[BROWSER] Playwright Chromium online in mode={self.execution_mode} (headless={headless}).")

        if not headless:
            await self.discover_browser_window()
            self.force_foreground()

        return self.page

    async def navigate_to(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30000) -> bool:
        """Navigates active page to target URL with explicit readiness check and foreground activation."""
        await self.wait_if_paused()
        page = self.get_page()
        if not page:
            page = await self.launch_browser(execution_mode=self.execution_mode)
            if not page:
                return False

        if not url.startswith("http://") and not url.startswith("https://") and not url.startswith("file://"):
            url = f"https://{url}"

        try:
            logger.info(f"[BROWSER] Navigating to: {url}")
            await page.goto(url, wait_until=wait_until, timeout=timeout)
            await page.wait_for_timeout(1000)

            if self.execution_mode == "visible":
                await self.discover_browser_window()
                self.force_foreground()

            self.last_action = f"NAVIGATE to {url}"
            self.last_outcome = "SUCCESS"
            return True
        except Exception as e:
            logger.error(f"[BROWSER] Navigation failed to '{url}': {e}")
            self.last_action = f"NAVIGATE to {url}"
            self.last_outcome = f"FAILED: {e}"
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

    async def get_live_status(self) -> Dict[str, Any]:
        """Returns comprehensive real-time execution status, mode, and visibility state."""
        url = await self.get_current_url()
        title = await self.get_title()
        return {
            "execution_mode": self.execution_mode,
            "url": url,
            "title": title,
            "browser_pid": self.browser_pid,
            "browser_hwnd": self.browser_hwnd,
            "is_paused": self.is_paused,
            "is_cancelled": self.is_cancelled,
            "last_action": self.last_action,
            "last_outcome": self.last_outcome,
            "window_state": self.window_state,
        }


browser_driver = PlaywrightBrowserDriver()
