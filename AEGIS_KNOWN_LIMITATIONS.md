# AEGIS Known Limitations & Hardware Operating Boundaries

**Document Version:** 3.0  
**Target Hardware:** Windows 11 Laptop with RTX 3050 (4GB VRAM) & 16GB System RAM  

---

## 1. Laptop Hardware & VRAM Operating Envelope

AEGIS runs on resource-constrained laptop hardware rather than cloud server clusters. To prevent OOM crashes, the following boundaries are actively enforced:

1. **4GB VRAM Allocation Ceiling:**
   - EasyOCR GPU engine consumes ~400MB–600MB VRAM.
   - Moondream (1.8B) or LLaVA (7B quantized) requires 2.0GB–3.5GB VRAM.
   - Attempting to simultaneously load EasyOCR, Moondream, and PyTorch Whisper / Kokoro TTS into GPU memory causes CUDA Out-of-Memory faults.
   - **Enforced Strategy:** Vision models and OCR engines load lazily on-demand. EasyOCR is gated behind `ensure_loaded()`. Speech generation runs on CPU or lightweight endpoints.

2. **Local Ollama Model Response Latency:**
   - Under heavy laptop CPU/GPU loads, local Ollama endpoints (e.g. `llava:latest`, `moondream:latest`) may take 15–45 seconds per vision inference query.
   - If a local model query exceeds 45s, a `ReadTimeout` is triggered and circuit breakers gracefully degrade to Tier 1 Cloud Vision (Gemini Flash) with 2-second latency.

---

## 2. Windows Desktop Session Constraints

1. **Display Sleep / Workstation Lock:**
   - Windows GDI functions (`BitBlt`, `GetDC`) and `PIL.ImageGrab.grab()` fail with `ScreenShotError` or `OSError: screen grab failed` when:
     - The laptop display turns off due to Windows Power Options.
     - The user locks their desktop (Win+L / Secure Desktop active).
   - In this state, `GetForegroundWindow()` returns `0` (`FG HWND: 0`).
   - Native desktop automation requires an unlocked, active interactive console session (`WinSta0\Default`).

2. **Windows 11 Packaged App Lifecycle:**
   - Modern Windows 11 apps (e.g., packaged Notepad, Calculator) run inside AppX/WinUI application containers.
   - Invoking `C:\Windows\System32\notepad.exe` directly via raw `subprocess.Popen` without shell activation spawns an execution alias stub that exits immediately.
   - Launching must use Windows Shell activation (`start notepad`) or `os.startfile()` to establish top-level interactive windows.

3. **Orphaned Process Accumulation:**
   - Rapid repetitive test scripts launching desktop apps without teardown can leave dormant processes running in the background. Periodic health checks must verify PID cleanliness.

---

## 3. Cloud Provider Quotas & Circuit Breakers

1. **OpenAI Credits Exhaustion:**
   - As documented in runtime telemetry: `OpenAIException - You have no credits remaining`.
   - The system's circuit breaker (`cloud_openai`) correctly trips to OPEN and prevents repeated failing requests.

2. **Gemini Flash Rate Limiting:**
   - Free-tier / burst quota limits (HTTP 429) trip the `cloud_gemini` breaker for 300s, routing tasks to local Moondream or Groq Vision.

---

## 4. Architectural Boundaries for Upcoming Phases

1. **Ticket Booking Engine:**
   - Phase 3 stabilized multi-agent routing, error propagation, and tool execution.
   - The end-to-end booking flow across real ticket portals (e.g., BookMyShow / IRCTC) remains an experimental capability and is intentionally scheduled for full end-to-end overhaul in Phase 4.

2. **Destructive Operations & Financial Actions:**
   - All financial transactions (payments, ticket purchases) and irreversible filesystem operations require human confirmation. Autonomous bypass of these gates is explicitly prohibited.
