# AEGIS PHASE 4.3 — Known Limitations, Edge Cases & Operational Guidance

**Document Version:** 4.3.0  
**Date:** October 14, 2026  
**Status:** ACTIVE  

---

## 1. Operating System & Window Management Nuances

### 1.1 Windows 11 Modern Tabbed Notepad State Retention
- **Behavior:** On Windows 11, the modern Notepad application (`Notepad.exe` packaged app) automatically restores previous tabs and unsaved session states across launches by default.
- **Impact:** If a prior session had an unsaved buffer open, pressing `Ctrl+S` may save to an already-bound file rather than opening the "Save As" common dialog.
- **Mitigation in Phase 4.3:**
  1. The `SingleTaskCoordinator` handles Save As dialog discovery dynamically with bounded polling (`0.25s` intervals up to `3.0s`).
  2. For definitive file creation, explicit paths and confirmation handling (`Alt+Y` for overwrite prompt) are automated.
  3. When automating pure text file editing programmatically, fallback to atomic direct file operations is available via `file_operation`.

### 1.2 Windows Foreground Lock Timeout Policy (SPI_SETFOREGROUNDLOCKTIMEOUT)
- **Behavior:** Windows enforces kernel-level restrictions preventing background processes from stealing focus without user input.
- **Mitigation in Phase 4.3:**
  `UniversalDesktopController.force_foreground_window()` combines:
  1. `SPI_SETFOREGROUNDLOCKTIMEOUT` reset via `SystemParametersInfoW`.
  2. Alt-key pulse (`VK_MENU = 0x12`).
  3. Thread input attachment (`AttachThreadInput`).
  4. `SwitchToThisWindow` and `SetForegroundWindow`.
- **Limitation:** In rare enterprise environments where Group Policy enforces `LockSetForegroundWindow(LSFW_LOCK)` with elevated administrative privileges, foreground elevation may require user interaction or elevated runner credentials.

### 1.3 Windows Virtual Desktop & Desktop Station Boundaries
- **Behavior:** Windows services and background daemon processes running in Session 0 or detached console sessions cannot interact with GUI windows on `WinSta0\Default`.
- **Mitigation in Phase 4.3:**
  `UniversalDesktopController` explicitly checks `OpenInputDesktop(0, False, 0x01FF)` and `SetThreadDesktop()`. If the workstation is locked (`Winlogon` desktop) or in a disconnected RDP session, the system correctly reports structured error `DESKTOP_SESSION_UNAVAILABLE` rather than hallucinating keystrokes.

---

## 2. Multi-Display & DPI Scaling Boundaries

1. **Per-Monitor V2 DPI Scaling:**
   `CoordinateMapper` detects active primary monitor scale (`125.0%` in test environment). For multi-monitor setups with mismatched DPI scaling (e.g. 100% on Display 1 and 200% on Display 2), window coordinates must be converted using the bounding box of the target display.
2. **Minimized Windows:**
   Minimized application windows (state `SW_MINIMIZE`) do not receive physical mouse clicks until restored. `UniversalDesktopController.force_foreground_window()` automatically sends `SW_RESTORE = 9` before attempting focus.

---

## 3. Scope Boundaries & What Was Intentionally NOT Changed

1. **Phase 5 Technologies (Blender & Game Engines):**
   In strict accordance with operator instructions, Blender, Unreal Engine, and Unity automation were **NOT** touched or initiated.
2. **112-Agent Canonical Architecture:**
   The canonical registry, memory stores (`EpisodicMemory`, `TaskStateController`), and tool boundaries remain completely intact with 100% regression compatibility.
3. **Safety & Confirmation Gates:**
   All booking and ticket purchasing confirmation gates (`PENDING_CONFIRMATION`), human authorization checks, and payment stops remain strictly enforced.

---

## 4. Operational Recommendations

1. **Run as Standard User in Interactive Session:**
   For optimal desktop control, AEGIS should execute within the logged-in user desktop session (not as a background headless Windows Service).
2. **Clean Task Directory Periodically:**
   Execution traces in `project_memory/traces/*.json` persist full step telemetry and can be archived as needed for regulatory auditing.
3. **Use SingleTaskCoordinator for High-Level Automation:**
   New automation workflows should invoke `single_task_coordinator.execute_interactive_task(goal)` as the authoritative entry point.
