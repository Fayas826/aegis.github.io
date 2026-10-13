# AEGIS Phase 4 — Browser Automation and Ticket Booking Comprehensive Audit

**Date:** 2026-10-13  
**Status:** Audit Complete — Identified 8 Core Architectural Defects  
**Target:** Ticket Booking Pipeline, Playwright Browser Drivers, Intent Parsing, Session Continuity, and Safety Gates  

---

## 1. Executive Summary

AEGIS incorporates an ambitious multimodal autonomous agent architecture designed to handle complex desktop and web workflows. However, previous audits and end-user testing identified significant failure modes in browser automation and ticket booking:
1. **Brittle regex intent extraction** that mangles titles (e.g. `BOOK AVENGER MOVIE TICKET` yields corrupted titles or unintended branches, `Find an evening show for Avengers in Kochi` extracts `"evening   avengers"`).
2. **Silent fabrication of missing parameters** (e.g. defaulting city to `"Kollam"` and seats to `2` without prompting the user or identifying missing data).
3. **Severe browser context fragmentation** across four independent Playwright instances (`JarvisTicketBooker`, `AegisWebAgent`, `MultimodalVisionAgent`, and `PlaywrightBrowserDriver`), breaking cross-agent continuity and session handoffs.
4. **Hardcoded, fragile BookMyShow DOM selectors** that fail whenever layouts change or dynamic popups occur.
5. **Premature browser closure upon errors**, discarding active sessions and preventing recovery or human inspection.
6. **Absence of a formal task state machine**, lacking verified transition criteria (`CREATED → INTENT_VALIDATION → SEARCHING → RESULTS_VERIFICATION → SELECTION → DETAILS_VERIFICATION → PENDING_CONFIRMATION`).
7. **Lack of deterministic end-to-end testing fixtures** for automated CI validation of booking flows without live payments or CAPTCHA blockers.

---

## 2. Detailed Capability & Defect Audit

### Defect 1: Brittle Regex Intent Extraction and Title Corruption
- **Source Location:** `core/ticket_booking_agent.py` lines 828-868.
- **Reproducible Symptom:**
  - `BOOK AVENGER MOVIE TICKET`: Extracted `raw_title = "avenger movie ticket"`, seats default to `2` instead of `1`.
  - `Find tickets for Avengers`: Hits Mode 2 Step 2 search branch because it matches `"find"`, extracting `"tickets for avengers"` as the movie query.
  - `Search Avengers movie tickets`: Hits Mode 2 Step 2, searching for `"avengers movie tickets"`.
  - `Find an evening show for Avengers in Kochi`: Noise-word stripping (`re.sub(noise, '', movie)`) replaces `"show"` and `"for"` with empty space, producing `"evening   avengers"`.
  - `Look for two tickets tomorrow`: Extracts `"two  tomorrow"` as the movie name and defaults city to `"Kollam"`.
  - `Find the next available show for this movie`: Extracts `"the next available   this"` as movie name.
- **Root Cause:**
  - Reliance on single-pass heuristic regular expressions with non-greedy lookaheads against ambiguous end-of-string anchors.
  - Naive noise-word substring stripping that leaves orphan adjectives and adverbs attached to entity titles.
  - Overlapping branch trigger keywords (`"search"`, `"find"`, `"book"`) that route single-shot requests into conversational step branches.
- **Proposed Minimal Repair:**
  - Implement a validated structured intent extraction model (`BookingIntent`) with fields: `booking_type`, `movie_name`, `city`, `venue`, `date`, `time_preference`, `ticket_count`, `language`, `seat_preference`, `missing_information`, `requires_confirmation`.
  - Use deterministic grammar and LLM structured output when available, falling back to a robust entity-aware parser that strips action tokens, prepositions, and qualifiers cleanly without mangling titles.
  - Identify required fields (`movie_name`, `city`, `ticket_count`) and mark `missing_information` rather than silently inventing default values.

---

### Defect 2: Session Fragmentation and Multiple Independent Playwright Lifecycles
- **Source Location:**
  - `core/ticket_booking_agent.py` lines 42-65 (`JarvisTicketBooker.start()`)
  - `core/perception/browser_driver.py` lines 15-25 (`PlaywrightBrowserDriver.launch_browser()`)
  - `core/web_agent.py` lines 20-49 (`AegisWebAgent.initialize()`)
  - `core/perception/multimodal_vision_agent.py` lines 87-110 (`MultimodalVisionAgent.execute_visual_task()`)
- **Reproducible Symptom:**
  - When `ComputerUseAgent` detects a booking task and issues `SwarmHandoff("ticket_booking", ...)`, `JarvisTicketBooker` launches a completely new Chrome instance.
  - Active browser contexts, cookies, and opened tabs in `browser_driver` or `computer_use_session_manager` are abandoned.
  - Multiple headless or headed browser windows accumulate in the Windows desktop session.
- **Root Cause:**
  - Every component implemented its own `async_playwright().start()` loop and managed its own `self.browser` and `self.page` attributes without central session binding.
  - `computer_use_session_manager` possesses `get_browser_page()` and `set_browser_page()`, but `JarvisTicketBooker` and `browser_driver` never consulted it.
- **Proposed Minimal Repair:**
  - Unify Playwright browser management through `browser_driver` and `computer_use_session_manager`.
  - Ensure `JarvisTicketBooker.start()` checks for an existing active page in `computer_use_session_manager` or `browser_driver` before spawning a new browser process.
  - Implement clean shared context reuse and lifecycle controls.

---

### Defect 3: Missing `navigate_to` and Browser Navigation Primitives in `browser_driver.py`
- **Source Location:** `core/perception/browser_driver.py` and `core/execution/adaptive_execution_router.py` lines 201-204.
- **Reproducible Symptom:**
  - In `AdaptiveExecutionRouter`: Tier 4 `ExecutionMethod.BROWSER_DOM` calls `await browser_driver.navigate_to(url)`.
  - `PlaywrightBrowserDriver` only defined `launch_browser`, `get_active_elements`, and `close`. Calling `navigate_to` raises `AttributeError: 'PlaywrightBrowserDriver' object has no attribute 'navigate_to'`.
- **Root Cause:**
  - Incomplete API surface on `PlaywrightBrowserDriver` when Phase 3.2 introduced the adaptive execution router.
- **Proposed Minimal Repair:**
  - Implement `navigate_to(url, timeout, wait_until)` on `PlaywrightBrowserDriver`.
  - Add page verification, reload, and element query methods to complete the abstraction.

---

### Defect 4: Hardcoded, Brittle BookMyShow DOM Selectors
- **Source Location:** `core/ticket_booking_agent.py` lines 346-444.
- **Reproducible Symptom:**
  - Hardcoded selectors like `ul#popQty li[data-qty='{seats}']`, `button#btmcntbook`, and `a.showtime-pill` fail when BookMyShow updates its React component class hashes (e.g. `sc-1or3vea-12`).
  - Search fails if the search input uses dynamic IDs or if the modal backdrop intercepts pointer events.
- **Root Cause:**
  - High coupling to ephemeral DOM selectors of a single commercial ticketing portal instead of semantic locators (`role="button"`, `role="searchbox"`, accessible name, ARIA labels) combined with vision grounding.
- **Proposed Minimal Repair:**
  - Use semantic Playwright locators prioritizing accessibility roles and text matching (`page.get_by_role()`, `page.get_by_placeholder()`, `page.get_by_text()`).
  - Leverage `dom_accessibility_driver` to discover controls based on accessibility tree properties.
  - Implement bounded visual fallback using `multimodal_vision_agent` when canvas or inaccessible elements are detected.

---

### Defect 5: Abrupt Browser Termination on Failure
- **Source Location:** `core/ticket_booking_agent.py` lines 888, 909.
- **Reproducible Symptom:**
  - Whenever an exception occurs or an unhandled status is returned, `book_from_command` calls `await self.stop()`, immediately destroying the browser instance.
  - The operator cannot inspect the page, see the error state, or intervene manually.
- **Root Cause:**
  - Blind `try...finally: await self.stop()` pattern designed for batch CLI scripts rather than an interactive assistant with human-in-the-loop recovery.
- **Proposed Minimal Repair:**
  - Preserve the browser context upon non-fatal failures or authorization requests.
  - Provide structured error reporting with error codes (`NAVIGATION_TIMEOUT`, `ELEMENT_NOT_FOUND`, `NO_SHOWTIMES`, `SESSION_EXPIRED`) and retain page state for inspection.

---

### Defect 6: Unverified Result Assumptions and Inaccurate Status Reporting
- **Source Location:** `core/ticket_booking_agent.py` lines 424-462.
- **Reproducible Symptom:**
  - Even if seat layout navigation fails (`if "seat" not in current_url.lower()`), the script proceeds to attempt visual click and outputs:
    `"Sir, I have navigated to the graphical seat layout... Your contact details are ready in the vault... Please authorize payment on screen"`.
  - Reports `HUMAN_AUTHORIZATION_REQUIRED` even when seats were not selected and the page never reached the checkout or payment screen.
- **Root Cause:**
  - Optimistic progression without independent post-condition assertion at each step.
- **Proposed Minimal Repair:**
  - Enforce explicit verification at each step:
    - Search verification: confirms search results list contains at least one match.
    - Selection verification: confirms URL or page header reflects the selected movie and venue.
    - Showtime verification: confirms active showtime is highlighted.
    - Seat verification: confirms selected seat count matches requested count.
    - Checkout verification: confirms order summary and pricing are rendered before entering `PENDING_CONFIRMATION`.

---

### Defect 7: Lack of a Formal Booking State Machine
- **Source Location:** `core/ticket_booking_agent.py` overall flow.
- **Reproducible Symptom:**
  - States are represented as ad-hoc strings in dictionaries (`"status": "HUMAN_AUTHORIZATION_REQUIRED"`, `"stage": "PAYMENT_GATE"`).
  - No deterministic transition table or rollback mechanism.
- **Root Cause:**
  - Direct procedural script without integration into AEGIS `TaskEngine` or task-state management.
- **Proposed Minimal Repair:**
  - Define explicit `BookingState` enum:
    `CREATED → INTENT_VALIDATION → SEARCHING → RESULTS_VERIFICATION → SELECTION → DETAILS_VERIFICATION → PENDING_CONFIRMATION → AUTHORIZED → SUBMITTING → CONFIRMED`
  - Ensure failure transitions go to `FAILED`, `BLOCKED`, `CANCELLED`, or `RECOVERY`.
  - Strictly prohibit reporting `BOOKED` until independent confirmation verification succeeds.

---

### Defect 8: Absence of Automated End-to-End Test Fixtures
- **Source Location:** `tests/` directory (no mock ticketing website or end-to-end tests for booking).
- **Reproducible Symptom:**
  - Testing previously required hitting live commercial websites (`in.bookmyshow.com`), resulting in flakiness from dynamic anti-bot protection, IP rate limits, geolocation redirects, and payment requirements.
- **Root Cause:**
  - No local deterministic HTTP mock fixture existed in the repository for end-to-end testing of the complete browser automation pipeline.
- **Proposed Minimal Repair:**
  - Build a local deterministic mock ticket website running on `http://127.0.0.1:8765` featuring movies (e.g. *Avengers: Secret Wars*), cities (Kochi, Kollam, Mumbai), showtimes, interactive seat selection, and order summary.
  - Implement full end-to-end automated test suite verifying natural-language intent parsing, navigation, search, selection, detail verification, confirmation stop gate, and authorized completion.

---

## 3. Summary Audit Matrix

| Component | Status | Identified Defects | Repair Action |
|:---|:---:|:---|:---|
| **Intent Parser** | ❌ FAILED | Mangles movie names, strips words blindly, assumes city/seats | Implement `BookingIntent` model + entity-aware parser + missing info detection |
| **Browser Driver** | ⚠️ PARTIAL | Missing `navigate_to`, disconnected from ticket agent | Add navigation/verification APIs + unify lifecycle |
| **Session Management** | ❌ FAILED | 4 separate Playwright runtimes; session handoff lost | Bind to `computer_use_session_manager` |
| **DOM / A11y Driver** | ⚠️ PARTIAL | DOM driver exists but bypassed by hardcoded BMS selectors | Use semantic locators (roles, placeholders, text) |
| **Visual Fallback** | ⚠️ PARTIAL | Available in `multimodal_vision_agent`, but bypasses DOM when invoked | Establish DOM-first -> Visual fallback ladder |
| **State Machine** | ❌ FAILED | Procedural strings, unverified state progression | Formal `BookingState` lifecycle with independent assertions |
| **Confirmation Safety** | ⚠️ PARTIAL | Message printed but status reported prematurely without verification | Structured `PENDING_CONFIRMATION` gate with full summary before any submission |
| **End-to-End Tests** | ❌ MISSING | Only manual prompt testing against live web | Create local deterministic mock server + automated acceptance test suite |

---
