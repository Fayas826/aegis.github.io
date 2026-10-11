# AEGIS Desktop Adapter Architecture & Semantic Control Framework
**Phase 3.2 Architectural Specification — Adaptive Multi-Modal Execution**

---

## 1. Executive Summary

AEGIS Phase 3.2 introduces the **Adaptive Desktop Execution and Semantic UI Control Architecture**, elevating AEGIS from coordinate-based visual heuristics to an enterprise-grade, multi-tiered automation system.

Rather than relying on brittle pixel coordinates or generic prompt keywords, AEGIS inspects the target application environment and dispatches operations across four distinct execution tiers:
1. **Tier 1: Native Application Integration / CLI Scripting**
2. **Tier 2: Microsoft Windows UI Automation (UIA)**
3. **Tier 3: Visual Computer Use (VLM / OCR / Coordinates)**
4. **Tier 4: Browser Automation (Playwright DOM Locators)**

All tiers operate under strict security boundaries: thread-safe COM apartments, foreground-window active verification (`FOCUS_MISMATCH` protection), desktop session preflight checks, bounded retry limits, and mandatory independent outcome verification.

---

## 2. Core Execution Tiers

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AEGIS TASK DISPATCHER                             │
│                  (Task Intent, Application Identity, Goal)                  │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
                                       ▼
                     ┌───────────────────────────────────┐
                     │     AdaptiveExecutionRouter       │
                     │  - Environment Profiler           │
                     │  - Capability Inspector (PATH/Win)│
                     │  - Opaque Canvas Catalog          │
                     └─────────────────┬─────────────────┘
                                       │
       ┌───────────────────────────────┼───────────────────────────────┐
       │                               │                               │
       ▼                               ▼                               ▼
┌──────────────┐             ┌──────────────────┐            ┌──────────────────┐
│    TIER 1    │             │      TIER 2      │            │      TIER 4      │
│  Native API  │             │   Windows UIA    │            │   Playwright     │
│   and CLI    │             │  (Semantic UI)   │            │   Browser DOM    │
└──────┬───────┘             └────────┬─────────┘            └────────┬─────────┘
       │                              │                               │
       │ (Non-GUI Intent)             │ (Accessible Controls)         │ (Web URLs)
       │                              │                               │
       ▼                              ▼                               ▼
Deterministic Run             Control Patterns               Semantic Locators
Exit Code & Stdout            Invoke / Value / Select        CDP Sessions
       │                              │                               │
       └──────────────────────┐       │ (Failure/Opaque Canvas)       │ (Web Fallback)
                              │       ▼                               │
                              │ ┌──────────────┐                      │
                              └►│    TIER 3    │◄─────────────────────┘
                                │ Visual Screen│
                                │ VLM / OCR /  │
                                │ Grounding    │
                                └──────┬───────┘
                                       │
                                       ▼
                       Independent Outcome Verification
                       (Screen / UIA / Process / File)
```

### Tier 1: Native Application Integration & CLI Scripting
- **Primary Use**: Non-GUI computation, data processing, code compilation, Git repository management, and applications exposing direct scripting runtimes (e.g., Python scripts, Node.js, Blender Python `bpy`).
- **Mechanisms**: Explicit process execution with isolated working directories (`cwd`), environment inheritance, real exit codes (`0` vs error codes), and captured stdout/stderr streams.
- **Distinction**: Strictly separated from GUI automation. Unrestricted shell access is never substituted for desktop control where visual or interactive application manipulation is requested.

### Tier 2: Microsoft Windows UI Automation (Semantic UIA)
- **Primary Use**: Standard Windows desktop applications (Win32, WPF, UWP/WinUI, WinForms) exposing accessible control trees.
- **Mechanisms**:
  - Thread-safe COM initialization via `UIAutomationInitializerInThread`.
  - Window hierarchy traversal (`inspect_window`, `get_ui_tree`).
  - Control pattern invocation: `GetInvokePattern().Invoke()`, `GetTogglePattern().Toggle()`, `GetSelectionItemPattern().Select()`, and `GetValuePattern().SetValue()`.
  - Dynamic type-based resolvers across `ButtonControl`, `EditControl`, `TextControl`, `ListItemControl`, `DataItemControl`, and `TreeItemControl`.
  - Invariant to monitor resolution, window scaling, and DPI display changes.

### Tier 3: Visual Computer Use
- **Primary Use**: Applications presenting opaque graphics canvases (e.g., MSPaint, game engines, specialized CAD tools), custom canvas widgets lacking UIA accessibility, or as a safety fallback when UIA patterns fail.
- **Mechanisms**:
  - Fresh desktop screen capture with Windows Desktop Station awareness (`WinSta0\Default`).
  - DPI coordinate normalization (handling 125%, 150%, 200% Windows display scaling).
  - OCR and VLM spatial grounding with post-action verification.
  - Safe human-like mouse and keyboard injection (`pyautogui`, `pywin32`).

### Tier 4: Browser Automation
- **Primary Use**: Web applications and web navigation.
- **Mechanisms**: Playwright-managed Chromium instances using semantic DOM locators (roles, text, accessible labels). Retains session continuity and never falls back to desktop mouse simulation when Playwright semantic locators are available.

---

## 3. Adaptive Execution Selection Engine

The `AdaptiveExecutionRouter` dynamically chooses execution methods using an empirical capability discovery ladder:

1. **Target Profiling**:
   - URL & Web Protocol Inspection (`http://`, `https://`, domain TLDs) -> Routes to **Tier 4 (Playwright)**.
   - CLI Executable Verification (inspects system `PATH`, command arguments, and intent verbs like `run`, `execute`, `version`) -> Routes to **Tier 1 (Native API/CLI)**.
   - Opaque Canvas Catalog (`mspaint`, `photoshop`, `gimp`, `unity`, `unreal`, `godot`) -> Identifies low UIA accessibility, pre-routing to **Tier 3 (Visual Computer Use)**.
   - Running HWND Accessibility Probing -> Probes top-level windows for responsive UIA trees. If responsive, routes to **Tier 2 (Windows UIA)**.

2. **Structured Fallback Ladder**:
   - If a Tier 2 action encounters an unhandled control or COM exception (e.g., `EVENT_E_ALL_SUBSCRIBERS_FAILED`), the engine falls back to bounding-box coordinate clicking (`CoordinateClick`) and visual verification.
   - If an application is uninstalled or an invalid HWND is provided, the engine immediately halts with a structured error (`APPLICATION_NOT_FOUND`, `WINDOW_NOT_FOUND`, `ELEMENT_NOT_FOUND`), never attempting blind retries.

---

## 4. Safety & Security Invariants

1. **Active Foreground Verification (`FOCUS_MISMATCH`)**:
   - Keystroke injection and focused control invocation strictly assert `GetForegroundWindow() == target_hwnd`.
   - If another window steals focus, input is suppressed immediately and a structured `FOCUS_MISMATCH` status is returned.
2. **Desktop Session Preflight (`DESKTOP_SESSION_UNAVAILABLE`)**:
   - Windows desktop station (`OpenInputDesktop`) is checked before launching or interacting. If the desktop is locked or sleeping, the system returns `BLOCKED` instead of falsely reporting success or corrupting input.
   - Never attempts to bypass the Windows lock screen.
3. **Bounded Retries & Non-Idempotent Safeguards**:
   - Non-idempotent operations (such as submit buttons or purchase workflows) are limited to bounded single attempts.
4. **Mandatory Independent Verification**:
   - An action is only marked `SUCCESS` when independently verified via UIA value inspection, file existence checks, or screenshot visual observation.

---

## 5. Tool Registry Integration

The Phase 3.2 capabilities are fully integrated into the canonical AEGIS `ToolRegistry`:

| Tool Identifier | Input Parameters | Primary Execution Tier | Function |
|---|---|---|---|
| `desktop.inspect_window` | `target: Union[int, str]` | Tier 2 | Inspects window title, class name, PID, bounding box, and visibility. |
| `desktop.get_ui_tree` | `target: Union[int, str], max_depth: int` | Tier 2 | Traverses the accessible UIA hierarchy into a structured JSON tree. |
| `desktop.find_ui_element` | `target, name, automation_id, control_type` | Tier 2 | Resolves specific controls matching semantic criteria with timeout. |
| `desktop.invoke_ui_element`| `target, name, automation_id, verify_focus` | Tier 2 / 3 Fallback | Invokes control via `InvokePattern`, `TogglePattern`, or coordinate click. |
| `desktop.read_ui_element` | `target, name, automation_id` | Tier 2 | Reads text or value property from controls (e.g. calculation displays). |
| `desktop.set_ui_value` | `target, name, value` | Tier 2 / 3 Fallback | Sets value via `ValuePattern` or focused typing with focus assertion. |
| `desktop.select_ui_item` | `target, name, automation_id` | Tier 2 | Selects items in lists/trees via `SelectionItemPattern`. |
| `desktop.adaptive_execute` | `task, target, action, params` | All Tiers (Router) | Autonomous tier selection, execution, fallback, and verification. |

All 112 canonical AEGIS swarm agents have direct access to these tools through the standard tool executor, maintaining 100% backward compatibility with the existing multi-agent architecture.
