from typing import Dict, Any, List, Optional
try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None

class PlaywrightBrowserDriver:
    """Manages active Playwright browser instance context and page element boundaries."""

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.page = None

    async def launch_browser(self, headless: bool = False):
        if async_playwright is None:
            print("[BROWSER] Playwright is not installed.")
            return None
        if not self.browser:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(headless=headless)
            self.page = await self.browser.new_page()
            print("[BROWSER] Playwright Chromium launched.")
        return self.page

    async def get_active_elements(self) -> List[Dict[str, Any]]:
        """Executes JS in the active browser page to fetch interactive element boundaries."""
        if not self.page:
            return []

        js_script = """
        () => {
            const elements = [];
            const items = document.querySelectorAll('button, input, textarea, a, select, [role="button"]');
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
            elements = await self.page.evaluate(js_script)
            return elements
        except Exception as e:
            print(f"[BROWSER] Failed to parse DOM context elements: {e}")
            return []

    async def close(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        self.browser = None
        self.page = None

browser_driver = PlaywrightBrowserDriver()
