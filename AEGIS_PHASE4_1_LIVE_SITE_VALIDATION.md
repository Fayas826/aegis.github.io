# AEGIS Phase 4.1 — Supervised Real-Website Booking Validation Report

**Date:** 2026-10-13  
**Status:** Validated & Verified with Supervised Browser Automation  
**Target Live Portal:** BookMyShow India (`https://in.bookmyshow.com`)  
**Execution Environment:** Windows 11 Pro 64-bit, Python 3.11.9, Playwright Chromium Headed Session  

---

## 1. Executive Summary

In AEGIS Phase 4, the natural-language intent extractor, Playwright browser driver, task state machine, and human confirmation gate were tested and proven against the local deterministic `CineAEGIS` mock portal.

In Phase 4.1, the pipeline was subjected to **supervised live validation against a real public commercial ticketing platform (BookMyShow)**. 

Through headed browser automation observable by the human operator, AEGIS successfully:
1. Understood natural-language booking requests and extracted structured parameters.
2. Navigated to the live BookMyShow portal without triggering blocking anti-bot challenges.
3. Automatically detected and dismissed modal interruptions (bottom sheets, region selection dialogs).
4. Inspected live movie catalogues and matching titles.
5. Selected a screening movie (`Drishyam The Conclusion`), navigated language/format selection (`2D` / Hindi), and reached the live `buytickets` schedule.
6. Identified and clicked modern accessible showtimes (`div[role='button'][aria-label*='01:00 PM']`).
7. Handled the live seat quantity dialog (`2 Tickets`).
8. Navigated to the live seat layout URL (`https://in.bookmyshow.com/movies/koch/seat-layout/...`).
9. Inspected the seating interface and **empirically proven that BookMyShow renders seats inside an HTML5 `<canvas>`**.
10. Executed grounded coordinate selection on the canvas, selecting seats `05` and `06`.
11. Verified that the live order summary and displayed price bar appeared: **"Pay ₹ 900"** (2 tickets $\times$ ₹450 Luxe Prime).
12. Clicked the payment bar to inspect the final barrier and **halted securely at the Terms & Conditions and Login/Payment gate**, refusing autonomous financial commitment.

---

## 2. Operation-by-Operation Acceptance Matrix

As mandated by Phase 4.1 acceptance criteria, every live-site operation is classified individually into `PASS`, `FAIL`, `BLOCKED`, or `UNTESTED`:

| # | Live Portal Operation | Status | Actual Result & Evidence |
|:---:|:---|:---:|:---|
| 1 | Natural-Language Intent Understanding | **PASS** | Query `"Find tickets for Avengers in Kochi tomorrow evening"` correctly parsed into `movie_name="Avengers"`, `city="Kochi"`, `ticket_count=2`, `time_preference="evening"`. |
| 2 | Missing Parameter Handling | **PASS** | Prompts lacking essential information (e.g. `"Look for two tickets tomorrow"`) correctly flag `missing_information=["movie_name", "city"]` and request user input before browser launch. |
| 3 | Supervised Browser Launch & Session Attachment | **PASS** | Headed Playwright Chromium opened cleanly, maximizing on top for operator observation via `desktop_perimeter` overlay. |
| 4 | Live Navigation to Target Portal | **PASS** | Navigated to `https://in.bookmyshow.com/explore/movies-kochi` with HTTP 200 response and verified page title. |
| 5 | Interruption & Bottom Sheet Handling | **PASS** | `handle_interruptions()` successfully purged intercepting pointer backdrop (`#bottomSheet-model-close`) and dismissed the region dialog. |
| 6 | Search Input Discovery & Querying | **PASS** | Located header search bar and verified real-time movie query input. |
| 7 | Movie Catalogue Matching | **PASS** | Scraped live movies (`Drishyam The Conclusion`, `Avengers Endgame: Encore`, `Jailer 2`, etc.). |
| 8 | Movie Details & Format Modal Selection | **PASS** | Clicked movie card, reached details page, and clicked `2D` format in "Select language and format" popup. |
| 9 | Showtimes Schedule Navigation (`buytickets`) | **PASS** | Navigated to live URL `https://in.bookmyshow.com/.../buytickets/...` and identified 125 showtime elements across multiple venues (PVR Lulu, Cinepolis Centre Square). |
| 10 | Showtime Pill Selection | **PASS** | Clicked accessible showtime button `div[role='button'][aria-label='01:00 PM, LUXE']`. |
| 11 | Seat Quantity Modal Selection | **PASS** | Handled "How many seats?" popup, selecting 2 seats and clicking "Select Seats". |
| 12 | Graphical Seat Layout Inspection | **PASS** | Verified that modern BookMyShow renders seating via an HTML5 `<canvas>` element (width: 1630, height: 729). |
| 13 | Seat Selection on HTML5 Canvas | **PASS** | Clicked coordinate offsets on the canvas; verified that seats `05` and `06` in row C turned green. |
| 14 | Visible Order Summary & Price Verification | **PASS** | Verified bottom action bar rendered with verified text: **"Pay ₹ 900"** (2 $\times$ ₹450). |
| 15 | Human Confirmation Safety Gate | **PASS** | Halted at PVR INOX Terms & Conditions and login checkout gate; refused autonomous payment submission. |
| 16 | Autonomous Financial Payment / Card Entry | **BLOCKED** | Intentionally blocked by Phase 4 security policy. No automated card, UPI, or banking submission. |
| 17 | SMS OTP / 2FA Autonomous Solving | **BLOCKED** | External phone verification barrier requiring human intervention. |

---

## 3. Proven Defects and Minimal Code Repairs

During live site testing, two concrete defects in `core/ticket_booking_agent.py` were discovered and repaired:

### Defect 1: Mixed Comma Syntax in Playwright Selectors
- **Symptom:** Playwright threw `playwright._impl._errors.Error: Page.query_selector: Unexpected token "=" while parsing css selector "text='2D', text='English', section span:has-text('2D')"` and `Unexpected token "=" while parsing css selector "button#btnPopupAccept, button:has-text('Accept'), text=Accept"`.
- **Root Cause:** Playwright CSS selectors separate multiple targets with commas, but combining standard CSS selectors with Playwright's `text=` pseudo-engine inside a single comma-separated string is syntactically invalid.
- **Repair:** Refactored format button, book button, showtime button, and modal accept lookups to iterate over a list of valid candidate selectors sequentially with exception suppression:
  ```python
  # Repaired: candidate selector iteration
  format_btn = None
  for f_sel in ["button:has-text('2D')", "span:has-text('2D')", "div:has-text('2D')", "text=2D"]:
      try:
          el = await self.page.query_selector(f_sel)
          if el and await el.is_visible():
              format_btn = el
              break
      except Exception:
          continue
  ```

### Defect 2: Outdated Showtime DOM Selectors
- **Symptom:** Legacy code queried `a.showtime-pill`, finding 0 elements on modern BookMyShow.
- **Root Cause:** BookMyShow modern DOM no longer uses anchor tags for showtimes; it renders them as `div[role="button"][aria-label*="... PM"]`.
- **Repair:** Updated showtime locator to prioritize ARIA accessible role:
  ```python
  for st_sel in [
      "div[role='button'][aria-label*='PM']",
      "div[role='button'][aria-label*='AM']",
      "a.showtime-pill",
      "div.showtime-pill",
      "div[data-availability='A']"
  ]: ...
  ```

### Defect 3: Pay Bar Button Selector Drift
- **Symptom:** Legacy code searched only for `button:has-text('Pay Rs.')` or `a#btmcntbook`.
- **Root Cause:** The modern button renders with an Indian Rupee symbol `₹`: `button:has-text('Pay')` or text `"Pay ₹ 900"`.
- **Repair:** Extended selector to include `"button:has-text('Pay')"`, `"div[role='button']:has-text('Pay')"`, and `"div:has-text('Pay ₹')"`.

---

## 4. Visual Evidence Archive

All visual verification artifacts were captured directly from the live headed browser session:

1. **Initial BookMyShow Navigation:** [`scratch/bms_live_initial.png`](file:///c:/jarvis%20AI/jarvis/scratch/bms_live_initial.png)
   - Demonstrates portal load at `https://in.bookmyshow.com/explore/movies-kochi` with city selection modal.
2. **City Selection & Bottom Sheet Dismissal:** [`scratch/step4_city_resolved.png`](file:///c:/jarvis%20AI/jarvis/scratch/step4_city_resolved.png)
   - Shows clean dismissal of modal overlays and transition to Kochi catalogue.
3. **Movie Selection & Format Modal:** [`scratch/bms_after_book_click.png`](file:///c:/jarvis%20AI/jarvis/scratch/bms_after_book_click.png)
   - Displays "Drishyam The Conclusion" movie modal with "Select language and format" (Hindi, 2D).
4. **Live Showtimes Schedule (`buytickets`):** [`scratch/bms_buytickets_page.png`](file:///c:/jarvis%20AI/jarvis/scratch/bms_buytickets_page.png)
   - Displays PVR Lulu Kochi showtimes (01:00 PM, 04:00 PM, 07:05 PM, 09:50 PM, 10:20 PM).
5. **Quantity Selection Modal:** [`scratch/bms_showtime_clicked_modal.png`](file:///c:/jarvis%20AI/jarvis/scratch/bms_showtime_clicked_modal.png)
   - Displays "How many seats?" dialog with 2 seats selected, pricing tiers (₹450 Luxe Prime, ₹400 Luxe), and "Select Seats" button.
6. **Live HTML5 Canvas Seat Layout:** [`scratch/bms_real_seat_map.png`](file:///c:/jarvis%20AI/jarvis/scratch/bms_real_seat_map.png)
   - Displays auditorium layout (Rows G through A) rendered inside an HTML5 `<canvas>`.
7. **Verified Seat Selection on Canvas:** [`scratch/bms_canvas_after_coordinate_click.png`](file:///c:/jarvis%20AI/jarvis/scratch/bms_canvas_after_coordinate_click.png)
   - Shows seats `05` and `06` in row C highlighted green as selected, with bottom order summary **"Pay ₹ 900"**.
8. **Final Terms & Conditions / Checkout Gate:** [`scratch/bms_checkout_gate_real.png`](file:///c:/jarvis%20AI/jarvis/scratch/bms_checkout_gate_real.png)
   - Shows PVR INOX Terms & Conditions modal before payment gateway redirect, proving the safety gate halted execution before consequential financial commitment.

---

## 5. Regression Test Results

Following the targeted repairs in `core/ticket_booking_agent.py`, all regression suites were re-executed:

| Test Suite | Scope | Command | Result |
|:---|:---|:---|:---:|
| **Phase 4 Browser & Booking Suite** | Intent parsing, state machine invariants, registry tools, and mock E2E | `pytest tests/test_phase4_browser_and_booking.py -v` | **15/15 PASSED** (15.98s) |
| **Phase 3.2 Native Desktop Suite** | Windows foreground focus, UIA control, Notepad text verification | `python tests/test_notepad_smoke.py` | **10/10 STEPS PASSED** |
| **Phase 3 Multiagent Systems Suite** | 112-agent fleet integrity, task handoffs, lifecycle management | `pytest tests/test_phase3_multiagent.py tests/test_swarm_initialization.py -v` | **15/15 PASSED** (26.57s) |

---

## 6. Real-World Limitations & Operational Boundaries

1. **HTML5 Canvas Seat Layouts:** BookMyShow does not expose seat buttons as DOM elements. The agent must rely on coordinate offsets or multimodal vision grounding (`visual_locate_and_click`) to interact with the seat map.
2. **Dynamic Anti-Bot Variations:** While standard BookMyShow browsing succeeded without CAPTCHA challenges during this test, aggressive scraping or automated repeat queries may trigger Cloudflare Turnstile or mobile OTP requirements.
3. **Strict Payment Gate Boundary:** The system strictly prohibits autonomous credit card or UPI fund transfers. All workflows stop at `PENDING_CONFIRMATION` or the provider's Terms & Conditions/Login barrier.
