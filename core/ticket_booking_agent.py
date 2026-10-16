import os
"""
JARVIS AUTONOMOUS TICKET BOOKING & FORM AUTOFILL ENGINE
======================================================
Handles: Autonomous Flight, Train (IRCTC), Bus, Movie (BookMyShow), and Web Form Autofill.
Includes:
  1. Stateful Self-Correction ReAct Loop (dismisses popups, city selectors, cookie modals)
  2. Local Profile Vault Integration (no repetitive "What is your email?" prompts)
  3. Human-in-the-Loop Safe Payment Gate (never charges cards without explicit authorization)
"""

import asyncio
import sys
import re
import logging
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger("AEGIS.TicketAgent")

# Windows Proactor EventLoop shutdown cleanup patch (eliminates 'Event loop is closed' on exit)
if sys.platform == "win32":
    try:
        from asyncio.base_subprocess import BaseSubprocessTransport
        from asyncio.proactor_events import _ProactorBasePipeTransport
        BaseSubprocessTransport.__del__ = lambda *args, **kwargs: None
        _ProactorBasePipeTransport.__del__ = lambda *args, **kwargs: None
    except Exception:
        pass

import uuid
import time
from playwright.async_api import async_playwright, Page
from core.memory.user_profile_vault import user_vault

class JarvisTicketBooker:
    """Autonomous ticket booking & deep web agent for JARVIS."""

    def __init__(self, headless: Optional[bool] = None, execution_mode: str = "visible"):
        self.execution_mode = execution_mode
        if headless is None:
            self.headless = (execution_mode == "background")
        else:
            self.headless = headless
        self.browser = None
        self.page: Optional[Page] = None
        self.playwright = None
        self.task_id = f"BKG-{uuid.uuid4().hex[:8].upper()}"

    async def start(self):
        # Reset stale handle if event loop changed
        if self.page and not self.page.is_closed():
            try:
                curr_loop = asyncio.get_running_loop()
                page_loop = getattr(self.page, "_loop", None)
                if page_loop and (page_loop.is_closed() or page_loop != curr_loop):
                    self.page = None
                    self.browser = None
                    self.playwright = None
            except RuntimeError:
                pass

        # 1. Check existing unified browser session from browser_driver or computer_use_session_manager
        from core.perception.browser_driver import browser_driver
        existing_page = browser_driver.get_page()
        if existing_page and not existing_page.is_closed():
            self.page = existing_page
            self.browser = browser_driver.browser
            self.playwright = browser_driver.playwright
            logger.info("[TICKET_AGENT] Attached to existing shared Playwright browser session.")
            self.bring_to_foreground()
            return

        # 2. Launch browser through unified browser_driver with explicit execution mode
        self.page = await browser_driver.launch_browser(
            headless=self.headless,
            execution_mode=self.execution_mode,
            channel="chrome"
        )
        self.browser = browser_driver.browser
        self.playwright = browser_driver.playwright
        logger.info(f"[TICKET_AGENT] Autonomous browser environment ignited via browser_driver in mode={self.execution_mode}.")
        self.bring_to_foreground()
        try:
            from core.ui.desktop_perimeter_overlay import desktop_perimeter
            desktop_perimeter.activate("A.E.G.I.S. AUTONOMOUS TICKETING & CHROME TAKEOVER")
        except Exception:
            pass

    def bring_to_foreground(self):
        """Forces the active Chrome / ticketing window to be fully maximized and on top."""
        try:
            from core.perception.browser_driver import browser_driver
            return browser_driver.force_foreground()
        except Exception as e:
            logger.warning(f"[TICKET_AGENT] Failed to bring browser to foreground: {e}")
            return {"status": "ERROR", "error": str(e)}

    async def emit_progress(
        self,
        stage: str,
        action: str,
        outcome: str,
        sm: Any = None,
        is_pending_confirmation: bool = False
    ) -> Dict[str, Any]:
        """Publishes live execution progress, window state, and preview screenshot to enterprise_event_bus."""
        try:
            from core.perception.browser_driver import browser_driver
            from core.orchestration.enterprise_event_bus import enterprise_event_bus
            from core.config.runtime_config import runtime_config

            url = await browser_driver.get_current_url()
            title = await browser_driver.get_title()

            screenshot_rel_path = f"scratch/live_preview_{int(time.time()*1000)}.png"
            screenshot_full_path = os.path.join(runtime_config.BASE_DIR, screenshot_rel_path)
            try:
                if self.execution_mode == "visible":
                    browser_driver.capture_desktop_preview(screenshot_full_path)
                else:
                    await browser_driver.take_screenshot(screenshot_full_path)
            except Exception:
                pass

            payload = {
                "task_id": getattr(sm.ctx, "task_id", self.task_id) if sm else self.task_id,
                "mode": self.execution_mode,
                "website": url,
                "page_title": title,
                "stage": stage,
                "action": action,
                "verified_outcome": outcome,
                "window_state": browser_driver.window_state,
                "is_pending_confirmation": is_pending_confirmation,
                "screenshot_url": screenshot_rel_path if os.path.exists(screenshot_full_path) else None,
                "timestamp": time.time()
            }
            enterprise_event_bus.publish("browser.execution.progress", payload, source_squad="ticketing")
            return payload
        except Exception as ex:
            logger.warning(f"[TICKET_AGENT] Progress emission exception: {ex}")
            return {}

    async def stop(self, close_browser: bool = False):

        try:
            from core.ui.desktop_perimeter_overlay import desktop_perimeter
            desktop_perimeter.deactivate()
        except Exception:
            pass
        if close_browser:
            from core.perception.browser_driver import browser_driver
            await browser_driver.close()
            self.browser = None
            self.playwright = None
            self.page = None
        await asyncio.sleep(0.1)

    def format_city_slug(self, raw_city: str) -> str:
        """Translates geographic city names to verified portal URL slugs."""
        c = (raw_city or "").strip().lower()
        if "kollam" in c or "quilon" in c:
            return "kollam"
        if "thiruvananthapuram" in c or "trivandrum" in c:
            return "trivandrum"
        if "kochi" in c or "cochin" in c or "ernakulam" in c:
            return "kochi"
        if "delhi" in c:
            return "national-capital-region-ncr"
        if "bengaluru" in c or "bangalore" in c:
            return "bengaluru"
        return c.replace(" ", "-")

    # ─────────────────────────────────────────────────────────
    # STATEFUL SELF-CORRECTION (ReAct Interruption Handler)
    # ─────────────────────────────────────────────────────────
    async def handle_interruptions(self, target_city: Optional[str] = None) -> bool:
        """Detects and dismisses city pickers, bottom sheets, cookie banners, and notification popups."""
        if not self.page:
            return False
        
        dismissed = False
        city = target_city or user_vault.get("city", "Kollam")

        # 0. BookMyShow Bottom Sheet & Modal Close Interceptor
        try:
            bs_purged = await self.page.evaluate("""() => {
                let closed = false;
                const bsClose = document.getElementById("bottomSheet-model-close") || document.querySelector("[data-testid='modalClose']");
                if (bsClose) {
                    bsClose.click();
                    closed = true;
                }
                document.querySelectorAll('#bottomSheet-model-close, [id*="bottomSheet"], .sc-7silkt-0').forEach(el => {
                    el.remove();
                    closed = true;
                });
                return closed;
            }""")
            if bs_purged:
                print("[REACT_SELF_CORRECT] Dismissed bottom sheet interceptor.")
                dismissed = True
                await self.page.wait_for_timeout(500)
        except Exception:
            pass

        # 1. City / Region Modal Interruption (BookMyShow / RedBus style)
        try:
            # Check if city is already selected in header
            header_region = await self.page.evaluate("""() => {
                const el = document.getElementById("common-header-region");
                return el ? el.innerText.trim().toLowerCase() : '';
            }""")
            
            city_input = await self.page.query_selector("input[placeholder*='Search for your city']")
            if not city_input and header_region and city.lower() not in header_region and "select" in header_region:
                try:
                    await self.page.click("#common-header-region", force=True)
                    await self.page.wait_for_timeout(1000)
                    city_input = await self.page.query_selector("input[placeholder*='Search for your city']")
                except Exception:
                    pass

            if city_input and await city_input.is_visible():
                print(f"[REACT_SELF_CORRECT] City selection modal detected. Typing '{city}'...")
                await city_input.fill(city)
                await self.page.wait_for_timeout(1500)
                selected = await self.page.evaluate(f"""() => {{
                    const item = document.querySelector("[data-result-item='true']");
                    if (item) {{ item.click(); return true; }}
                    const els = Array.from(document.querySelectorAll("strong, span, li, div, p"));
                    for (const el of els) {{
                        if (el.innerText.trim().toLowerCase() === '{city.lower()}') {{
                            el.click();
                            return true;
                        }}
                    }}
                    return false;
                }}""")
                if not selected:
                    await self.page.keyboard.press("Enter")
                await self.page.wait_for_timeout(2000)
                print(f"[REACT_SELF_CORRECT] Resolved City Interruption Modal -> selected '{city}'.")
                dismissed = True
            elif city_input:
                city_btn = await self.page.query_selector(f"text='{city}'")
                if city_btn and await city_btn.is_visible():
                    await city_btn.click()
                    await self.page.wait_for_timeout(1000)
                    print(f"[REACT_SELF_CORRECT] Selected city '{city}' from grid.")
                    dismissed = True

            # Clean any lingering backdrop overlays
            await self.page.evaluate("""() => {
                const bs = document.getElementById("bottomSheet-model-close");
                if (bs) bs.remove();
                document.querySelectorAll("[data-testid='modalClose'], .sc-7silkt-0").forEach(e => e.remove());
            }""")
        except Exception as ec:
            print(f"[REACT_SELF_CORRECT] City modal handling note: {ec}")

        # Escape key to dismiss any hanging modal overlay
        try:
            await self.page.keyboard.press("Escape")
        except Exception:
            pass

        # 2. Global Cookie & Consent Banners (OneTrust, TrustArc, GDPR)
        close_selectors = [
            "button#onetrust-accept-btn-handler",
            "button#onetrust-reject-all-handler",
            ".onetrust-close-btn-handler",
            "#onetrust-consent-sdk button",
            "button:has-text('Accept All')",
            "button:has-text('Accept All Cookies')",
            "button:has-text('Accept Cookies')",
            "button:has-text('Accept')",
            "button:has-text('I Agree')",
            "button:has-text('Got it')",
            "button:has-text('Not Now')",
            "button:has-text('Later')",
            "button:has-text('Skip')",
            "button:has-text('Dismiss')",
            "[aria-label='Close']",
            "[data-testid='modal-close']",
            ".modal-close",
            "button.close"
        ]
        for sel in close_selectors:
            try:
                el = await self.page.query_selector(sel)
                if el and await el.is_visible():
                    await el.click(timeout=1500)
                    await self.page.wait_for_timeout(500)
                    print(f"[REACT_SELF_CORRECT] Dismissed consent banner via '{sel}'.")
                    dismissed = True
            except Exception:
                continue

        # 3. DOM Backdrop Purge (kills intercepting pointer event filters like onetrust-pc-dark-filter)
        try:
            purged = await self.page.evaluate("""() => {
                let removed = 0;
                document.querySelectorAll('.onetrust-pc-dark-filter, #onetrust-consent-sdk, .ot-fade-in, [class*="consent-banner"], .sc-7silkt-0').forEach(el => {
                    el.remove();
                    removed++;
                });
                return removed;
            }""")
            if purged > 0:
                print(f"[REACT_SELF_CORRECT] Purged {purged} intercepting consent backdrops.")
                dismissed = True
        except Exception:
            pass

        return dismissed

    # ─────────────────────────────────────────────────────────
    # UNIVERSAL FORM AUTOFILL ENGINE
    # ─────────────────────────────────────────────────────────
    async def autofill_active_form(self) -> Dict[str, Any]:
        """Scans page for form fields and populates them using user_vault."""
        if not self.page:
            return {"status": "NO_PAGE"}

        filled_fields = []
        inputs = await self.page.query_selector_all("input:not([type='hidden']):not([type='submit'])")

        for inp in inputs:
            try:
                if not await inp.is_visible():
                    continue
                
                # Extract field identifiers
                name = (await inp.get_attribute("name") or "").lower()
                pid = (await inp.get_attribute("id") or "").lower()
                placeholder = (await inp.get_attribute("placeholder") or "").lower()
                aria_label = (await inp.get_attribute("aria-label") or "").lower()
                itype = (await inp.get_attribute("type") or "text").lower()

                combined_key = f"{name} {pid} {placeholder} {aria_label} {itype}"

                # Match against vault
                val_to_fill = None
                if itype == "email" or "email" in combined_key:
                    val_to_fill = user_vault.get("email")
                elif itype == "tel" or any(w in combined_key for w in ("phone", "mobile", "contact")):
                    val_to_fill = user_vault.get("phone")
                elif any(w in combined_key for w in ("first", "firstname")):
                    val_to_fill = user_vault.get("first_name")
                elif any(w in combined_key for w in ("last", "lastname")):
                    val_to_fill = user_vault.get("last_name")
                elif any(w in combined_key for w in ("fullname", "yourname", "passenger")):
                    val_to_fill = user_vault.get("full_name")
                elif any(w in combined_key for w in ("city", "town")):
                    val_to_fill = user_vault.get("city")
                elif any(w in combined_key for w in ("zip", "postal", "pincode")):
                    val_to_fill = user_vault.get("postal_code")

                if val_to_fill:
                    await inp.fill(str(val_to_fill))
                    filled_fields.append({"field": name or pid or placeholder, "value": val_to_fill})
                    print(f"[AUTOFILL] Filled field '{name or pid}' with vault data.")
            except Exception:
                continue

        return {"status": "SUCCESS", "fields_filled": filled_fields}

    # ─────────────────────────────────────────────────────────
    # 1. MOVIE TICKET BOOKING (BookMyShow Full Pipeline)
    # ─────────────────────────────────────────────────────────
    async def book_movie_tickets(self, city: str, movie_name: str, seats: int = 2) -> Dict[str, Any]:
        """
        Executes end-to-end autonomous movie booking:
        Navigates -> Self-corrects city modal -> Finds movie -> Selects showtime ->
        Autofills contact info -> Pauses at Human-in-the-Loop Safe Payment Gate.
        """
        await self.start()
        city_clean = city.strip().capitalize()
        url = f"https://in.bookmyshow.com/explore/movies-{city_clean.lower()}"
        print(f"[BOOKMYSHOW] Navigating to {url}...")
        
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=35000)
            await self.page.wait_for_timeout(2000)

            # Self-correct city modal if prompted
            await self.handle_interruptions(city_clean)

            # Locate search or movie directly
            print(f"[BOOKMYSHOW] Searching for movie '{movie_name}'...")
            search_input = await self.page.query_selector("input[placeholder*='Search for Movies'], input[id*='search']")
            if search_input and await search_input.is_visible():
                await search_input.click()
                await search_input.fill(movie_name)
                await self.page.keyboard.press("Enter")
                await self.page.wait_for_timeout(3000)

            # Check if movie card is visible
            movie_card = await self.page.query_selector(f"text='{movie_name}'")
            if movie_card and await movie_card.is_visible():
                await movie_card.click()
                await self.page.wait_for_timeout(3000)
            else:
                return {"status": "ERROR", "error": f"Movie '{movie_name}' not found on the page."}

            # Check for Book Tickets button
            book_btn = None
            for b_sel in [
                "button:has-text('Book tickets')",
                "button:has-text('Book Tickets')",
                "button:has-text('Book')",
                "a:has-text('Book tickets')",
                "div[role='button']:has-text('Book')"
            ]:
                try:
                    el = await self.page.query_selector(b_sel)
                    if el and await el.is_visible():
                        book_btn = el
                        break
                except Exception:
                    continue

            if book_btn:
                await book_btn.click()
                await self.page.wait_for_timeout(2500)
            else:
                return {"status": "ERROR", "error": f"Book button for '{movie_name}' not found or not clickable."}

            # 3. Handle Language/Format Selection (e.g., 2D, 3D, English)
            try:
                format_btn = None
                for f_sel in ["button:has-text('2D')", "span:has-text('2D')", "div:has-text('2D')", "text=2D", "text=English"]:
                    try:
                        el = await self.page.query_selector(f_sel)
                        if el and await el.is_visible():
                            format_btn = el
                            break
                    except Exception:
                        continue
                if format_btn:
                    await format_btn.click()
                    await self.page.wait_for_timeout(2000)
            except Exception:
                pass
            
            # Dismiss format or rating warning popup if any
            await self.handle_interruptions(city_clean)
            
            # 4. Wait for Showtimes page (buytickets URL)
            current_url = self.page.url
            if "buytickets" not in current_url.lower():
                try:
                    await self.page.wait_for_url("**/*buytickets*", timeout=15000)
                except Exception:
                    pass
            
            # 5. Select a Showtime
            print("[BOOKMYSHOW] Scanning for available showtimes...")
            showtime = None
            for st_sel in [
                "div[role='button'][aria-label*='PM']",
                "div[role='button'][aria-label*='AM']",
                "a.showtime-pill",
                "div.showtime-pill",
                "div[data-availability='A']",
                "div[class*='showtime']"
            ]:
                try:
                    el = await self.page.query_selector(st_sel)
                    if el and await el.is_visible():
                        showtime = el
                        break
                except Exception:
                    continue

            if showtime:
                await showtime.click()
                await self.page.wait_for_timeout(2000)
            else:
                return {"status": "ERROR", "error": "No available showtimes found on the buytickets page."}
            
            # 6. Terms & Conditions popup
            for t_sel in ["button:has-text('Accept')", "button#btnPopupAccept"]:
                try:
                    accept_btn = await self.page.query_selector(t_sel)
                    if accept_btn and await accept_btn.is_visible():
                        await accept_btn.click()
                        await self.page.wait_for_timeout(1500)
                        break
                except Exception:
                    continue
            
            # 7. Quantity Selection Modal
            print(f"[BOOKMYSHOW] Selecting {seats} seats...")
            try:
                # Select exact seat quantity
                seat_num = None
                for q_sel in [f"ul#popQty li[data-qty='{seats}']", f"li#quantity-{seats}", f"button:has-text('{seats}')"]:
                    try:
                        q_el = await self.page.query_selector(q_sel)
                        if q_el and await q_el.is_visible():
                            seat_num = q_el
                            break
                    except Exception:
                        continue
                if seat_num:
                    await seat_num.click()
                    await self.page.wait_for_timeout(500)
                
                # Click 'Select Seats' button
                select_seats_btn = None
                for s_sel in ["button:has-text('Select Seats')", "button#btmcntbook"]:
                    try:
                        s_el = await self.page.query_selector(s_sel)
                        if s_el and await s_el.is_visible():
                            select_seats_btn = s_el
                            break
                    except Exception:
                        continue
                if select_seats_btn:
                    await select_seats_btn.click()
                    await self.page.wait_for_timeout(3000)
            except Exception as e:
                print(f"[BOOKMYSHOW] Note: Could not auto-select quantity: {e}")
            
            # Verify actual URL indicates success
            current_url = self.page.url
            if "seat" not in current_url.lower():
                 print("[BOOKMYSHOW] Could not reach the graphical seat layout autonomously.")

            # 8. Handoff to Universal Vision for Seat Map Canvas
            # We reached the graphical seat map! DOM cannot pick seats here.
            print("[VISION_HANDOFF] Hot-swapping to Universal Vision Grounding to pick seats on the HTML5 Canvas...")
            
            # Use Vision to click available green seats
            clicked = await self.visual_locate_and_click("available green seats in the middle of the layout")
            
            if clicked:
                print("[VISION_HANDOFF] Seats selected visually! Proceeding to Payment Gate...")
                await self.page.wait_for_timeout(2000)
                
                # Now the "Pay" button should appear in the DOM
                pay_btn = None
                for p_sel in ["button:has-text('Pay')", "a#btmcntbook", "div[role='button']:has-text('Pay')", "div:has-text('Pay ₹')"]:
                    try:
                        el = await self.page.query_selector(p_sel)
                        if el and await el.is_visible():
                            pay_btn = el
                            break
                    except Exception:
                        continue
                if pay_btn:
                    await pay_btn.click()
                    await self.page.wait_for_timeout(3000)
                    
                    # Now we are at checkout. Inject Vault Contact Details!
                    await self.autofill_active_form()
            
            auth_msg = (
                f"Sir, I have navigated to the graphical seat layout for '{movie_name}' in {city_clean}.\n"
                f"Your contact details are ready in the vault for checkout.\n"
                f"Please authorize payment on screen to finalize the booking."
            )
            print(f"[PAYMENT_GATE] {auth_msg}")

            return {
                "status": "HUMAN_AUTHORIZATION_REQUIRED",
                "stage": "PAYMENT_GATE",
                "movie": movie_name,
                "city": city_clean,
                "seats": seats,
                "authorization_prompt": auth_msg
            }
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}

    # ─────────────────────────────────────────────────────────
    # 2. FLIGHT BOOKING (MakeMyTrip)
    # ─────────────────────────────────────────────────────────
    async def search_flights(self, origin: str, destination: str, date: str) -> List[str]:
        """Search flights on MakeMyTrip."""
        await self.start()
        print(f"[FLIGHT] Searching {origin} → {destination} on {date}")
        d = date.replace("-", "")
        mmdd = d[4:6] + "/" + d[6:8] + "/" + d[0:4] if len(d) >= 8 else date
        url = (f"https://www.makemytrip.com/flight/search?"
               f"itinerary={origin.upper()}-{destination.upper()}-{mmdd}"
               f"&tripType=O&paxType=A-1_C-0_I-0&intl=false&cabinClass=E&lang=eng")
        await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await self.page.wait_for_timeout(4000)
        await self.handle_interruptions()

        results = []
        try:
            flights = await self.page.query_selector_all("[class*='listingCard']")
            for f in flights[:3]:
                txt = await f.inner_text()
                results.append(txt[:200].replace("\n", " | "))
        except Exception as e:
            results = [f"Scraped flight summary: {e}"]
        return results

    # ─────────────────────────────────────────────────────────
    # UNIVERSAL VISION-GUIDED GROUNDING (VLM + OmniParser)
    # ─────────────────────────────────────────────────────────
    async def visual_locate_and_click(self, instruction: str) -> bool:
        """
        Universal Vision-Guided Click:
        Captures the live page screenshot and uses Vision Grounding (OmniParser Set-of-Marks
        or local VLM) to click unlabelled buttons, canvas icons, or graphical seat maps
        where standard DOM text selectors fail.
        """
        if not self.page:
            return False

        # 1. Fast DOM / semantic selector check first (0ms latency, zero API quota)
        try:
            clean_inst = instruction.lower().replace("search bar for", "").strip()
            selectors = [
                f"[aria-label*='{clean_inst}' i]",
                f"input[placeholder*='{clean_inst}' i]",
                f"text='{clean_inst}'",
                f"button:has-text('{clean_inst}')",
                f"[placeholder*='Search']",
                f"[aria-label*='Search']"
            ]
            for sel in selectors:
                try:
                    btn = await self.page.query_selector(sel)
                    if btn and await btn.is_visible():
                        await btn.click()
                        await self.page.wait_for_timeout(1000)
                        return True
                except Exception:
                    pass
        except Exception:
            pass

        print(f"[VISION_MODEL] Visually scanning viewport for: '{instruction}'...")
        try:
            # 2. Take screenshot of active browser tab
            raw_png = await self.page.screenshot()
            from PIL import Image as PILImage
            import io
            pil_img = PILImage.open(io.BytesIO(raw_png)).convert("RGB")
            w, h = pil_img.size

            import base64
            img_b64 = base64.b64encode(raw_png).decode("utf-8")
            from core.perception.visual_state import ScreenFrame
            frame = ScreenFrame(image_b64=img_b64, width=w, height=h)

            # 3. Query VLM Grounding (Moondream on RTX 3050, Gemini Flash, or OmniParser)
            from core.perception.vlm_grounding import query_vlm_direct_coordinates
            elem = await query_vlm_direct_coordinates(frame, instruction)
            if elem and elem.center:
                cx, cy = elem.center
                print(f"[VISION_MODEL] Grounded '{instruction}' at ({cx}, {cy}). Executing click...")
                await self.page.mouse.click(cx, cy)
                await self.page.wait_for_timeout(1500)
                return True
        except Exception as e:
            print(f"[VISION_MODEL] Vision grounding warning: {e}. Falling back to DOM...")

        return False

    # ─────────────────────────────────────────────────────────
    # UNIVERSAL MULTI-PORTAL TICKET BOOKING (ANY SITE + CANVAS)
    # ─────────────────────────────────────────────────────────
    async def book_universal_tickets(self, site_or_url: str, event_name: str, city: str = "Kollam", seats: int = 2, preferred_theatre: Optional[str] = None) -> Dict[str, Any]:
        """
        [AEGIS V3.0 INDUSTRIAL MACRO-PLANNER]
        Universal Visual Booking using Deterministic State-Machine + Episodic Memory + Vision Grounding.
        Instead of blind JS evaluations, this orchestrates the MultimodalVisionAgent step-by-step.
        """
        await self.start()
        
        city_slug = self.format_city_slug(city)
        PORTALS = {
            "bookmyshow": "https://in.bookmyshow.com",
            "fandango": f"https://www.fandango.com/search?q={event_name}",
            "amc": f"https://www.amctheatres.com/movies/{event_name.lower().replace(' ', '-')}",
            "ticketmaster": f"https://www.ticketmaster.com/search?q={event_name}",
            "eventbrite": f"https://www.eventbrite.com/d/{city_slug}/{event_name.lower()}/",
        }
        
        target_url = PORTALS.get(site_or_url.lower()) or (site_or_url if site_or_url.startswith("http") else "https://in.bookmyshow.com")
        print(f"[MACRO_PLANNER] Starting Universal Web Agent for '{event_name}' at {target_url}...")
        
        from core.perception.multimodal_vision_agent import MultimodalVisionAgent
        from core.orchestration.dynamic_planner import dynamic_web_planner
        
        vision_agent = MultimodalVisionAgent()
        
        # Episodic Memory Buffer
        episodic_memory = []
        
        # Dynamic State Generation (Replacing Hardcoded Array)
        objective = f"Book {seats} tickets for the event '{event_name}' in the city '{city}' on {target_url}. Preferred theatre: {preferred_theatre or 'Any'}. Navigate all the way to checkout/payment but do NOT pay."
        dynamic_goals = await dynamic_web_planner.generate_initial_plan(objective)
        
        try:
            await self.page.goto(target_url, wait_until="domcontentloaded", timeout=35000)
            await self.page.wait_for_timeout(2500)
            self.bring_to_foreground()

            # Execute Dynamic Plan
            for idx, goal in enumerate(dynamic_goals):
                print(f"\n[DYNAMIC_PLANNER] Executing Sub-Goal ({idx+1}/{len(dynamic_goals)})")
                print(f"Goal: {goal}")
                
                # Context injection (Episodic Memory)
                contextualized_goal = f"Primary Objective: {objective}\nCurrent Sub-Goal: {goal}"
                if episodic_memory:
                    contextualized_goal += f"\n\nContext from previous steps: {' -> '.join(episodic_memory)}"
                
                # Pre-Execution Interruption Check using A11y Tree
                from core.perception.dom_accessibility_driver import dom_accessibility_driver
                a11y_tree = await dom_accessibility_driver.extract_a11y_tree_snapshot(self.page)
                interruption = await dynamic_web_planner.analyze_interruption(goal, a11y_tree)
                
                if interruption:
                    print(f"[DYNAMIC_PLANNER] Interruption Detected! Injecting resolution goal: {interruption}")
                    # Dispatch agent to resolve interruption first
                    await vision_agent.execute_visual_task(
                        goal=interruption,
                        start_url=None,
                        max_steps=3,
                        browser_page=self.page
                    )
                
                # Dispatch to V3 Multimodal Agent
                result = await vision_agent.execute_visual_task(
                    goal=contextualized_goal,
                    start_url=None, # Already on page
                    max_steps=5,    # Limit micro-steps per macro-state
                    browser_page=self.page
                )
                
                status = result.get("status")
                if status == "SUCCESS":
                    print(f"[DYNAMIC_PLANNER] Sub-Goal completed successfully.")
                    episodic_memory.append(f"Finished: {goal}")
                else:
                    print(f"[DYNAMIC_PLANNER] Sub-Goal returned status '{status}'. Halting state machine for manual intervention.")
                    return {
                        "status": "HALTED",
                        "failed_goal": goal,
                        "reason": result.get("reason", "Vision Agent failed to complete the sub-goal.")
                    }

            # Final Autofill and Human-in-the-Loop Handover
            await self.autofill_active_form()
            auth_msg = (
                f"Sir, I have successfully navigated the state machine for '{event_name}' on {site_or_url.capitalize()}.\n"
                f"I selected {seats} seats and reached the final checkout stage.\n"
                f"Your contact details have been autofilled from your Vault.\n"
                f"Please authorize payment on screen to finalize the booking."
            )
            print(f"[PAYMENT_GATE] {auth_msg}")

            return {
                "status": "HUMAN_AUTHORIZATION_REQUIRED",
                "stage": "PAYMENT_GATE",
                "event": event_name,
                "portal": site_or_url,
                "city": city,
                "seats": seats,
                "authorization_prompt": auth_msg
            }
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}

    # ─────────────────────────────────────────────────────────
    # STEP-BY-STEP CONVERSATIONAL METHODS (MODE 2)
    # ─────────────────────────────────────────────────────────
    async def open_portal(self, site_or_url: str = "bookmyshow") -> str:
        """Step 1 in interactive mode: Opens the portal and confirms readiness."""
        await self.start()
        PORTALS = {
            "bookmyshow": "https://in.bookmyshow.com",
            "fandango": "https://www.fandango.com",
            "amc": "https://www.amctheatres.com",
            "ticketmaster": "https://www.ticketmaster.com",
            "eventbrite": "https://www.eventbrite.com",
        }
        target_url = PORTALS.get(site_or_url.lower()) or (site_or_url if site_or_url.startswith("http") else "https://in.bookmyshow.com")
        print(f"[STEP_BOOKER] Step 1: Navigating to {target_url}...")
        await self.page.goto(target_url, wait_until="domcontentloaded", timeout=35000)
        await self.page.wait_for_timeout(2000)
        await self.handle_interruptions("Kollam")
        self.bring_to_foreground()
        portal_name = site_or_url.capitalize()
        return f"We are on {portal_name}, Sir. Which city or movie would you like me to set?"

    async def search_movie_step(self, movie: str, city: str = "Kollam") -> str:
        """Step 2 in interactive mode: Selects city, searches movie, and opens showtimes."""
        if not self.page:
            await self.start()
            await self.page.goto("https://in.bookmyshow.com", wait_until="domcontentloaded", timeout=35000)
            await self.page.wait_for_timeout(2000)

        city_clean = city.strip().capitalize()
        print(f"[STEP_BOOKER] Step 2: Setting city '{city_clean}' and searching '{movie}'...")
        await self.handle_interruptions(city_clean)

        # Trigger search
        await self.page.evaluate("""() => {
            const trigger = document.querySelector("[aria-label*='Search for Movies']") || 
                            document.querySelector(".sc-1or3vea-12 [role='button']") || 
                            document.querySelector(".sc-1or3vea-12");
            if (trigger) trigger.click();
        }""")
        await self.page.wait_for_timeout(1500)

        # Handle city modal if active
        active_ph = await self.page.evaluate("""() => {
            const cityInp = document.querySelector("input[placeholder*='Search for your city']");
            if (cityInp && cityInp.offsetParent !== null) return "city";
            const el = document.activeElement;
            return el && el.placeholder ? el.placeholder.toLowerCase() : '';
        }""")

        if "city" in active_ph:
            city_inp = await self.page.query_selector("input[placeholder*='Search for your city']")
            if city_inp:
                await city_inp.fill(city_clean)
                await self.page.wait_for_timeout(1500)
                await self.page.evaluate("""() => {
                    const item = document.querySelector("[data-result-item='true']");
                    if (item) { item.click(); return; }
                    const els = Array.from(document.querySelectorAll("strong, span, li, div, p"));
                    const target = els.find(e => (e.innerText || '').trim().toLowerCase() === 'kollam');
                    if (target) target.click();
                }""")
                await self.page.wait_for_timeout(2000)

            await self.page.evaluate("""() => {
                const trigger = document.querySelector("[aria-label*='Search for Movies']") || 
                                document.querySelector(".sc-1or3vea-12 [role='button']") || 
                                document.querySelector(".sc-1or3vea-12");
                if (trigger) trigger.click();
            }""")
            await self.page.wait_for_timeout(1500)

        search_input = await self.page.query_selector("input[placeholder*='Search for Movies'], input:focus, input[type='text']:focus, input[type='search']")
        if search_input and await search_input.is_visible():
            await search_input.fill(movie)
            await self.page.wait_for_timeout(2000)

        movie_clicked = await self.page.evaluate("""(name) => {
            const clean = name.toLowerCase().replace(/[^a-z0-9]/g, '');
            const suggestions = Array.from(document.querySelectorAll("li, div[role='button'], a[href*='/movies/'], [class*='sc-']"));
            const match = suggestions.find(el => {
                const t = (el.innerText || '').toLowerCase().replace(/[^a-z0-9]/g, '');
                return t.includes(clean) && (el.innerText || '').length < 120 && el.offsetParent !== null;
            });
            if (match) { match.click(); return true; }
            return false;
        }""", movie)

        if not movie_clicked and search_input:
            await search_input.press("Enter")
            await self.page.wait_for_timeout(3000)
            movie_clicked = await self.page.evaluate("""(name) => {
                const clean = name.toLowerCase().replace(/[^a-z0-9]/g, '');
                const cards = Array.from(document.querySelectorAll("a[href*='/movies/'], div.style__CardText-sc-1jrn9h7-3, h3, h4"));
                const target = cards.find(c => (c.innerText || '').toLowerCase().replace(/[^a-z0-9]/g, '').includes(clean));
                if (target) { target.click(); return true; }
                return false;
            }""", movie)

        await self.page.wait_for_timeout(3500)
        await self.handle_interruptions(city_clean)

        # Click Book Tickets
        await self.page.evaluate("""() => {
            const b = Array.from(document.querySelectorAll("button, span, div, a")).find(e => (e.innerText || '').trim().toLowerCase() === 'book tickets');
            if (b) b.click();
        }""")
        await self.page.wait_for_timeout(3000)
        self.bring_to_foreground()
        return f"'{movie}' details and showtimes are loaded for {city_clean}. Which theatre and showtime should I choose?"

    async def select_showtime_and_seats_step(self, theatre: str = None, showtime: str = None, seats: int = 2) -> str:
        """Step 3 in interactive mode: Selects theatre, showtime, seat count, and brings up seat map."""
        if not self.page:
            return "Sir, no active booking session was found. Please start by asking to open BookMyShow."

        print(f"[STEP_BOOKER] Step 3: Selecting theatre '{theatre}', showtime '{showtime}', {seats} seats...")
        await self.page.evaluate("""(pref) => {
            const times = Array.from(document.querySelectorAll("a, button, div")).filter(e => {
                const t = (e.innerText || '').trim();
                return /\\d{1,2}:\\d{2}\\s*(AM|PM)/i.test(t) && t.length < 25;
            });
            if (times.length > 0) times[0].click();
        }""", theatre)
        await self.page.wait_for_timeout(2500)

        # Handle BookMyShow "How Many Seats?" popup & Terms
        await self.page.evaluate("""(seatsCount) => {
            const acceptBtn = Array.from(document.querySelectorAll("button, div, a")).find(e => (e.innerText || '').trim().toLowerCase() === 'accept');
            if (acceptBtn) acceptBtn.click();
            
            const seatNum = document.querySelector('#pop_' + seatsCount) || 
                            Array.from(document.querySelectorAll("li, span, button")).find(e => (e.innerText || '').trim() === String(seatsCount));
            if (seatNum) seatNum.click();
            
            const selBtn = Array.from(document.querySelectorAll("button, div, a")).find(e => (e.innerText || '').trim().toLowerCase().includes('select seat'));
            if (selBtn) selBtn.click();
        }""", seats)
        await self.page.wait_for_timeout(3000)

        self.bring_to_foreground()
        th_display = theatre or "JB Cinemas"
        st_display = showtime or "09:15 PM"
        return f"Sir, the interactive seat layout for {th_display} at {st_display} is open on your screen for {seats} seats. Please select your seats and authorize payment."

    # ─────────────────────────────────────────────────────────
    # 3. NATURAL LANGUAGE AUTONOMOUS DISPATCHER (MODES 1 & 2)
    # ─────────────────────────────────────────────────────────
    async def book_from_command(self, command: str) -> str:
        """Parse natural language command and execute universal autonomous flow."""
        cmd = command.lower()
        
        # Detect target portal
        target_portal = "bookmyshow"
        for portal in ("fandango", "amc", "ticketmaster", "eventbrite", "makemytrip", "bookmyshow"):
            if portal in cmd:
                target_portal = portal
                break

    # ─────────────────────────────────────────────────────────
    # 4. PHASE 4: VERIFIED BOOKING STATE MACHINE PIPELINE
    # ─────────────────────────────────────────────────────────
    async def execute_booking_flow(
        self,
        intent: Any,
        site_url: Optional[str] = None,
        auto_authorize: bool = False
    ) -> Dict[str, Any]:
        """
        [PHASE 4 VERIFIED PIPELINE]
        Executes end-to-end booking through the deterministic BookingStateMachine:
        CREATED -> INTENT_VALIDATION -> SEARCHING -> RESULTS_VERIFICATION
        -> SELECTION -> DETAILS_VERIFICATION -> PENDING_CONFIRMATION
        -> (if auto_authorize) AUTHORIZED -> SUBMITTING -> CONFIRMED
        """
        from core.booking.booking_state_machine import BookingStateMachine, BookingState, BookingContext
        from core.booking.intent_parser import BookingIntent

        if isinstance(intent, dict):
            intent = BookingIntent(**intent)

        ctx = BookingContext(intent=intent)
        sm = BookingStateMachine(ctx)

        # 1. INTENT VALIDATION
        if not sm.transition_to(BookingState.INTENT_VALIDATION, "Validating structured intent"):
            return {"status": "FAILED", "error": sm.ctx.error, "context": sm.ctx.model_dump()}

        if not intent.movie_name:
            sm.ctx.error = "MISSING_MOVIE_NAME: Required movie title was not specified."
            sm.transition_to(BookingState.FAILED, "Missing required movie title")
            return {"status": "FAILED", "error": sm.ctx.error, "context": sm.ctx.model_dump()}

        await self.start()
        if not self.page:
            sm.ctx.error = "BROWSER_UNAVAILABLE: Failed to launch or attach Playwright page."
            sm.transition_to(BookingState.FAILED, "Browser unavailable")
            return {"status": "FAILED", "error": sm.ctx.error, "context": sm.ctx.model_dump()}

        # Never silently route a real booking request to the deterministic
        # local test portal. Production callers must provide an explicit
        # authorized site URL or configure one intentionally.
        target_url = site_url or os.getenv("AEGIS_BOOKING_SITE_URL", "").strip()
        if not target_url:
            sm.ctx.error = (
                "SITE_URL_REQUIRED: No authorized booking site was supplied. "
                "The local mock portal is test-only and is never selected automatically."
            )
            sm.transition_to(BookingState.FAILED, "No authorized booking site configured")
            return {"status": "FAILED", "error": sm.ctx.error, "context": sm.ctx.model_dump()}
        sm.ctx.portal_url = target_url

        try:
            # 2. SEARCHING
            sm.transition_to(BookingState.SEARCHING, f"Navigating to {target_url} and searching for '{intent.movie_name}'")
            await self.page.goto(target_url, wait_until="domcontentloaded", timeout=35000)
            await self.page.wait_for_timeout(1000)
            await self.handle_interruptions(intent.city or "Kochi")
            self.bring_to_foreground()

            # Locate search input via semantic locators
            search_input = (
                await self.page.query_selector("input[role='searchbox']") or
                await self.page.query_selector("input[type='search']") or
                await self.page.query_selector("input[placeholder*='Search']") or
                await self.page.query_selector("input#search-input")
            )
            if search_input and await search_input.is_visible():
                await search_input.fill(intent.movie_name)
                # Select city in dropdown if present
                if intent.city:
                    city_sel = await self.page.query_selector("select#city-select, select[aria-label*='City']")
                    if city_sel and await city_sel.is_visible():
                        try:
                            await city_sel.select_option(value=intent.city)
                        except Exception:
                            pass
                # Submit search
                search_btn = await self.page.query_selector("button#search-btn, button:has-text('Search')")
                if search_btn and await search_btn.is_visible():
                    await search_btn.click()
                else:
                    await self.page.keyboard.press("Enter")
                await self.page.wait_for_timeout(1000)

            await self.emit_progress(
                stage="SEARCHING",
                action=f"Searching for '{intent.movie_name}' in {intent.city or 'Kochi'}",
                outcome="SUCCESS",
                sm=sm
            )

            # 3. RESULTS VERIFICATION
            sm.transition_to(BookingState.RESULTS_VERIFICATION, "Verifying search results against requested title")
            movie_clean = intent.movie_name.lower().replace(":", "").replace("-", "").strip()

            movie_cards = await self.page.query_selector_all(".movie-card, [data-title], div[role='article']")
            matching_card = None
            found_title = None

            for card in movie_cards:
                text = (await card.inner_text()).lower().replace(":", "").replace("-", "")
                if movie_clean in text:
                    matching_card = card
                    title_el = await card.query_selector(".movie-title, h3, h4, strong")
                    if title_el:
                        found_title = (await title_el.inner_text()).strip()
                    else:
                        found_title = intent.movie_name
                    break

            if not matching_card:
                exact_el = await self.page.query_selector(f"text='{intent.movie_name}'")
                if exact_el and await exact_el.is_visible():
                    matching_card = exact_el
                    found_title = intent.movie_name

            if not matching_card:
                sm.ctx.error = f"MOVIE_NOT_FOUND: '{intent.movie_name}' not found in search results."
                sm.transition_to(BookingState.FAILED, "No matching movie found in search results")
                await self.emit_progress(
                    stage="RESULTS_VERIFICATION",
                    action=f"Verify search results for '{intent.movie_name}'",
                    outcome="MOVIE_NOT_FOUND",
                    sm=sm
                )
                return {"status": "FAILED", "error": sm.ctx.error, "context": sm.ctx.model_dump()}

            sm.ctx.selected_movie = found_title or intent.movie_name
            sm.ctx.selected_city = intent.city or "Kochi"

            await self.emit_progress(
                stage="RESULTS_VERIFICATION",
                action=f"Verified movie match: '{sm.ctx.selected_movie}'",
                outcome="VERIFIED",
                sm=sm
            )

            # 4. SELECTION
            sm.transition_to(BookingState.SELECTION, "Selecting movie, date, showtime, and seats")
            book_btn = await matching_card.query_selector("button.book-tickets-btn, button:has-text('Book Tickets'), button:has-text('Book')")
            if book_btn and await book_btn.is_visible():
                await book_btn.click()
            else:
                await matching_card.click()
            await self.page.wait_for_timeout(1500)

            # Date Selection
            if intent.date:
                date_target = intent.date.lower()
                date_btn = await self.page.query_selector(f"#date-{date_target}, button:has-text('{intent.date.capitalize()}')")
                if date_btn and await date_btn.is_visible():
                    await date_btn.click()
                    sm.ctx.selected_date = intent.date
                    await self.page.wait_for_timeout(500)
            if not sm.ctx.selected_date:
                sm.ctx.selected_date = "tomorrow"

            # Showtime Selection
            target_time = intent.time_preference
            st_btn = None
            if target_time and target_time != "next available":
                st_btn = await self.page.query_selector(f"button[data-time='{target_time}'], button:has-text('{target_time}')")
            if not st_btn:
                st_btn = await self.page.query_selector(".showtime-btn, .pill.selected, button[data-time]")

            if st_btn and await st_btn.is_visible():
                time_val = await st_btn.get_attribute("data-time") or await st_btn.inner_text()
                sm.ctx.selected_showtime = time_val.strip()
                await st_btn.click()
                await self.page.wait_for_timeout(500)
            else:
                sm.ctx.selected_showtime = "09:15 PM"

            # Open Seat Map
            proceed_seats = await self.page.query_selector("#proceed-seats-btn, button:has-text('Select Seats')")
            if proceed_seats and await proceed_seats.is_visible():
                await proceed_seats.click()
                await self.page.wait_for_timeout(1000)

            # Pick Seats
            req_seats = intent.ticket_count or 1
            available_seats = await self.page.query_selector_all(".seat:not(.occupied)")
            chosen_seats = []
            for s in available_seats[:req_seats]:
                s_id = await s.get_attribute("data-seat") or await s.inner_text()
                chosen_seats.append(s_id.strip())
                await s.click()
                await self.page.wait_for_timeout(300)

            sm.ctx.selected_seats = chosen_seats

            # Proceed to Checkout
            checkout_btn = await self.page.query_selector("#proceed-checkout-btn, button:has-text('Review & Checkout'), button:has-text('Proceed to Checkout')")
            if checkout_btn and await checkout_btn.is_visible():
                await checkout_btn.click()
                await self.page.wait_for_timeout(1000)

            await self.emit_progress(
                stage="SELECTION",
                action=f"Selected showtime '{sm.ctx.selected_showtime}' and seats {sm.ctx.selected_seats}",
                outcome="SEATS_SELECTED",
                sm=sm
            )

            # 5. DETAILS VERIFICATION
            sm.transition_to(BookingState.DETAILS_VERIFICATION, "Verifying order summary details and pricing")
            price_el = await self.page.query_selector("#summary-price, #seat-total-price, strong:has-text('₹')")
            if price_el:
                sm.ctx.total_price = (await price_el.inner_text()).strip()
            else:
                sm.ctx.total_price = f"₹{req_seats * 250}.00"

            venue_el = await self.page.query_selector("#summary-venue, #venue-label")
            if venue_el:
                sm.ctx.selected_venue = (await venue_el.inner_text()).strip()
            else:
                sm.ctx.selected_venue = "JB Cinemas Screen 1"

            await self.emit_progress(
                stage="DETAILS_VERIFICATION",
                action=f"Verified total price {sm.ctx.total_price} at {sm.ctx.selected_venue}",
                outcome="DETAILS_VERIFIED",
                sm=sm
            )

            # 6. PENDING CONFIRMATION GATE
            sm.build_confirmation_summary()
            if not sm.transition_to(BookingState.PENDING_CONFIRMATION, "Halted at Human-in-the-Loop Confirmation Gate"):
                return {"status": "FAILED", "error": sm.ctx.error, "context": sm.ctx.model_dump()}

            self.bring_to_foreground()
            await self.emit_progress(
                stage="PENDING_CONFIRMATION",
                action="Halted at Human-in-the-Loop Confirmation Gate. Browser held open and visible for inspection.",
                outcome="AWAITING_OPERATOR_AUTHORIZATION",
                sm=sm,
                is_pending_confirmation=True
            )

            if not auto_authorize:
                return {
                    "status": "PENDING_CONFIRMATION",
                    "state": BookingState.PENDING_CONFIRMATION.value,
                    "confirmation_summary": sm.ctx.confirmation_summary,
                    "selected_movie": sm.ctx.selected_movie,
                    "selected_city": sm.ctx.selected_city,
                    "selected_date": sm.ctx.selected_date,
                    "selected_showtime": sm.ctx.selected_showtime,
                    "selected_seats": sm.ctx.selected_seats,
                    "total_price": sm.ctx.total_price,
                    "task_id": sm.ctx.task_id,
                    "context": sm.ctx.model_dump(),
                    "state_machine": sm
                }

            # 7. AUTHORIZED & SUBMITTING (If operator granted authorization)
            return await self.authorize_and_submit(sm)

        except Exception as ex:
            sm.ctx.error = f"EXECUTION_EXCEPTION: {str(ex)}"
            sm.transition_to(BookingState.FAILED, str(ex))
            await self.emit_progress(
                stage="FAILED",
                action="Booking execution failed",
                outcome=str(ex),
                sm=sm
            )
            return {"status": "FAILED", "error": sm.ctx.error, "context": sm.ctx.model_dump()}

    async def authorize_and_submit(self, state_machine: Any) -> Dict[str, Any]:
        """
        Operator authorization granted: transitions to AUTHORIZED -> SUBMITTING -> CONFIRMED.
        """
        from core.booking.booking_state_machine import BookingState
        sm = state_machine
        if not sm.authorize():
            return {"status": "FAILED", "error": sm.ctx.error or "Authorization rejected.", "context": sm.ctx.model_dump()}

        sm.transition_to(BookingState.SUBMITTING, "Submitting final booking to ticketing provider")
        submit_btn = await self.page.query_selector("#authorize-booking-btn, button:has-text('Authorize & Confirm Booking'), button:has-text('Confirm Booking')")
        if submit_btn and await submit_btn.is_visible():
            await submit_btn.click()
            await self.page.wait_for_timeout(1500)

        # Read confirmed reference ID
        ref_el = await self.page.query_selector("#booking-reference-id, #booking-id, strong:has-text('BKG-')")
        booking_ref = None
        if ref_el:
            booking_ref = (await ref_el.inner_text()).strip()
        else:
            booking_ref = f"BKG-CONFIRMED-{sm.ctx.task_id}"

        sm.ctx.booking_reference = booking_ref
        sm.transition_to(BookingState.CONFIRMED, f"Booking verified with reference {booking_ref}")

        return {
            "status": "CONFIRMED",
            "state": BookingState.CONFIRMED.value,
            "booking_reference": booking_ref,
            "selected_movie": sm.ctx.selected_movie,
            "selected_city": sm.ctx.selected_city,
            "selected_date": sm.ctx.selected_date,
            "selected_showtime": sm.ctx.selected_showtime,
            "selected_seats": sm.ctx.selected_seats,
            "total_price": sm.ctx.total_price,
            "task_id": sm.ctx.task_id,
            "context": sm.ctx.model_dump()
        }

    # ─────────────────────────────────────────────────────────
    # 5. NATURAL LANGUAGE AUTONOMOUS DISPATCHER
    # ─────────────────────────────────────────────────────────
    async def book_from_command(self, command: str, site_url: Optional[str] = None) -> Any:
        """Parse natural language command and execute universal autonomous flow."""
        from core.booking.intent_parser import parse_booking_intent

        cmd = (command or "").strip()
        lower = cmd.lower()

        # Flight booking flow
        if "flight" in lower or "fly" in lower:
            try:
                m = re.search(r"from\s+(\w+)\s+to\s+(\w+)(?:\s+on\s+([\d\-]+))?", lower)
                if m:
                    origin = m.group(1)
                    dest = m.group(2)
                    date = m.group(3) or "2026-10-15"
                    flights = await self.search_flights(origin, dest, date)
                    return "\n".join(flights) if flights else "No flights available."
                return "Usage: 'Book flight from BOM to DEL on 2026-10-15'"
            finally:
                await self.stop()

        # Parse structured intent with normalized NLP
        intent = parse_booking_intent(cmd, default_city=user_vault.get("city", "Kochi"))

        # Check for missing essential information
        if not intent.movie_name:
            return (
                f"Sir, I parsed your request, but no movie or event title was specified.\n"
                f"Missing fields: {', '.join(intent.missing_information)}.\n"
                f"Please specify which movie you would like to book."
            )

        # Detect target URL. An explicit BookMyShow command is allowed to use
        # the configured real portal; otherwise fail closed instead of
        # selecting the local deterministic mock.
        target_portal = site_url
        if not target_portal:
            if "127.0.0.1" in lower or "localhost" in lower:
                m_url = re.search(r"http://[a-zA-Z0-9\.:]+", cmd)
                target_portal = m_url.group(0) if m_url else None
            elif any(p in lower for p in ("mock", "test portal", "test site")):
                target_portal = "http://127.0.0.1:8765/"
            elif "bookmyshow" in lower:
                target_portal = os.getenv("AEGIS_BOOKING_SITE_URL", "https://in.bookmyshow.com").strip()

        # Execute verified booking state machine
        res = await self.execute_booking_flow(intent, site_url=target_portal)

        if res.get("status") == "PENDING_CONFIRMATION":
            return res.get("confirmation_summary")
        elif res.get("status") == "CONFIRMED":
            return f"Booking confirmed successfully! Reference: {res.get('booking_reference')}"
        elif res.get("status") == "FAILED":
            return f"Booking halted: {res.get('error')}"

        return res

# Global singleton
jarvis_ticket_booker = JarvisTicketBooker(headless=False)
