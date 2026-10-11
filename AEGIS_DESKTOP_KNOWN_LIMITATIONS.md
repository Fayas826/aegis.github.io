# AEGIS Desktop Known Limitations
**Phase 3.2 Operating Boundaries, Edge Cases, and Environmental Constraints**

---

## 1. Environmental & Session Boundaries

### 1.1 Windows Session Lock & RDP Disconnection (`DESKTOP_SESSION_UNAVAILABLE`)
- **Limitation**: When a Windows session is locked (`Win + L`), asleep, or disconnected from an active Remote Desktop Protocol (RDP) session, the interactive desktop station `WinSta0\Default` is suspended by the operating system kernel.
- **System Impact**: Calls to `GetForegroundWindow()`, `BitBlt`, and `SendInput` will fail or return `0`.
- **Enforced Safety Behavior**: AEGIS verifies desktop availability via `OpenInputDesktop()`. If unavailable, operations are halted with status `BLOCKED` (`DESKTOP_SESSION_UNAVAILABLE`). AEGIS **never** attempts to bypass or crack the Windows lock screen, ensuring complete compliance with OS security boundaries.

---

## 2. Windows UI Automation (Tier 2) Boundaries

### 2.1 UWP / XAML Subscriber Event Latency (`EVENT_E_ALL_SUBSCRIBERS_FAILED`)
- **Limitation**: Modern Universal Windows Platform (UWP) and Windows App SDK / WinUI 3 applications (e.g., Windows 11 Calculator, Windows Settings) run inside an `ApplicationFrameWindow` host container. Control events invoked via `GetInvokePattern().Invoke()` can occasionally encounter COM error `0x80040201` (`EVENT_E_ALL_SUBSCRIBERS_FAILED`) if invoked in rapid succession before the XAML UI thread dispatches event subscribers.
- **Enforced Mitigation**: The `semantic_ui_controller` implements an automatic multi-step fallback ladder:
  1. Primary: `InvokePattern.Invoke()` with minimum 0.2s inter-click pacing.
  2. Secondary: `TogglePattern.Toggle()` where applicable.
  3. Tertiary: Direct control `Click()`.
  4. Visual Grounding Fallback: Computes the element's `BoundingRectangle` center coordinates `((left+right)/2, (top+bottom)/2)` and executes a `CoordinateClick`.

### 2.2 Virtualized List Controls in File Explorer and Data Grids
- **Limitation**: Windows File Explorer and WPF/WinUI data grids employ UI virtualization to conserve memory. In folders containing thousands of files, items that are scrolled off-screen are not instantiated in the active UI Automation element tree until scrolled into viewport.
- **Enforced Mitigation**: `select_ui_item` searches across `ListItemControl`, `DataItemControl`, `TreeItemControl`, and generic controls. For massive directories, caller agents should supply exact search names or navigate directly via path rather than relying on full tree traversal.

### 2.3 Window Title Latency upon Application Launch
- **Limitation**: Newly launched processes frequently undergo multi-stage window creation (e.g. `calc.exe` launcher stub spawning `CalculatorApp.exe`, or `explorer.exe` launching a background shell thread before creating `CabinetWClass`).
- **Enforced Mitigation**: `open_app()` polls visible windows over a 5.0s window with multi-term search matching (including target folder basenames) to reliably acquire the final top-level HWND.

---

## 3. Opaque Canvas Applications (Tier 3 Requirement)

### 3.1 Zero-UIA Canvas Controls
- **Limitation**: Applications like Microsoft Paint, Adobe Photoshop, GIMP, Blender, and game engines (Unity, Unreal, Godot) render their primary workspace onto an opaque DirectX, OpenGL, Vulkan, or software surface. The OS accessibility tree reports only a generic pane or empty window control.
- **Enforced Behavior**: The `AdaptiveExecutionRouter` maintains an explicit `_opaque_canvas_catalog`. These applications are automatically detected and directed to **Tier 3 (Visual Computer Use)** using screenshot capture, OCR, and coordinate grounding rather than wasting cycles on empty UIA tree traversals.

---

## 4. Execution Modality Boundaries

### 4.1 CLI vs GUI Separation
- **Limitation**: Direct shell access (`run_command`) is fast and deterministic, but cannot replace visual desktop automation when the user requests GUI workflows, visual confirmation, or window interactions.
- **Enforced Boundary**: The router selects `NATIVE_API` only when intent is non-GUI computation, code compilation, or file inspection. GUI tasks are preserved within Tier 2 and Tier 3.

### 4.2 Non-Idempotent Operation Protection
- **Limitation**: Operations such as "Click Purchase", "Delete File", or "Submit Form" cannot be blindly retried upon transient failures without risk of side-effects.
- **Enforced Boundary**: Non-idempotent operations have bounded retry limits (single attempt), and their outcome must be independently verified before declaring completion.
