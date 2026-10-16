"""Bounded, evidence-first desktop text-file execution.

This module is intentionally independent from the large legacy task engine.  It
provides one reusable desktop workflow that can be called by an orchestrator:

    open application -> focus -> edit -> save -> close -> reopen -> read back

The file read-back is independent of the UI action report.  A successful key
press or process launch is therefore never reported as a verified user goal.
The workflow is also deliberately scoped to an allowed directory so a generic
desktop agent cannot silently turn a test into an arbitrary file writer.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence

from core.orchestration.agent_contract import ExecutionEnvironment, TaskContext
from core.orchestration.shared_task_junction import SharedTaskJunction, task_junction


Status = str
SleepFn = Callable[[float], Awaitable[None]]


@dataclass
class DesktopTaskResult:
    """JSON-friendly result returned by :class:`VerifiedDesktopTextTask`."""

    task_id: str
    status: Status
    goal: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "task_id": self.task_id,
            "status": self.status,
            "goal": self.goal,
            "message": self.message,
            "evidence": self.evidence,
        }
        if self.error:
            result["error"] = self.error
        return result


class _PyAutoGuiInput:
    """Small lazy input adapter so importing this module stays portable."""

    def __init__(self) -> None:
        import pyautogui  # type: ignore
        import pyperclip  # type: ignore

        self._pyautogui = pyautogui
        self._pyperclip = pyperclip

    def paste(self, text: str) -> None:
        self._pyperclip.copy(text)
        self._pyautogui.hotkey("ctrl", "v")

    def hotkey(self, *keys: str) -> None:
        self._pyautogui.hotkey(*keys)

    def press(self, key: str) -> None:
        self._pyautogui.press(key)


def _default_launcher(command: Sequence[str]) -> Any:
    return subprocess.Popen(list(command), shell=False)


def _split_command(command: str | Sequence[str]) -> list[str]:
    if isinstance(command, str):
        # ``posix=False`` preserves Windows executable/path quoting while still
        # avoiding shell=True and shell metacharacter interpretation.
        return shlex.split(command, posix=False)
    return [str(item) for item in command]


def _safe_observation(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): val for key, val in value.items() if _json_safe(val)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return {"items": [item for item in value if _json_safe(item)]}
    return {"value": str(value)} if value is not None else {}


def _json_safe(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, Mapping):
        return all(isinstance(key, (str, int, float, bool)) and _json_safe(item) for key, item in value.items())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return all(_json_safe(item) for item in value)
    return False


class VerifiedDesktopTextTask:
    """Execute and independently verify a bounded text-editor workflow.

    Dependencies are injectable so the offline test suite can exercise the
    state machine without pretending that a fake desktop is real evidence.
    Production callers should leave the adapters at their defaults.
    """

    def __init__(
        self,
        *,
        controller: Any = None,
        input_adapter: Any = None,
        launcher: Callable[[Sequence[str]], Any] = _default_launcher,
        junction: Optional[SharedTaskJunction] = None,
        observer: Optional[Callable[[], Any]] = None,
        screenshot: Optional[Callable[[str], bool]] = None,
        sleep: SleepFn = asyncio.sleep,
        platform_name: Optional[str] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.controller = controller if controller is not None else self._load_controller()
        self.input = input_adapter
        self.launcher = launcher
        # Persist production runs by default. Tests and special callers can
        # inject an isolated junction explicitly.
        self.junction = junction if junction is not None else task_junction
        self.observer = observer or self._default_observer
        self.screenshot = screenshot or self._default_screenshot
        self.sleep = sleep
        self.platform_name = platform_name or sys.platform
        self.clock = clock

    @staticmethod
    def _load_controller() -> Any:
        try:
            from core.execution.universal_desktop_controller import desktop_controller

            return desktop_controller
        except Exception:
            return None

    def _load_input(self) -> Any:
        if self.input is None:
            self.input = _PyAutoGuiInput()
        return self.input

    def _default_observer(self) -> dict[str, Any]:
        if self.controller is None or not hasattr(self.controller, "list_visible_windows"):
            return {"available": False, "reason": "desktop_controller_missing"}
        try:
            windows = self.controller.list_visible_windows()
            return {
                "available": True,
                "windows": [{"hwnd": hwnd, "title": title} for hwnd, title in windows],
                "timestamp": self.clock(),
            }
        except Exception as exc:
            return {"available": False, "reason": f"observation_failed:{exc}"}

    def _default_screenshot(self, path: str) -> bool:
        return bool(self.controller and self.controller.take_screenshot_safely(path))

    @staticmethod
    def _within_root(path: Path, roots: Sequence[Path]) -> bool:
        return any(path == root or root in path.parents for root in roots)

    def _validate_destination(
        self, destination: str | os.PathLike[str], allowed_roots: Sequence[str | os.PathLike[str]]
    ) -> tuple[Optional[Path], Optional[str]]:
        raw = Path(destination).expanduser()
        if not raw.is_absolute():
            return None, "DESTINATION_MUST_BE_ABSOLUTE"
        try:
            resolved = raw.resolve()
            roots = [Path(root).expanduser().resolve() for root in allowed_roots]
        except OSError as exc:
            return None, f"DESTINATION_RESOLUTION_FAILED:{exc}"
        if not roots:
            return None, "ALLOWED_ROOT_REQUIRED"
        if not self._within_root(resolved, roots):
            return None, "DESTINATION_OUTSIDE_ALLOWED_ROOT"
        return resolved, None

    def _goal(self, app_title: str, destination: Path, content: str) -> str:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return f"Save text in {app_title} to {destination} (sha256={digest})"

    def _cancelled(self, context: TaskContext) -> bool:
        if context.cancelled or context.is_expired():
            return True
        if self.junction:
            current = self.junction.get(context.task_id)
            return bool(current and current.get("status") == "CANCEL_REQUESTED")
        return False

    async def _wait_for_file(self, path: Path, timeout: float, context: TaskContext) -> bool:
        deadline = self.clock() + timeout
        while self.clock() <= deadline:
            if self._cancelled(context):
                return False
            if path.is_file():
                return True
            await self.sleep(0.1)
        return path.is_file()

    async def _settle_process(self, process: Any) -> None:
        if process is None or not hasattr(process, "poll"):
            return
        for _ in range(10):
            if process.poll() is not None:
                return
            await self.sleep(0.1)

    def _record(self, task_id: str, event: str, **details: Any) -> None:
        if self.junction:
            self.junction.record_event(task_id, event, evidence=details.pop("evidence", None), **details)

    def _complete(self, result: DesktopTaskResult) -> DesktopTaskResult:
        if self.junction:
            self.junction.complete(
                result.task_id,
                result.status,
                result.to_dict(),
                evidence={"type": "verified_desktop_task", **result.evidence},
            )
        return result

    async def run(
        self,
        *,
        app_command: str | Sequence[str],
        app_title: str,
        content: str,
        destination: str | os.PathLike[str],
        allowed_roots: Sequence[str | os.PathLike[str]],
        context: Optional[TaskContext] = None,
        reopen_command: Optional[str | Sequence[str]] = None,
        timeout_seconds: float = 15.0,
        evidence_dir: Optional[str | os.PathLike[str]] = None,
        save_dialog_title: str = "Save as",
        overwrite: bool = False,
        confirm_save_title: str = "Confirm Save As",
    ) -> DesktopTaskResult:
        """Run the workflow and return a truthful terminal outcome."""

        target, validation_error = self._validate_destination(destination, allowed_roots)
        task_context = context or TaskContext(
            goal=f"Save text in {app_title}",
            environment=ExecutionEnvironment.DESKTOP,
        )
        goal = self._goal(app_title, target or Path(destination), content)
        if self.junction:
            self.junction.start(task_context, "verified_desktop_task", {"destination": str(destination)})

        if validation_error:
            return self._complete(DesktopTaskResult(task_context.task_id, "BLOCKED", goal, validation_error, error=validation_error))
        if self.platform_name != "win32":
            reason = "DESKTOP_SESSION_UNAVAILABLE: Windows desktop execution requires win32"
            return self._complete(DesktopTaskResult(task_context.task_id, "UNAVAILABLE", goal, reason, error=reason))
        if self.controller is None:
            reason = "DESKTOP_SESSION_UNAVAILABLE: desktop controller is unavailable"
            return self._complete(DesktopTaskResult(task_context.task_id, "UNAVAILABLE", goal, reason, error=reason))
        if self._cancelled(task_context):
            return self._complete(DesktopTaskResult(task_context.task_id, "CANCELLED", goal, "Task cancelled before execution."))

        target.parent.mkdir(parents=True, exist_ok=True)
        existed_before = target.exists()
        if existed_before and not overwrite:
            reason = "DESTINATION_EXISTS: refusing to overwrite an existing file"
            return self._complete(DesktopTaskResult(task_context.task_id, "BLOCKED", goal, reason, error=reason))
        if existed_before:
            target.unlink()
        evidence: dict[str, Any] = {
            "destination": str(target),
            "existed_before": existed_before,
            "expected_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "before": _safe_observation(self.observer()),
            "actions": [],
        }
        process = None
        try:
            input_adapter = self._load_input()
            command = _split_command(app_command)
            if not command:
                raise ValueError("APP_COMMAND_REQUIRED")
            process = self.launcher(command)
            evidence["actions"].append({"action": "launch", "command": command, "reported": True})
            await self.sleep(0.8)
            hwnd = self.controller.focus_application(app_title, minimize_others=False)
            if not hwnd:
                raise RuntimeError("FOCUS_MISMATCH: application window was not found")
            evidence["actions"].append({"action": "focus", "window": app_title, "hwnd": hwnd})

            if self._cancelled(task_context):
                return self._complete(DesktopTaskResult(task_context.task_id, "CANCELLED", goal, "Task cancelled during execution.", evidence))
            input_adapter.hotkey("ctrl", "a")
            await self.sleep(0.2)
            input_adapter.press("backspace")
            await self.sleep(0.2)
            input_adapter.paste(content)
            await self.sleep(0.2)
            input_adapter.hotkey("ctrl", "s")
            # Saving is a second window-state transition.  Give the native
            # dialog time to appear and explicitly focus it before touching
            # the filename field; otherwise keystrokes can return to the
            # editor while a Save As dialog remains open in the foreground.
            await self.sleep(0.6)
            dialog_hwnd = self.controller.focus_application(save_dialog_title, minimize_others=False)
            if not dialog_hwnd:
                raise RuntimeError("FOCUS_MISMATCH: save dialog was not found")
            input_adapter.hotkey("ctrl", "a")
            await self.sleep(0.2)
            input_adapter.press("backspace")
            await self.sleep(0.2)
            input_adapter.paste(str(target))
            input_adapter.press("enter")
            await self.sleep(0.6)
            if existed_before:
                confirm_hwnd = self.controller.focus_application(confirm_save_title, minimize_others=False)
                if confirm_hwnd:
                    input_adapter.press("enter")
                    await self.sleep(0.6)
            evidence["actions"].append({"action": "type_and_save", "reported": True})

            if not await self._wait_for_file(target, timeout_seconds, task_context):
                if self._cancelled(task_context):
                    return self._complete(DesktopTaskResult(task_context.task_id, "CANCELLED", goal, "Task cancelled while waiting for save.", evidence))
                raise TimeoutError("TIMEOUT: saved file did not appear")
            actual = target.read_text(encoding="utf-8")
            actual_digest = hashlib.sha256(actual.encode("utf-8")).hexdigest()
            evidence.update({"actual_sha256": actual_digest, "bytes": target.stat().st_size})
            if actual != content:
                evidence["verification"] = {"status": "UNVERIFIED", "method": "independent_file_readback"}
                return self._complete(DesktopTaskResult(task_context.task_id, "UNVERIFIED", goal, "File exists but read-back content differs.", evidence))

            input_adapter.hotkey("alt", "f4")
            await self._settle_process(process)
            reopen = _split_command(reopen_command) if reopen_command is not None else [command[0]]
            reopen.append(str(target))
            reopened_process = self.launcher(reopen)
            await self.sleep(0.8)
            reopened_hwnd = self.controller.focus_application(app_title, minimize_others=False)
            if not reopened_hwnd:
                raise RuntimeError("FOCUS_MISMATCH: reopened application window was not found")
            evidence["actions"].append({"action": "close_and_reopen", "window": app_title, "hwnd": reopened_hwnd})
            reopened_content = target.read_text(encoding="utf-8")
            if reopened_content != content:
                evidence["verification"] = {"status": "UNVERIFIED", "method": "reopen_file_readback"}
                return self._complete(DesktopTaskResult(task_context.task_id, "UNVERIFIED", goal, "Reopened file content differs.", evidence))
            input_adapter.hotkey("alt", "f4")
            await self._settle_process(reopened_process)

            evidence["after"] = _safe_observation(self.observer())
            if evidence_dir:
                evidence_path = Path(evidence_dir).expanduser().resolve() / f"desktop_task_{task_context.task_id}.png"
                evidence_path.parent.mkdir(parents=True, exist_ok=True)
                if self.screenshot(str(evidence_path)):
                    evidence["screenshot"] = str(evidence_path)
            evidence["verification"] = {"status": "VERIFIED", "method": "independent_file_readback_after_reopen"}
            return self._complete(DesktopTaskResult(task_context.task_id, "VERIFIED", goal, "Text file saved, reopened, and independently verified.", evidence))
        except FileNotFoundError as exc:
            reason = f"UNAVAILABLE: application could not be launched: {exc}"
            return self._complete(DesktopTaskResult(task_context.task_id, "UNAVAILABLE", goal, reason, evidence, reason))
        except Exception as exc:
            evidence.setdefault("after", _safe_observation(self.observer()))
            return self._complete(DesktopTaskResult(task_context.task_id, "FAILED", goal, "Desktop execution failed.", evidence, str(exc)))
        finally:
            await self._settle_process(process)


async def run_verified_desktop_text_task(**kwargs: Any) -> dict[str, Any]:
    """Convenience entrypoint for orchestrators and adapters."""

    return (await VerifiedDesktopTextTask().run(**kwargs)).to_dict()
