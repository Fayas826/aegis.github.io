"""Portable desktop perception primitives for AEGIS.

This module deliberately has no hard dependency on a display server, Pillow,
``mss`` or Windows UI Automation.  Those integrations are discovered only
when an observation is requested.  Callers therefore receive an explicit
``UNAVAILABLE`` observation instead of an invented screenshot or UI tree.
"""

from __future__ import annotations

import base64
import io
import math
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence


SUCCESS = "SUCCESS"
UNAVAILABLE = "UNAVAILABLE"
BLOCKED = "BLOCKED"
FAILED = "FAILED"


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def normalize_coordinates(x: float, y: float, width: int, height: int) -> tuple[float, float]:
    """Convert a pixel coordinate to inclusive ``[0, 1]`` coordinates.

    Coordinates outside the actual pixel rectangle are rejected.  Using
    ``width - 1``/``height - 1`` means the last pixel round-trips exactly and
    avoids an off-by-one click outside a display.
    """

    if width < 1 or height < 1 or not (_finite_number(x) and _finite_number(y)):
        raise ValueError("display dimensions and coordinates must be finite and positive")
    if not (0 <= float(x) <= width - 1 and 0 <= float(y) <= height - 1):
        raise ValueError("pixel coordinate is outside the display")
    return (float(x) / max(width - 1, 1), float(y) / max(height - 1, 1))


def denormalize_coordinates(x: float, y: float, width: int, height: int) -> tuple[int, int]:
    """Convert normalized coordinates to a clamped physical pixel position."""

    if width < 1 or height < 1 or not (_finite_number(x) and _finite_number(y)):
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

    @property
    def ok(self) -> bool:
        return self.status == SUCCESS

    @property
    def marks(self) -> tuple[DesktopMark, ...]:
        return self.manifest.marks if self.manifest else ()

    def to_dict(self) -> dict[str, Any]:
        return {
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
                }
                for element in self.elements
            ],
            "manifest": self.manifest.to_dict() if self.manifest else None,
            "timestamp": self.timestamp,
            "error": self.error,
            "sources": list(self.sources),
        }


def _coerce_element(raw: Any, index: int, source: str = "visual") -> Optional[DesktopElement]:
    """Accept existing ``GUIElement`` objects and plain provider dictionaries."""

    if isinstance(raw, DesktopElement):
        return raw
    if isinstance(raw, Mapping):
        get = raw.get
        element_id = get("element_id", get("id", f"{source}_{index}"))
        text = get("text", get("name", get("label", ""))) or ""
        role = get("role", get("element_type", get("control_type", "element"))) or "element"
        bbox = get("bbox")
        if bbox is None and all(key in raw for key in ("left", "top", "right", "bottom")):
            bbox = [raw["left"], raw["top"], raw["right"], raw["bottom"]]
        if bbox is None:
            center = get("center", [0, 0])
            bbox = [center[0], center[1], center[0], center[1]] if len(center) >= 2 else [0, 0, 0, 0]
        try:
            rect = tuple(int(round(float(value))) for value in bbox[:4])
        except (TypeError, ValueError, IndexError):
            return None
        if len(rect) != 4:
            return None
        return DesktopElement(
            element_id=str(element_id), text=str(text), role=str(role), bbox=rect,
            clickable=bool(get("clickable", True)), visible=bool(get("visible", True)),
            confidence=float(get("confidence", 1.0)), source=str(get("source", source)),
            metadata=dict(get("metadata", {})) if isinstance(get("metadata", {}), Mapping) else {},
        )
    # Existing core.perception.visual_state.GUIElement is intentionally handled
    # by attributes, without importing Pillow-heavy modules at import time.
    if hasattr(raw, "element_id") and hasattr(raw, "bbox"):
        return _coerce_element({
            "element_id": getattr(raw, "element_id"), "text": getattr(raw, "text", ""),
            "role": getattr(raw, "role", "element"), "bbox": getattr(raw, "bbox"),
            "clickable": getattr(raw, "clickable", True), "visible": getattr(raw, "visible", True),
            "confidence": getattr(raw, "confidence", 1.0), "source": getattr(raw, "source", source),
        }, index, source)
    return None


def fuse_elements(primary: Iterable[Any], uia_elements: Iterable[Any]) -> tuple[DesktopElement, ...]:
    """Fuse visual/provider elements with UIA, preferring UIA metadata on overlap."""

    visual: list[DesktopElement] = []
    for index, raw in enumerate(primary):
        item = _coerce_element(raw, index, "visual")
        if item is not None:
            visual.append(item)
    uia: list[DesktopElement] = []
    for index, raw in enumerate(uia_elements):
        item = _coerce_element(raw, index, "uia")
        if item is not None:
            uia.append(item)

    def overlap(a: DesktopElement, b: DesktopElement) -> float:
        al, at, ar, ab = a.bbox
        bl, bt, br, bb = b.bbox
        inter = max(0, min(ar, br) - max(al, bl)) * max(0, min(ab, bb) - max(at, bt))
        area_a = max(0, ar - al) * max(0, ab - at)
        area_b = max(0, br - bl) * max(0, bb - bt)
        return inter / max(1, min(area_a, area_b))

    result = list(visual)
    for item in uia:
        match = next((old for old in result if overlap(old, item) >= 0.5), None)
        if match is None:
            result.append(item)
        else:
            # Keep the visual rectangle (it is in the same display space), but
            # enrich it with UIA's semantic name/role and mark the provenance
            # explicitly.  This is a true fusion rather than two competing
            # entries in the Set-of-Marks manifest.
            result[result.index(match)] = DesktopElement(
                element_id=match.element_id, text=item.text or match.text,
                role=item.role or match.role,
                bbox=match.bbox, clickable=match.clickable or item.clickable,
                visible=match.visible and item.visible, confidence=max(match.confidence, item.confidence),
                source="fused", metadata={**match.metadata, **item.metadata},
            )
    return tuple(result)


def build_manifest(elements: Iterable[DesktopElement], width: int, height: int,
                   manifest_id: Optional[str] = None) -> SetOfMarkManifest:
    marks: list[DesktopMark] = []
    for element in elements:
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


def _default_capture() -> Mapping[str, Any]:
    """Capture through the existing AEGIS capturer, then portable fallbacks."""

    try:
        from core.perception.screen_capture import screen_capturer
        frame = screen_capturer.capture_frame()
        return {
            "width": int(frame.width), "height": int(frame.height),
            "image_b64": frame.image_b64, "active_window": frame.active_window,
            "active_process": frame.active_process,
        }
    except Exception as existing_error:
        # Fallback imports are intentionally local: headless Linux and minimal
        # installations must still import this module successfully.
        try:
            screenshot = None
            try:
                import mss  # type: ignore
                with mss.mss() as server:
                    monitor = server.monitors[1] if len(server.monitors) > 1 else server.monitors[0]
                    grabbed = server.grab(monitor)
                    from PIL import Image  # type: ignore
                    screenshot = Image.frombytes("RGB", grabbed.size, grabbed.bgra, "raw", "BGRX")
            except Exception:
                try:
                    import pyautogui  # type: ignore
                    screenshot = pyautogui.screenshot()
                except Exception:
                    from PIL import ImageGrab  # type: ignore
                    screenshot = ImageGrab.grab()
            output = io.BytesIO()
            screenshot.convert("RGB").save(output, format="PNG")
            return {"width": screenshot.width, "height": screenshot.height,
                    "image_b64": base64.b64encode(output.getvalue()).decode("ascii")}
        except Exception as fallback_error:
            raise RuntimeError(f"desktop capture unavailable: {fallback_error}") from existing_error


def _default_uia() -> tuple[Sequence[Any], Optional[str]]:
    if sys.platform != "win32":
        return (), "Windows UI Automation is unavailable on this platform"
    try:
        import uiautomation  # type: ignore  # noqa: F401
        from core.perception.accessibility import accessibility_provider
        return accessibility_provider.get_visible_elements(), None
    except ImportError as exc:
        return (), f"UI Automation package unavailable: {exc}"
    except Exception as exc:
        return (), f"UI Automation scan failed: {exc}"


class DesktopPerception:
    """Capture one fresh display observation and optionally fuse UIA controls."""

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
            return raw
        if not isinstance(raw, Mapping):
            return DesktopObservation(FAILED, timestamp=timestamp, error="capture provider returned an invalid observation")
        try:
            width, height = int(raw["width"]), int(raw["height"])
            if width < 1 or height < 1:
                raise ValueError("invalid display dimensions")
        except (KeyError, TypeError, ValueError) as exc:
            return DesktopObservation(FAILED, timestamp=timestamp, error=f"invalid capture dimensions: {exc}")
        primary = raw.get("elements", ())
        uia: Iterable[Any] = ()
        sources = ["display"]
        if include_uia:
            if self.uia_provider is not None:
                try:
                    uia = self.uia_provider() or ()
                    sources.append("uia")
                except Exception:
                    sources.append("uia_failed")
            else:
                uia, uia_error = _default_uia()
                if uia:
                    sources.append("uia")
                elif uia_error:
                    sources.append("uia_unavailable")
        elements = fuse_elements(primary, uia)
        manifest = build_manifest(elements, width, height)
        return DesktopObservation(
            SUCCESS, width, height, str(raw.get("image_b64", "")),
            str(raw.get("active_window", "Desktop")), str(raw.get("active_process", "none")),
            elements, manifest, timestamp, None, tuple(sources),
        )


desktop_perception = DesktopPerception()


def capture_active_display() -> DesktopObservation:
    """Convenience entry point used by integrations that need one observation."""

    return desktop_perception.observe()


__all__ = [
    "BLOCKED", "FAILED", "SUCCESS", "UNAVAILABLE", "DesktopElement", "DesktopMark",
    "DesktopObservation", "DesktopPerception", "SetOfMarkManifest", "build_manifest",
    "capture_active_display", "denormalize_coordinates", "desktop_perception",
    "fuse_elements", "normalize_coordinates",
]
