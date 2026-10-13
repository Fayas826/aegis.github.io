# AEGIS Phase 4 — Known Limitations and Operational Boundaries

**Date:** 2026-10-13  
**Status:** Documented & Enforced  
**Scope:** Browser Automation, Web Ticket Booking, Security Gates, and External System Interactivity  

---

## 1. Executive Summary

AEGIS Phase 4 provides robust, schema-validated natural language intent extraction, unified Playwright browser lifecycle management, verified state transitions, and an immutable human confirmation safety gate.

However, browser automation in the open internet is subject to significant real-world constraints. To maintain architectural integrity, avoid misleading claims of "universal autonomous booking", and uphold legal and ethical standards, this document details the verified limitations of the system.

---

## 2. Detailed Technical & Operational Limitations

### 2.1 Anti-Bot Protections and Web Application Firewalls (WAF)
- **Constraint:** Real-world ticketing websites (such as BookMyShow, Ticketmaster, Fandango, and IRCTC) deploy aggressive bot mitigation suites (Cloudflare Turnstile, Akamai Bot Manager, PerimeterX, Datadome, and Google reCAPTCHA v3/Enterprise).
- **Impact on AEGIS:**
  - Automated Playwright browsers running in default or headless mode may trigger interstitial verification challenges (e.g. "Checking your browser before accessing", puzzle CAPTCHAs, or silent IP rate limiting).
  - AEGIS intentionally does **not** incorporate CAPTCHA-bypassing hacks, unauthorized proxy rotation farms, or anti-fingerprinting exploits.
- **System Policy:** When an anti-bot challenge is encountered, AEGIS transitions to state `BLOCKED`, logs a structured error, captures a diagnostic screenshot, and requests the operator to solve the challenge manually in the active browser window.

### 2.2 Dynamic Canvas & Non-DOM Interactive Seat Maps
- **Constraint:** Modern cinema chains and stadium ticketing platforms frequently render seating layouts within an HTML5 `<canvas>` element or heavily obscured WebGL viewports rather than accessible DOM nodes (`<button>`, `<div role="checkbox">`).
- **Impact on AEGIS:**
  - Standard Playwright locators (`page.locator()`, `get_by_role()`, `get_by_text()`) cannot query individual seat coordinates inside a single pixel buffer canvas.
  - While AEGIS incorporates multimodal vision grounding (`MultimodalVisionAgent`, `RealtimeSpatialVision`), vision-based coordinate clicking requires higher inference latency and can vary with screen resolution and zoom scaling.
- **Operational Boundary:** DOM-based automation is verified for accessible HTML seating structures (as implemented in the CineAEGIS portal). For canvas-only seat layouts, AEGIS must either utilize site-specific internal APIs (if authorized) or fall back to visual coordinate grounding with operator validation.

### 2.3 Authentication, SMS OTPs, and Two-Factor Security
- **Constraint:** Most commercial ticketing portals require user login via mobile phone numbers, followed by an SMS-delivered One-Time Password (OTP) or OAuth (Google / Apple Sign-in).
- **Impact on AEGIS:**
  - AEGIS cannot autonomously read SMS messages from the operator's personal mobile device unless an authorized hardware SMS gateway or dedicated integration is configured.
  - The agent must pause and notify the user to complete the login step.
- **Safety Rule:** AEGIS never stores, prompts for, or logs user banking passwords or static SMS OTPs in plaintext memory or logs.

### 2.4 Payment Gateways and PCI-DSS Security Boundaries
- **Constraint:** The final step of any real ticket booking requires financial commitment via UPI, credit/debit card, net banking, or digital wallets (Razorpay, Stripe, PayU, Paytm).
- **Impact on AEGIS:**
  - Phase 4 strictly enforces that **no automated agent may submit financial payments or initiate fund transfers autonomously**.
  - All booking flows halt unconditionally at `PENDING_CONFIRMATION`.
- **Enforcement:**
  - The state machine will refuse transition to `CONFIRMED` unless `is_authorized == True` is explicitly supplied by the human operator.
  - Payment details (CVV, card numbers, UPI PINs) are never accepted by the intent parser or browser automation forms.

### 2.5 Website Layout Drift and Geographic Fragmentation
- **Constraint:** Public commercial platforms routinely conduct A/B testing, redesign checkout funnels, modify class names, and alter URL routing based on user geolocation, language, and regional regulations.
- **Impact on AEGIS:**
  - Hardcoded CSS selectors or XPath expressions will inevitably break during layout updates.
  - While AEGIS's `click_semantic()` and `fill_semantic()` use accessible roles and visible text labels to minimize breakage, radical structural shifts (e.g. migrating from a multi-page checkout to a single-page modal wizard) require updating site-specific adapter rules.

### 2.6 Headed vs. Headless Browser Execution in Windows
- **Constraint:** In Windows desktop environments, running Playwright in headless mode (`headless=True`) avoids taskbar clutter and focus stealing, but certain websites detect headless user agents and disable interactive elements.
- **Recommendation:** Headed mode (`headless=False`) is recommended for user-supervised operations, while headless mode is reserved for automated CI testing and local mock validation.

---

## 3. Summary of Operational Guardrails

1. **Local & Controlled Staging First:** All feature development and verification must take place against deterministic test fixtures (`tests/mock_ticket_server.py`) before attempting live public websites.
2. **Mandatory Confirmation Gate:** No booking is ever submitted without explicit human review of movie, venue, date, time, seat, and price details.
3. **Truth in State Reporting:** Search completion is not event selection; showtime selection is not seat reservation; and reaching the payment screen is never reported as `BOOKED`.
4. **Graceful Failure & Recovery:** When faced with missing entities, network timeouts, or layout changes, AEGIS reports structured, actionable diagnostics without terminating abruptly.
