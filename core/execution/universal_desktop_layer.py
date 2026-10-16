"""Bounded, observation-verified desktop actions for AEGIS.

The executor is dependency-injected for tests and alternate desktop drivers.
The default driver is discovered lazily, so importing AEGIS on a headless or
non-Windows machine is safe and actions report ``UNAVAILABLE`` honestly.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from core.perception.universal_desktop_layer import (
    BLOCKED, FAILED, SUCCESS, UNAVAILABLE, DesktopElement, DesktopMark,
    DesktopObservation, DesktopPerception, denormalize_coordinates,
    normalize_coordinates,
)


@dataclass
class DesktopActionResult:
    status: str
    action: str
    detail: str = ""
    before: Optional[DesktopObservation] = None
    after: Optional[DesktopObservation] = None
    error: Optional[str] = None
    backend: str = ""
    timestamp: float = field(default_factory=time.time)

    @property
    def success(self) -> bool:
        return self.status == SUCCESS

    @property
    def before_state(self) -> Optional[DesktopObservation]:
        return self.before

    @property
    def after_state(self) -> Optional[DesktopObservation]:
        return self.after

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status, "action": self.action, "detail": self.detail,
            "error": self.error, "backend": self.backend, "success": self.success,
            "before": self.before.to_dict() if self.before else None,
            "after": self.after.to_dict() if self.after else None,
            "timestamp": self.timestamp,
        }


class _PyAutoGUIBackend:
    name = "pyautogui"

    def __init__(self) -> None:
        import pyautogui  # type: ignore
        pyautogui.FAILSAFE = True
        self.pg = pyautogui

    def click(self, x: int, y: int) -> None:
        self.pg.click(x=x, y=y)

    def type_text(self, text: str) -> None:
        self.pg.write(text, interval=0.01)

    def hotkey(self, keys: Sequence[str]) -> None:
        self.pg.hotkey(*keys)

    def scroll(self, amount: int) -> None:
        self.pg.scroll(amount)

    def drag(self, start: tuple[int, int], end: tuple[int, int], duration: float) -> None:
        self.pg.moveTo(*start)
        self.pg.mouseDown()
        try:
            self.pg.moveTo(*end, duration=duration)
        finally:
            self.pg.mouseUp()


def _default_backend() -> Any:
    try:
        return _PyAutoGUIBackend()
    except Exception:
        return None


def _safe_text(text: Any, limit: int = 4096) -> Optional[str]:
    if not isinstance(text, str) or "\x00" in text or len(text) > limit:
        return None
    return text


class DesktopActionLayer:
    """Execute only bounded actions against a freshly observed display."""

    def __init__(self, perception: Optional[DesktopPerception] = None,
                 backend: Any = None, observer: Optional[Callable[[], DesktopObservation]] = None,
                 launcher: Optional[Callable[[str, Sequence[str]], Any]] = None):
        self.perception = perception or DesktopPerception()
        self.backend = backend
        self.observer = observer or self.perception.observe
        self.launcher = launcher or self._launch_process

    def _fresh(self) -> DesktopObservation:
        result = self.observer()
        if isinstance(result, DesktopObservation):
            return result
        return DesktopObservation(FAILED, error="observer returned an invalid observation")

    def _result(self, action: str, status: str, detail: str, before: Optional[DesktopObservation] = None,
                after: Optional[DesktopObservation] = None, error: Optional[str] = None) -> DesktopActionResult:
        return DesktopActionResult(status, action, detail, before, after, error,
                                   getattr(self.backend, "name", "") if self.backend else "")

    def _with_observation(self, action: str, operation: Callable[[], Any],
                          validate: Optional[Callable[[DesktopObservation], Optional[str]]] = None) -> DesktopActionResult:
        before = self._fresh()
        if before.status != SUCCESS:
            return self._result(action, before.status, "fresh pre-action observation unavailable", before, error=before.error)
        if validate:
            reason = validate(before)
            if reason:
                return self._result(action, BLOCKED, reason, before)
        try:
            operation()
        except Exception as exc:
            after = self._fresh()
            return self._result(action, FAILED, "desktop backend failed", before, after, str(exc))
        after = self._fresh()
        if after.status != SUCCESS:
            return self._result(action, FAILED, "action ran but post-action observation is unavailable",
                                before, after, after.error)
        return self._result(action, SUCCESS, "action executed and a fresh post-action observation was captured",
                            before, after)

    def _driver(self) -> Any:
        if self.backend is None:
            self.backend = _default_backend()
        return self.backend

    @staticmethod
    def _find_element(observation: DesktopObservation, target: Any) -> Optional[DesktopElement]:
        if isinstance(target, DesktopMark):
            target_id = target.element_id
        elif isinstance(target, DesktopElement):
            target_id = target.element_id
        elif isinstance(target, Mapping):
            target_id = target.get("element_id", target.get("id"))
        else:
            target_id = None
        if target_id is not None:
            return next((element for element in observation.elements if element.element_id == str(target_id)), None)
        try:
            number = int(target)
        except (TypeError, ValueError):
            return None
        mark = observation.manifest.get(number) if observation.manifest else None
        return next((element for element in observation.elements if mark and element.element_id == mark.element_id), None)

    def click_element(self, target: Any) -> DesktopActionResult:
        def validate(observation: DesktopObservation) -> Optional[str]:
            element = self._find_element(observation, target)
            if element is None:
                return "target element is not present in the fresh observation"
            if not element.visible or not element.clickable:
                return "target element is not a visible clickable control"
            left, top, right, bottom = element.bbox
            if left < 0 or top < 0 or right <= left or bottom <= top:
                return "target element has an invalid bounding box"
            try:
                denormalize_coordinates(*normalize_coordinates(*element.center, observation.width, observation.height), observation.width, observation.height)
            except ValueError:
                return "target element has an unsafe coordinate"
            return None

        def operation() -> None:
            observation = self._fresh()  # resolved again immediately before input
            element = self._find_element(observation, target)
            if element is None:
                raise RuntimeError("target became stale before click")
            driver = self._driver()
            if driver is None:
                raise RuntimeError("desktop input backend unavailable")
            driver.click(*element.center)

        before = self._fresh()
        if before.status != SUCCESS:
            return self._result("click_element", before.status, "fresh pre-action observation unavailable", before, error=before.error)
        reason = validate(before)
        if reason:
            return self._result("click_element", BLOCKED, reason, before)
        driver = self._driver()
        if driver is None:
            return self._result("click_element", UNAVAILABLE, "desktop input backend unavailable", before)
        # Keep the already captured observation as the pre-state and avoid
        # hiding a stale-target failure behind a generic backend exception.
        try:
            element = self._find_element(self._fresh(), target)
            if element is None:
                return self._result("click_element", BLOCKED, "target became stale before click", before)
            driver.click(*element.center)
        except Exception as exc:
            after = self._fresh()
            return self._result("click_element", FAILED, "desktop backend failed", before, after, str(exc))
        after = self._fresh()
        if after.status != SUCCESS:
            return self._result("click_element", FAILED, "action ran but post-action observation is unavailable", before, after, after.error)
        return self._result("click_element", SUCCESS, "element clicked and state re-observed", before, after)

    def click_normalized(self, x: float, y: float) -> DesktopActionResult:
        def operation() -> None:
            before = self._fresh()
            px, py = denormalize_coordinates(x, y, before.width, before.height)
            driver = self._driver()
            if driver is None:
                raise RuntimeError("desktop input backend unavailable")
            driver.click(px, py)

        def validate(observation: DesktopObservation) -> Optional[str]:
            try:
                denormalize_coordinates(x, y, observation.width, observation.height)
            except ValueError as exc:
                return str(exc)
            return None
        before = self._fresh()
        if before.status != SUCCESS:
            return self._result("click_normalized", before.status, "fresh pre-action observation unavailable", before, error=before.error)
        reason = validate(before)
        if reason:
            return self._result("click_normalized", BLOCKED, reason, before)
        if self._driver() is None:
            return self._result("click_normalized", UNAVAILABLE, "desktop input backend unavailable", before)
        try:
            px, py = denormalize_coordinates(x, y, before.width, before.height)
            self.backend.click(px, py)
        except Exception as exc:
            return self._result("click_normalized", FAILED, "desktop backend failed", before, self._fresh(), str(exc))
        after = self._fresh()
        return self._result("click_normalized", SUCCESS if after.status == SUCCESS else FAILED,
                            "normalized coordinate clicked" if after.status == SUCCESS else "post-action observation unavailable",
                            before, after, after.error)

    def type_text(self, text: str, paste: bool = False) -> DesktopActionResult:
        clean = _safe_text(text)
        before = self._fresh()
        if before.status != SUCCESS:
            return self._result("type_text", before.status, "fresh pre-action observation unavailable", before, error=before.error)
        if clean is None:
            return self._result("type_text", BLOCKED, "text must be a NUL-free string of at most 4096 characters", before)
        if self._driver() is None:
            return self._result("type_text", UNAVAILABLE, "desktop input backend unavailable", before)
        try:
            if paste:
                import pyperclip  # type: ignore
                pyperclip.copy(clean)
                self._send_hotkey(["ctrl", "v"])
            else:
                self.backend.type_text(clean)
        except ImportError as exc:
            return self._result("type_text", UNAVAILABLE, "clipboard paste backend unavailable", before, error=str(exc))
        except Exception as exc:
            return self._result("type_text", FAILED, "desktop backend failed", before, self._fresh(), str(exc))
        after = self._fresh()
        return self._result("type_text", SUCCESS if after.status == SUCCESS else FAILED,
                            "text entered" if after.status == SUCCESS else "post-action observation unavailable", before, after, after.error)

    def paste_text(self, text: str) -> DesktopActionResult:
        return self.type_text(text, paste=True)

    def hotkey(self, keys: Sequence[str]) -> DesktopActionResult:
        allowed = re.compile(r"^(?:[a-z0-9]|ctrl|alt|shift|win|enter|tab|escape|esc|backspace|delete|space|home|end|up|down|left|right|f(?:[1-9]|1[0-2]))$", re.I)
        normalized = [str(key).strip().lower() for key in keys] if isinstance(keys, Sequence) and not isinstance(keys, str) else []
        before = self._fresh()
        if before.status != SUCCESS:
            return self._result("hotkey", before.status, "fresh pre-action observation unavailable", before, error=before.error)
        if not normalized or len(normalized) > 4 or not all(allowed.match(key) for key in normalized):
            return self._result("hotkey", BLOCKED, "hotkey must contain 1-4 recognized keys", before)
        if self._driver() is None:
            return self._result("hotkey", UNAVAILABLE, "desktop input backend unavailable", before)
        try:
            self._send_hotkey(normalized)
        except Exception as exc:
            return self._result("hotkey", FAILED, "desktop backend failed", before, self._fresh(), str(exc))
        after = self._fresh()
        return self._result("hotkey", SUCCESS if after.status == SUCCESS else FAILED, "hotkey sent", before, after, after.error)

    def _send_hotkey(self, keys: Sequence[str]) -> None:
        """Accept both the layer backend contract and pyautogui-style fakes."""
        try:
            self.backend.hotkey(keys)
        except TypeError:
            self.backend.hotkey(*keys)

    def scroll(self, amount: int) -> DesktopActionResult:
        before = self._fresh()
        if before.status != SUCCESS:
            return self._result("scroll", before.status, "fresh pre-action observation unavailable", before, error=before.error)
        if isinstance(amount, bool) or not isinstance(amount, int) or amount == 0 or abs(amount) > 20:
            return self._result("scroll", BLOCKED, "scroll amount must be an integer from -20 to 20 excluding zero", before)
        if self._driver() is None:
            return self._result("scroll", UNAVAILABLE, "desktop input backend unavailable", before)
        try:
            self.backend.scroll(amount)
        except Exception as exc:
            return self._result("scroll", FAILED, "desktop backend failed", before, self._fresh(), str(exc))
        after = self._fresh()
        return self._result("scroll", SUCCESS if after.status == SUCCESS else FAILED, "scroll sent", before, after, after.error)

    def drag(self, start: tuple[float, float], end: tuple[float, float], duration: float = 0.2) -> DesktopActionResult:
        before = self._fresh()
        if before.status != SUCCESS:
            return self._result("drag", before.status, "fresh pre-action observation unavailable", before, error=before.error)
        if not isinstance(duration, (int, float)) or isinstance(duration, bool) or not 0 <= duration <= 5:
            return self._result("drag", BLOCKED, "drag duration must be between 0 and 5 seconds", before)
        try:
            start_px = denormalize_coordinates(start[0], start[1], before.width, before.height)
            end_px = denormalize_coordinates(end[0], end[1], before.width, before.height)
        except (TypeError, IndexError, ValueError) as exc:
            return self._result("drag", BLOCKED, f"unsafe drag coordinates: {exc}", before)
        if self._driver() is None:
            return self._result("drag", UNAVAILABLE, "desktop input backend unavailable", before)
        try:
            self.backend.drag(start_px, end_px, float(duration))
        except Exception as exc:
            return self._result("drag", FAILED, "desktop backend failed", before, self._fresh(), str(exc))
        after = self._fresh()
        return self._result("drag", SUCCESS if after.status == SUCCESS else FAILED, "drag sent", before, after, after.error)

    @staticmethod
    def _launch_process(program: str, args: Sequence[str]) -> Any:
        return subprocess.Popen([program, *args], shell=False, close_fds=True)

    def launch(self, program: str, args: Sequence[str] = ()) -> DesktopActionResult:
        before = self._fresh()
        if before.status != SUCCESS:
            return self._result("launch", before.status, "fresh pre-action observation unavailable", before, error=before.error)
        if not isinstance(program, str) or not program.strip() or "\x00" in program or len(program) > 260:
            return self._result("launch", BLOCKED, "program must be a non-empty bounded executable name", before)
        if not isinstance(args, Sequence) or isinstance(args, str) or len(args) > 16:
            return self._result("launch", BLOCKED, "launch accepts at most 16 argument strings", before)
        clean_args = []
        for arg in args:
            if not isinstance(arg, str) or "\x00" in arg or len(arg) > 1024:
                return self._result("launch", BLOCKED, "launch arguments must be bounded strings", before)
            clean_args.append(arg)
        try:
            self.launcher(program.strip(), clean_args)
        except FileNotFoundError as exc:
            return self._result("launch", FAILED, "program was not found", before, self._fresh(), str(exc))
        except Exception as exc:
            return self._result("launch", FAILED, "program launch failed", before, self._fresh(), str(exc))
        after = self._fresh()
        return self._result("launch", SUCCESS if after.status == SUCCESS else FAILED,
                            "program launched" if after.status == SUCCESS else "post-launch observation unavailable",
                            before, after, after.error)


desktop_actions = DesktopActionLayer()


__all__ = ["DesktopActionLayer", "DesktopActionResult", "desktop_actions"]
