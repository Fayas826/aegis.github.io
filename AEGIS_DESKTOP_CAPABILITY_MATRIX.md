# AEGIS Desktop Capability Matrix
**Phase 3.2 Evaluation Across Applications, Modalities, and Verification Boundaries**

---

## 1. Application & Operation Status Matrix

Statuses:
- `PASS`: Formally tested and independently verified via live test execution.
- `FAIL`: Tested and failed verification without recovery.
- `BLOCKED`: Prevented by desktop station lock, security barrier, or unavailable environment.
- `UNTESTED`: Not part of the current Phase 3.2 test suite.
- `UNSUPPORTED`: Technology cannot support requested interface without architectural extension.

| Application & Version | Requested Task & Outcome | Chosen Execution Method | Detected Capabilities | Executed? | Verification Method | Test Result | Failure Reason & Recovery Behavior | Known Limitations |
|---|---|---|---|:---:|---|:---:|---|---|
| **Windows Calculator** (Windows 11 UWP `CalculatorApp.exe`) | Compute `23 × 17` and verify displayed output equals `391` | **Tier 2: Windows UIA** (`InvokePattern` + `read_ui_element`) | Full UIA Tree, `ApplicationFrameWindow`, `ButtonControl` (Automation IDs: `num2Button`, `multiplyButton`, `equalButton`), `TextControl` (`CalculatorResults`) | Yes | UIA `CalculatorResults` read -> `'Display is 391'`, screenshot saved | **PASS** | `EVENT_E_ALL_SUBSCRIBERS_FAILED` handled by `CoordinateClick` and inter-click pacing | Child buttons inside UWP lack independent Win32 HWNDs; require UIA pattern or bounding box click |
| **File Explorer** (Windows 11 `CabinetWClass`) | Open isolated folder, discover file `target_acceptance_sample.txt`, select item | **Tier 2: Windows UIA** (`find_ui_element` + `select_ui_item`) | Win32 Top-level HWND, `ListItemControl` / `DataItemControl`, `SelectionItemPattern` | Yes | `SelectionItemPattern.IsSelected == True`, window inspection, screenshot saved | **PASS** | Directory population delay handled by bounded timeout (8.0s) and multi-class probe | Deep subfolders with 10k+ items virtualize controls until scrolled into view |
| **Python CLI Runtime** (Python 3.11.9 on Windows 11) | Execute `python --version` and report active runtime version | **Tier 1: Native API / CLI** (`run_command`) | System PATH executable, subprocess stream capture, isolated cwd | Yes | Exit code `0`, stdout parsed against `sys.version_info` (`3.11`) | **PASS** | Invalid executable returns structured error; not confused with launch success | Non-interactive commands only; interactive REPLs require separate terminal session |
| **Windows Notepad** (`Notepad.exe` Win11) | Launch, focus verify, type text, save to disk, verify contents | **Tier 2: Windows UIA + Tier 3: Typing** | Top-level HWND, `DocumentControl` / `EditControl`, `ValuePattern` | Yes | File existence on disk, content equality comparison, screenshot saved | **PASS** | Keystrokes suppressed if `GetForegroundWindow() != hwnd` (`FOCUS_MISMATCH`) | Modern Win11 Notepad uses tabs; multiple open files share single top-level HWND |
| **Google Chrome** (Version 128+ 64-bit) | Browser navigation, DOM interaction, page queries | **Tier 4: Browser Automation** (Playwright DOM) | Playwright Chromium DevTools Protocol (CDP), semantic DOM locators | Yes (Phase 3.0/3.1) | DOM element assertions, URL state verification | **PASS** | Session crash handled by browser restart; avoids fallback to brittle mouse clicks | Heavy single-page apps (SPAs) with shadow DOM require explicit semantic locators |
| **MS Paint** (`mspaint.exe`) | Canvas drawing and image manipulation | **Tier 3: Visual Computer Use** | Top-level Win32 window, opaque graphics canvas, minimal UIA elements | Untested in 3.2 suite | N/A | **UNTESTED** | Pre-routed to Tier 3 by `AdaptiveExecutionRouter` due to opaque canvas catalog | UIA does not expose canvas pixels or individual vector strokes |
| **Blender** (`blender.exe` 4.x) | 3D mesh creation, scene rendering | **Tier 1: Native Scripting** (`bpy`) | Scripting engine via Python socket / background execution | Phase 3.3 Target | N/A | **UNTESTED** | Planned for dedicated Phase 3.3 Blender expansion | GUI is rendered purely via OpenGL/Vulkan with zero native OS UI automation support |
| **VS Code** (`code.cmd`) | Workspace editing, extension execution | **Tier 1: CLI** / **Tier 4: Electron DOM** | CLI arguments (`code .`), Electron accessibility tree | Untested in 3.2 suite | N/A | **UNTESTED** | Native CLI arguments preferred over GUI automation | Large file tree virtualization in Electron UI |

---

## 2. Fallback and Recovery Validation Matrix

| Tested Edge Case / Failure Condition | Expected System Behavior | Observed Behavior in Live Acceptance | Result |
|---|---|---|:---:|
| **UIA Control Missing** (`find_ui_element` for non-existent button) | Return structured `ELEMENT_NOT_FOUND` error; do not crash or claim success | Returned `status: "ELEMENT_NOT_FOUND"`, `error_code: "ELEMENT_NOT_FOUND"`, timeout bounded to 3.0s | **PASS** |
| **Uninstalled Application** (`open_app` for non-existent `.exe`) | Return structured `ERROR` with application not found; do not launch random fallback | Returned `status: "ERROR"`, `error: "Could not find application ... on system."` | **PASS** |
| **Foreground Mismatch** (`invoke_ui_element` with mismatched HWND) | Prevent keystroke / click injection, return `FOCUS_MISMATCH` | Returned `status: "FOCUS_MISMATCH"`, `error_code: "FOCUS_MISMATCH"`, input completely suppressed | **PASS** |
| **UWP Subscriber Latency** (`EVENT_E_ALL_SUBSCRIBERS_FAILED`) | Catch COM exception, gracefully fallback to `CoordinateClick` via bounding box | Inter-click delay and `CoordinateClick` fallback ensured expression was fully entered | **PASS** |
| **Adaptive Execution Profiler** (Routing CLI vs GUI tasks) | Select highest-fidelity tier based on capabilities, never simplistic keyword rules | CLI tasks routed to `NATIVE_API`; Windows apps routed to `WINDOWS_UIA` with `VISUAL_COMPUTER_USE` fallback | **PASS** |

---

## 3. Modality Generalization Summary

- **Total Applications Verified Live in Phase 3.2**: 4 (Calculator, File Explorer, Terminal Runtime, Notepad).
- **Execution Tiers Actively Demonstrated**: 3 (Tier 1: Native CLI, Tier 2: Windows UIA, Tier 3: Visual / Keystroke Fallback).
- **Universal Cross-Application Compatibility Claim**: **DISPROVEN / AVOIDED**. Universal compatibility across all Windows applications is not claimed; capability is empirically discovered per application target.
