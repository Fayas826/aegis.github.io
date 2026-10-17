"""Portable desktop perception primitives for AEGIS.

This module deliberately has no hard dependency on a display server, Pillow,
``mss`` or Windows UI Automation.  Those integrations are discovered only
when an observation is requested.  Callers therefore receive an explicit
``UNAVAILABLE`` observation instead of an invented screenshot or UI tree.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence


SUCCESS = "SUCCESS"
UNAVAILABLE = "UNAVAILABLE"
BLOCKED = "BLOCKED"
FAILED = "FAILED"


def _finite_number(value: Any) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except (ValueError, OverflowError):
        return False


def _dimension(value: Any) -> int:
    if not _finite_number(value) or value < 1 or int(value) != value:
        raise ValueError("display dimensions must be finite positive integers")
    return int(value)


def _rectangle(value: Any, size: int = 4) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != size or not all(_finite_number(v) for v in value):
        raise ValueError("invalid finite pixel geometry")
    result = tuple(int(round(v)) for v in value)
    if size == 4 and (result[2] < result[0] or result[3] < result[1]):
        raise ValueError("inverted pixel rectangle")
    return result


def _json_safe(value: Any) -> Any:
    """Copy provider metadata into JSON-safe values without invoking repr hooks."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(v) for v in value]
    return None


def normalize_coordinates(x: float, y: float, width: int, height: int) -> tuple[float, float]:
    """Convert a pixel coordinate to inclusive ``[0, 1]`` coordinates.

    Coordinates outside the actual pixel rectangle are rejected.  Using
    ``width - 1``/``height - 1`` means the last pixel round-trips exactly and
    avoids an off-by-one click outside a display.
    """

    width, height = _dimension(width), _dimension(height)
    if not (_finite_number(x) and _finite_number(y)):
        raise ValueError("display dimensions and coordinates must be finite and positive")
    if not (0 <= float(x) <= width - 1 and 0 <= float(y) <= height - 1):
        raise ValueError("pixel coordinate is outside the display")
    return (float(x) / max(width - 1, 1), float(y) / max(height - 1, 1))


def denormalize_coordinates(x: float, y: float, width: int, height: int) -> tuple[int, int]:
    """Convert normalized coordinates to a clamped physical pixel position."""

    width, height = _dimension(width), _dimension(height)
    if not (_finite_number(x) and _finite_number(y)):
        raise ValueError("display dimensions and normalized coordinates must be finite")
    if not (0.0 <= float(x) <= 1.0 and 0.0 <= float(y) <= 1.0):
        raise ValueError("normalized coordinate must be in the inclusive [0, 1] range")
    return (
        max(0, min(width - 1, int(round(float(x) * (width - 1))))),
        max(0, min(height - 1, int(round(float(y) * (height - 1))))),
    )


@dataclass(frozen=True)
class DesktopElement:
    """A source-neutral UI element in display pixel coordinates."""

    element_id: str
    text: str = ""
    role: str = "element"
    bbox: tuple[int, int, int, int] = (0, 0, 0, 0)
    clickable: bool = True
    visible: bool = True
    confidence: float = 1.0
    source: str = "visual"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bbox
        return (int(round((left + right) / 2)), int(round((top + bottom) / 2)))


@dataclass(frozen=True)
class DesktopMark:
    """One numbered Set-of-Marks entry."""

    number: int
    element_id: str
    label: str
    bbox: tuple[int, int, int, int]
    center: tuple[int, int]
    normalized_center: tuple[float, float]
    clickable: bool
    visible: bool
    confidence: float
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "label": self.label,
            "element_id": self.element_id,
            "bbox": list(self.bbox),
            "center": list(self.center),
            "normalized_center": list(self.normalized_center),
            "clickable": self.clickable,
            "visible": self.visible,
            "confidence": self.confidence,
            "source": self.source,
        }


@dataclass(frozen=True)
class SetOfMarkManifest:
    """Stable, numbered manifest associated with one observation."""

    manifest_id: str
    width: int
    height: int
    marks: tuple[DesktopMark, ...] = ()

    def get(self, number: int | str) -> Optional[DesktopMark]:
        try:
            wanted = int(number)
        except (TypeError, ValueError):
            return None
        return next((mark for mark in self.marks if mark.number == wanted), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "width": self.width,
            "height": self.height,
            "marks": [mark.to_dict() for mark in self.marks],
        }


@dataclass
class DesktopObservation:
    status: str
    width: int = 0
    height: int = 0
    image_b64: str = ""
    active_window: str = "Desktop"
    active_process: str = "none"
    elements: tuple[DesktopElement, ...] = ()
    manifest: Optional[SetOfMarkManifest] = None
    timestamp: float = field(default_factory=time.time)
    error: Optional[str] = None
    sources: tuple[str, ...] = ()
    observation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    image_mime: str = "image/png"
    origin: tuple[int, int] = (0, 0)
    window_id: int = 0
    window_bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    process_id: int = 0
    windows: tuple[dict[str, Any], ...] = ()
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == SUCCESS

    @property
    def marks(self) -> tuple[DesktopMark, ...]:
        return self.manifest.marks if self.manifest else ()

    @property
    def state_fingerprint(self) -> str:
        """Content identity, independent of capture time and generated IDs.

        Includes image bytes (when present) so visual-only changes are observable.
        Diagnostic messages, source ordering and manifest numbering are incidental.
        """
        state = self.to_dict(include_fingerprint=False)
        for key in ("observation_id", "timestamp", "manifest", "diagnostics", "sources", "error"):
            state.pop(key, None)
        for key in ("elements", "windows"):
            state[key] = sorted(state[key], key=lambda v: json.dumps(v, sort_keys=True))
        state["image_digest"] = hashlib.sha256(self.image_b64.encode("ascii")).hexdigest()
        return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":"),
                                         ensure_ascii=True).encode("utf-8")).hexdigest()

    def to_dict(self, *, include_fingerprint: bool = True) -> dict[str, Any]:
        result = {
            "status": self.status,
            "width": self.width,
            "height": self.height,
            "active_window": self.active_window,
            "active_process": self.active_process,
            "elements": [
                {
                    "element_id": element.element_id,
                    "text": element.text,
                    "role": element.role,
                    "bbox": list(element.bbox),
                    "center": list(element.center),
                    "clickable": element.clickable,
                    "visible": element.visible,
                    "confidence": element.confidence,
                    "source": element.source,
                    "metadata": _json_safe(element.metadata),
                }
                for element in self.elements
            ],
            "manifest": self.manifest.to_dict() if self.manifest else None,
            "timestamp": self.timestamp,
            "error": self.error,
            "sources": list(self.sources),
            "observation_id": self.observation_id,
            "image_mime": self.image_mime,
            "origin": list(self.origin),
            "window_id": self.window_id,
            "window_bounds": list(self.window_bounds),
            "process_id": self.process_id,
            "windows": _json_safe(self.windows),
            "diagnostics": _json_safe(self.diagnostics),
        }
        if include_fingerprint:
            result["state_fingerprint"] = self.state_fingerprint
        return result


def _coerce_element(raw: Any, index: int, source: str = "visual") -> Optional[DesktopElement]:
    """Accept existing ``GUIElement`` objects and plain provider dictionaries."""

    if isinstance(raw, DesktopElement):
        raw = {name: getattr(raw, name) for name in raw.__dataclass_fields__}
    if isinstance(raw, Mapping):
        get = raw.get
        element_id = get("element_id", get("id", f"{source}_{index}"))
        text = get("text", get("name", get("label", ""))) or ""
        role = get("role", get("element_type", get("control_type", "element"))) or "element"
        bbox = get("bbox")
        if bbox is None and all(key in raw for key in ("left", "top", "right", "bottom")):
            bbox = [raw["left"], raw["top"], raw["right"], raw["bottom"]]
        if bbox is None:
            center = _rectangle(get("center", [0, 0]), 2)
            bbox = [center[0], center[1], center[0], center[1]]
        try:
            rect = _rectangle(bbox)
            confidence = get("confidence", 1.0)
            if not _finite_number(confidence):
                raise ValueError("invalid element confidence")
        except (TypeError, ValueError, IndexError, OverflowError):
            return None
        metadata = dict(get("metadata", {})) if isinstance(get("metadata", {}), Mapping) else {}
        password = bool(metadata.get("is_password", metadata.get("password", get("is_password", False))))
        if password:
            text = ""
            # Never propagate provider-supplied password values or text metadata.
            metadata = {k: v for k, v in metadata.items() if k in {
                "runtime_id", "process_id", "is_enabled", "is_offscreen", "is_password",
                "has_keyboard_focus", "is_keyboard_focusable", "control_type", "native_window_handle",
            }}
            metadata["is_password"] = True
        return DesktopElement(
            element_id=str(element_id), text=str(text), role=str(role), bbox=rect,
            clickable=bool(get("clickable", True)) and bool(metadata.get("is_enabled", True)),
            visible=bool(get("visible", True)) and not bool(metadata.get("is_offscreen", False)),
            confidence=float(confidence), source=str(get("source", source)), metadata=_json_safe(metadata),
        )
    # Existing core.perception.visual_state.GUIElement is intentionally handled
    # by attributes, without importing Pillow-heavy modules at import time.
    if hasattr(raw, "element_id") and hasattr(raw, "bbox"):
        return _coerce_element({
            "element_id": getattr(raw, "element_id"), "text": getattr(raw, "text", ""),
            "role": getattr(raw, "role", "element"), "bbox": getattr(raw, "bbox"),
            "clickable": getattr(raw, "clickable", True), "visible": getattr(raw, "visible", True),
            "confidence": getattr(raw, "confidence", 1.0), "source": getattr(raw, "source", source),
            "metadata": getattr(raw, "metadata", {}),
        }, index, source)
    return None


def fuse_elements(primary: Iterable[Any], uia_elements: Iterable[Any]) -> tuple[DesktopElement, ...]:
    """One-to-one visual/UIA fusion by intersection-over-union, not containment.

    UIA siblings and ancestors are never merged with each other.
    """

    visual: list[DesktopElement] = []
    for index, raw in enumerate(primary):
        item = _coerce_element(raw, index, "visual")
        if item is None:
            raise ValueError("invalid visual element")
        visual.append(item)
    uia: list[DesktopElement] = []
    for index, raw in enumerate(uia_elements):
        item = _coerce_element(raw, index, "uia")
        if item is None:
            raise ValueError("invalid UIA element")
        uia.append(item)

    def overlap(a: DesktopElement, b: DesktopElement) -> float:
        al, at, ar, ab = a.bbox
        bl, bt, br, bb = b.bbox
        inter = max(0, min(ar, br) - max(al, bl)) * max(0, min(ab, bb) - max(at, bt))
        area_a = max(0, ar - al) * max(0, ab - at)
        area_b = max(0, br - bl) * max(0, bb - bt)
        return inter / max(1, area_a + area_b - inter)

    result = list(visual)
    unmatched = {i for i, item in enumerate(visual) if item.source not in ("uia", "fused")}
    for item in uia:
        candidates = [(overlap(visual[i], item), i) for i in unmatched]
        score, index = max(candidates, default=(0.0, -1))
        if score < 0.5:
            result.append(item)
        else:
            unmatched.remove(index)
            match = visual[index]
            # Keep the visual rectangle (it is in the same display space), but
            # enrich it with UIA's semantic name/role and mark the provenance
            # explicitly.  This is a true fusion rather than two competing
            # entries in the Set-of-Marks manifest.
            fused = DesktopElement(
                element_id=match.element_id,
                text="" if item.metadata.get("is_password") else item.text or match.text,
                role=item.role or match.role,
                bbox=match.bbox, clickable=item.clickable,
                visible=match.visible and item.visible, confidence=max(match.confidence, item.confidence),
                source="fused", metadata={**match.metadata, **item.metadata},
            )
            # UIA may identify a password field that visual detection did not.
            # Reapply redaction after metadata fusion, including visual metadata.
            result[index] = _coerce_element(fused, index, "fused")
    return tuple(result)


def build_manifest(elements: Iterable[DesktopElement], width: int, height: int,
                   manifest_id: Optional[str] = None) -> SetOfMarkManifest:
    width, height = _dimension(width), _dimension(height)
    marks: list[DesktopMark] = []
    for element in elements:
        if not element.visible:
            continue
        center = element.center
        try:
            normalized = normalize_coordinates(center[0], center[1], width, height)
        except ValueError:
            continue
        number = len(marks) + 1
        label = element.text.strip() if element.text.strip() else element.role
        marks.append(DesktopMark(
            number=number, element_id=element.element_id, label=label,
            bbox=element.bbox, center=center, normalized_center=normalized,
            clickable=element.clickable, visible=element.visible,
            confidence=max(0.0, min(1.0, element.confidence)), source=element.source,
        ))
    return SetOfMarkManifest(manifest_id or uuid.uuid4().hex, int(width), int(height), tuple(marks))


@contextmanager
def _physical_pixels():
    """Use physical Windows coordinates on this thread, restoring its DPI context."""
    previous = None
    setter = None
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        setter = getattr(user32, "SetThreadDpiAwarenessContext", None)
        if setter is not None:
            setter.argtypes, setter.restype = [wintypes.HANDLE], wintypes.HANDLE
            previous = setter(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
            if not previous:
                previous = setter(ctypes.c_void_p(-3))  # PER_MONITOR_AWARE
        if not previous:
            # Do not silently expose virtualized bounds as physical coordinates.
            raise RuntimeError("physical-pixel thread DPI context unavailable")
    try:
        yield
    finally:
        if previous is not None and setter is not None:
            setter(previous)


def _windows_snapshot() -> dict[str, Any]:
    """Read foreground identity, monitor geometry and visible top-level windows."""
    if sys.platform != "win32":
        return {"windows": (), "diagnostics": {"windows": "native discovery is Windows-only"}}
    import ctypes
    from ctypes import wintypes

    with _physical_pixels():
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                       wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        process_names: dict[int, str] = {}

        def describe(hwnd: int) -> dict[str, Any]:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
            user32.GetWindowTextW(hwnd, title, len(title))
            rect = wintypes.RECT()
            bounds = (0, 0, 0, 0)
            if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                bounds = (rect.left, rect.top, rect.right, rect.bottom)
            if pid.value not in process_names:
                name = ""
                handle = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
                if handle:
                    try:
                        path = ctypes.create_unicode_buffer(32768)
                        size = wintypes.DWORD(len(path))
                        if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                            name = path.value.rsplit("\\", 1)[-1]
                    finally:
                        kernel32.CloseHandle(handle)
                process_names[pid.value] = name
            return {"window_id": int(hwnd), "process_id": pid.value, "title": title.value,
                    "process": process_names[pid.value], "bounds": bounds,
                    "visible": bool(user32.IsWindowVisible(hwnd)), "minimized": bool(user32.IsIconic(hwnd))}

        foreground = user32.GetForegroundWindow()
        active = describe(foreground) if foreground else {}
        windows: list[dict[str, Any]] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def collect(hwnd, _):
            try:
                if user32.IsWindowVisible(hwnd):
                    windows.append(describe(hwnd))
            except Exception:
                pass  # A window may disappear while EnumWindows is running.
            return True

        user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        if not user32.EnumWindows(collect, 0):
            raise ctypes.WinError(ctypes.get_last_error())

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        monitor = MonitorInfo()
        monitor.cbSize = ctypes.sizeof(monitor)
        handle = user32.MonitorFromWindow(foreground, 2)  # nearest monitor
        if not user32.GetMonitorInfoW(handle, ctypes.byref(monitor)):
            raise ctypes.WinError(ctypes.get_last_error())
        rect = monitor.rcMonitor
        return {"active_window": active.get("title", ""), "active_process": active.get("process", ""),
                "window_id": active.get("window_id", 0), "process_id": active.get("process_id", 0),
                "window_bounds": active.get("bounds", (0, 0, 0, 0)), "windows": tuple(windows),
                "monitor_bounds": (rect.left, rect.top, rect.right, rect.bottom),
                "diagnostics": {"window_discovery": "win32", "coordinate_space": "physical_pixels"}}


def discover_windows() -> tuple[dict[str, Any], ...]:
    """Read-only native window inventory; empty on unsupported/unavailable desktops.

    Bounds are global physical pixels (left, top, right, bottom), not image-local.
    Does not activate windows, initialize UIA, or take a screenshot.
    """
    try:
        return tuple(_windows_snapshot().get("windows", ()))
    except Exception:
        return ()


def _default_capture() -> Mapping[str, Any]:
    """Capture a real monitor without importing global AEGIS/UI singletons.

    Images, elements and marks use capture-local pixels; add ``origin`` once to
    convert to desktop input coordinates. Window inventory stays desktop-global.
    """
    snapshot = _windows_snapshot()
    bounds = snapshot.pop("monitor_bounds", None)
    diagnostics = snapshot.setdefault("diagnostics", {})
    with _physical_pixels():
        try:
            import mss  # type: ignore
            from mss.tools import to_png  # type: ignore
            with mss.mss() as server:
                if bounds:
                    left, top, right, bottom = bounds
                    monitor = {"left": left, "top": top, "width": right - left, "height": bottom - top}
                else:
                    monitor = server.monitors[1] if len(server.monitors) > 1 else server.monitors[0]
                grabbed = server.grab(monitor)
                image = to_png(grabbed.rgb, grabbed.size)
                width, height = grabbed.size
                origin = (monitor["left"], monitor["top"])
                diagnostics["capture_backend"] = "mss"
        except Exception as mss_error:
            diagnostics["mss_error"] = str(mss_error)
            try:
                from PIL import ImageGrab  # type: ignore
                # On Windows bbox is physical and may include negative origins.
                screenshot = ImageGrab.grab(bbox=bounds, all_screens=True) if bounds else ImageGrab.grab()
                output = io.BytesIO()
                screenshot.convert("RGB").save(output, format="PNG")
                image = output.getvalue()
                width, height = screenshot.size
                origin = (bounds[0], bounds[1]) if bounds else (0, 0)
                diagnostics["capture_backend"] = "Pillow.ImageGrab"
            except Exception as exc:
                raise RuntimeError(f"desktop capture unavailable: {exc}") from exc
    diagnostics["monitor_bounds"] = (origin[0], origin[1], origin[0] + width, origin[1] + height)
    return {**snapshot, "width": width, "height": height, "origin": origin,
            "image_b64": base64.b64encode(image).decode("ascii"), "image_mime": "image/png"}


def _default_uia() -> tuple[Sequence[Any], Optional[str]]:
    if sys.platform != "win32":
        return (), "Windows UI Automation is unavailable on this platform"
    try:
        import uiautomation as auto  # type: ignore
        initializer = getattr(auto, "UIAutomationInitializerInThread", None)
        if initializer is None:
            return (), "UI Automation per-thread COM initializer is unavailable"
        with initializer():
            foreground = auto.GetForegroundWindow()
            root = auto.ControlFromHandle(foreground) if isinstance(foreground, int) else foreground
            if not root:
                return (), "UI Automation foreground window unavailable"
            return _traverse_uia(root)
    except ImportError as exc:
        return (), f"UI Automation package unavailable: {exc}"
    except Exception as exc:
        return (), f"UI Automation scan failed: {exc}"


_UIA_SCAN_LOCK = threading.Lock()
_UIA_SCAN_PENDING = None


def _bounded_default_uia(window_id: int = 0, timeout: float = 1.0) -> tuple[Sequence[Any], Optional[str]]:
    """A hung read-only COM call must not trap the desktop action thread.

    At most one native scan can be outstanding. Timed-out results are discarded,
    never cached as fresh evidence. Python cannot kill a hung COM call; the one
    daemon reader may remain blocked, but no new reader/input threads accumulate.
    """
    global _UIA_SCAN_PENDING
    request = {"done": threading.Event(), "result": ((), "UIA read unavailable")}
    with _UIA_SCAN_LOCK:
        if _UIA_SCAN_PENDING is not None:
            return (), "Previous UIA read is still pending; no stale controls reused"
        _UIA_SCAN_PENDING = request

    def scan():
        global _UIA_SCAN_PENDING
        try:
            request["result"] = _default_uia()
        except Exception:
            request["result"] = ((), "Native UIA read failed")
        finally:
            with _UIA_SCAN_LOCK:
                if _UIA_SCAN_PENDING is request:
                    _UIA_SCAN_PENDING = None
            request["done"].set()

    threading.Thread(target=scan, name="aegis-uia-reader", daemon=True).start()
    if not request["done"].wait(timeout):
        return (), "UIA read exceeded its time budget; controls unavailable"
    if window_id and sys.platform == "win32" and _windows_snapshot().get("window_id") != window_id:
        return (), "Foreground changed during UIA read; controls discarded"
    return request["result"]


def _traverse_uia(root: Any, max_nodes: int = 4096) -> tuple[Sequence[Any], Optional[str]]:
    """Iterative traversal with a node budget, not an arbitrary depth cutoff.

    Only control names and non-value states are read. In particular no Value,
    TextPattern document contents or password-control names are requested.
    """
    stack = [(root, ())]
    visited: set[tuple[Any, ...]] = set()
    elements = []
    scanned = stale = 0
    while stack and scanned < max_nodes:
        control, path = stack.pop()
        scanned += 1
        try:
            pid = int(control.ProcessId)
            try:
                runtime = tuple(int(v) for v in (control.GetRuntimeId() or ()))
            except Exception:
                runtime = ()
            identity = ("runtime", pid, runtime) if runtime else ("path", pid, path)
            if identity in visited:
                continue
            visited.add(identity)
            role = str(control.ControlTypeName or "element")
            # Fail closed if IsPassword cannot be read: do not read Name.
            try:
                password = bool(control.IsPassword)
            except Exception:
                password = True
            enabled = bool(control.IsEnabled)
            offscreen = bool(control.IsOffscreen)
            rect = control.BoundingRectangle
            bbox = _rectangle((rect.left, rect.top, rect.right, rect.bottom))
            metadata = {"runtime_id": runtime, "process_id": pid, "is_enabled": enabled,
                        "is_offscreen": offscreen, "is_password": password, "control_type": role}
            for attr, key in (("HasKeyboardFocus", "has_keyboard_focus"),
                              ("IsKeyboardFocusable", "is_keyboard_focusable"),
                              ("NativeWindowHandle", "native_window_handle")):
                try:
                    metadata[key] = getattr(control, attr)
                except Exception:
                    pass
            # These patterns expose state only, and are never invoked or mutated.
            if not password:
                for method, attr, key in (("GetTogglePattern", "ToggleState", "toggle_state"),
                                          ("GetSelectionItemPattern", "IsSelected", "is_selected"),
                                          ("GetExpandCollapsePattern", "ExpandCollapseState", "expand_collapse_state")):
                    try:
                        pattern = getattr(control, method)()
                        if pattern:
                            metadata[key] = int(getattr(pattern, attr))
                    except Exception:
                        pass
            stable = ".".join(str(v) for v in runtime) if runtime else "path-" + ".".join(map(str, path))
            actionable = role.lower().replace("control", "") in {
                "button", "edit", "checkbox", "radiobutton", "combobox", "listitem",
                "menuitem", "tabitem", "hyperlink", "slider", "treeitem", "spinner", "splitbutton",
            }
            elements.append(DesktopElement(
                f"uia_{pid}_{stable}", "" if password else str(control.Name or ""), role, bbox,
                enabled and not offscreen and actionable, not offscreen and bbox[2] > bbox[0] and bbox[3] > bbox[1],
                0.98, "uia", metadata,
            ))
        except Exception:
            stale += 1
        # Bad/stale container properties must not prevent visiting its children.
        try:
            children = control.GetChildren()
            for index in range(len(children) - 1, -1, -1):
                stack.append((children[index], path + (index,)))
        except Exception:
            stale += 1
    warnings = []
    if stack:
        warnings.append(f"UI Automation traversal truncated at {max_nodes} nodes")
    if stale:
        warnings.append(f"UI Automation skipped {stale} stale/unreadable nodes or child lists")
    return elements, "; ".join(warnings) or None


class DesktopPerception:
    """Capture one fresh display observation and optionally fuse UIA controls.

    Injected capture elements are image-local unless ``elements_coordinate_space``
    is ``desktop``. UIA providers return desktop-global rectangles, translated
    exactly once here. Capture origin and window bounds are physical pixels.
    Optional default UIA failure is diagnostic; malformed injected data is FAILED.
    """

    def __init__(self, capture: Optional[Callable[[], Any]] = None,
                 uia_provider: Optional[Callable[[], Iterable[Any]]] = None):
        self.capture = capture or _default_capture
        self.uia_provider = uia_provider

    def observe(self, include_uia: bool = True) -> DesktopObservation:
        timestamp = time.time()
        try:
            raw = self.capture()
        except Exception as exc:
            return DesktopObservation(UNAVAILABLE, timestamp=timestamp, error=str(exc))
        if isinstance(raw, DesktopObservation):
            if raw.status != SUCCESS:
                return raw
            raw = {name: getattr(raw, name) for name in raw.__dataclass_fields__}
        if not isinstance(raw, Mapping):
            return DesktopObservation(FAILED, timestamp=timestamp, error="capture provider returned an invalid observation")
        try:
            status = raw.get("status", SUCCESS)
            if status not in (SUCCESS, UNAVAILABLE, BLOCKED, FAILED):
                raise ValueError("invalid capture status")
            if status != SUCCESS:
                return DesktopObservation(status, timestamp=timestamp, error=str(raw.get("error", "capture failed")))
            width, height = _dimension(raw["width"]), _dimension(raw["height"])
            origin = _rectangle(raw.get("origin", (0, 0)), 2)
            bounds = _rectangle(raw.get("window_bounds", (0, 0, 0, 0)))

            def identifier(value: Any) -> int:
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError("window/process identifiers must be nonnegative integers")
                return value

            window_id = identifier(raw.get("window_id", 0))
            process_id = identifier(raw.get("process_id", 0))
            diagnostics = raw.get("diagnostics", {})
            if not isinstance(diagnostics, Mapping):
                raise ValueError("capture diagnostics must be a mapping")
            diagnostics = dict(diagnostics)
            diagnostics["element_coordinate_space"] = "capture_local"
            windows = []
            for window in raw.get("windows", ()):
                if not isinstance(window, Mapping):
                    raise ValueError("window inventory must contain dictionaries")
                window = dict(window)
                for key in ("window_id", "process_id"):
                    if key in window:
                        window[key] = identifier(window[key])
                if "bounds" in window:
                    window["bounds"] = _rectangle(window["bounds"])
                windows.append(window)
            image = raw.get("image_b64", "")
            if not isinstance(image, str) or not image.isascii():
                raise ValueError("image_b64 must be an ASCII string")
            mime = raw.get("image_mime", "image/png")
            if mime not in ("image/png", "image/jpeg", "image/jpg"):
                raise ValueError("image_mime must be PNG or JPEG")
            mime = "image/jpeg" if mime == "image/jpg" else mime
            # Signature wins over an incorrectly declared MIME. Do not require
            # decoding an entire image or import Pillow for injected captures.
            try:
                signature = base64.b64decode(image[:32], validate=True)
            except ValueError:
                signature = b""
            if signature.startswith(b"\x89PNG\r\n\x1a\n"):
                mime = "image/png"
            elif signature.startswith(b"\xff\xd8\xff"):
                mime = "image/jpeg"
            primary = raw.get("elements", ())
            space = raw.get("elements_coordinate_space", "capture_local")
            if space not in ("desktop", "capture_local"):
                raise ValueError("invalid elements_coordinate_space")

            def localize(items: Iterable[Any], source: str, global_pixels: bool) -> tuple[DesktopElement, ...]:
                if isinstance(items, (str, bytes, Mapping)):
                    raise ValueError(f"{source} provider must return an iterable of elements")
                result = []
                for index, item in enumerate(items):
                    element = _coerce_element(item, index, source)
                    if element is None:
                        raise ValueError(f"invalid {source} element at index {index}")
                    if global_pixels:
                        l, t, r, b = element.bbox
                        element = replace(element, bbox=(l - origin[0], t - origin[1], r - origin[0], b - origin[1]))
                    result.append(element)
                return tuple(result)

            primary = localize(primary, "visual", space == "desktop")
            uia: Iterable[Any] = ()
            sources = ["display"]
            if include_uia:
                if self.uia_provider is not None:
                    uia = self.uia_provider()
                    # None is not a valid provider result (an empty list is).
                    uia = localize(uia, "uia", True)
                    sources.append("uia")
                else:
                    uia, uia_error = _bounded_default_uia(window_id)
                    uia = localize(uia, "uia", True)
                    if uia:
                        sources.append("uia")
                    if uia_error:
                        diagnostics["uia"] = uia_error
                        if not uia:
                            sources.append("uia_unavailable")
            elements = fuse_elements(primary, uia)
            manifest = build_manifest(elements, width, height)
            observation_id = raw.get("observation_id", str(uuid.uuid4()))
            if not isinstance(observation_id, str) or not observation_id:
                raise ValueError("observation_id must be a nonempty string")
            return DesktopObservation(
                SUCCESS, width, height, image,
                str(raw.get("active_window", "Desktop")), str(raw.get("active_process", "none")),
                elements, manifest, timestamp, None, tuple(sources),
                observation_id=observation_id, image_mime=mime, origin=origin,
                window_id=window_id, window_bounds=bounds, process_id=process_id,
                windows=tuple(_json_safe(window) for window in windows), diagnostics=_json_safe(diagnostics),
            )
        except Exception as exc:
            return DesktopObservation(FAILED, timestamp=timestamp, error=f"invalid perception provider data: {exc}")


desktop_perception = DesktopPerception()


def capture_active_display() -> DesktopObservation:
    """Convenience entry point used by integrations that need one observation."""

    return desktop_perception.observe()


__all__ = [
    "BLOCKED", "FAILED", "SUCCESS", "UNAVAILABLE", "DesktopElement", "DesktopMark",
    "DesktopObservation", "DesktopPerception", "SetOfMarkManifest", "build_manifest",
    "capture_active_display", "denormalize_coordinates", "desktop_perception",
    "discover_windows", "fuse_elements", "normalize_coordinates",
]
