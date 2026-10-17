"""Bounded, observation-verified desktop actions for AEGIS.

The executor is dependency-injected for tests and alternate desktop drivers.
The default driver is discovered lazily, so importing AEGIS on a headless or
non-Windows machine is safe and actions report ``UNAVAILABLE`` honestly.
"""

from __future__ import annotations

import re
import os
import sys
import math
import inspect
import ntpath
import copy
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

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> None:
        self.pg.click(x=x, y=y, button=button, clicks=clicks, interval=0.08 if clicks > 1 else 0)

    def move(self, x: int, y: int) -> None:
        self.pg.moveTo(x, y)

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


# Deny execution surfaces, not a small allowlist of GUI application names.
# Python/shell flags cannot turn a desktop launch into model-authored code.
_EXECUTION_HOST = re.compile(
    r"^(?:python(?:w|\d+(?:\.\d+)*)?|pyw?|pypy\d*|powershell(?:_ise)?|pwsh|cmd|command|"
    r"wscript|cscript|mshta|rundll32|regsvr32|regasm|regsvcs|installutil|msbuild|"
    r"csc|vbc|jsc|csi|dotnet|node|nodejs|deno|bun|ruby|perl|php|javaw?|jshell|lua\d*|"
    r"bash|sh|zsh|fish|dash|ksh|busybox|wsl|wt|conhost|openconsole|terminal|windowsterminal|"
    r"ssh|sftp|ftp|curl|wget|certutil|bitsadmin|wmic|schtasks|at|sc|reg|msiexec|"
    r"forfiles|pcalua|cmstp|hh|msdt|ieexec|msxsl|mavinject|odbcconf|presentationhost|"
    r"addinprocess(?:32)?|addinutil|dfsvc|scrcons|wmiprvse|control|explorer|osascript|open|xdg-open|env|sudo|su|"
    r"taskkill|tasklist|net|netsh|net1|runas|diskpart|bcdedit|cdb|windbg|debug)\Z", re.I)
_SCRIPT_EXTENSIONS = {".bat", ".cmd", ".ps1", ".psm1", ".py", ".pyw", ".js", ".jse", ".vbs",
                      ".vbe", ".wsf", ".wsh", ".hta", ".sh", ".exe", ".com", ".scr", ".dll",
                      ".msi", ".msp", ".reg", ".lnk", ".url", ".appref-ms", ".desktop"}
_MAX_ITEMS = 200


def _safe_program(program: Any) -> bool:
    if not isinstance(program, str) or not program.strip() or len(program) > 1024:
        return False
    if any(c in program for c in '\x00\r\n"<>|&;`'):
        return False
    base = ntpath.basename(program.strip())
    stem, ext = ntpath.splitext(base)
    return bool(base and not base.startswith(("-", "/")) and
                (ext.lower() in ("", ".exe", ".lnk") or ext.lower() not in _SCRIPT_EXTENSIONS) and
                not _EXECUTION_HOST.fullmatch(stem) and
                not re.match(r"^(?:pythonw?|pypy|powershell|pwsh|node)[\d._-]", stem, re.I))


def _safe_executable(path: Any) -> bool:
    """Check aliases/symlinks as well as their final target before dispatch."""
    if not _safe_program(path) or str(path).lower().endswith(".lnk"):
        return False
    resolved = os.path.realpath(path)
    if not _safe_program(resolved) or not os.path.isfile(resolved):
        return False
    if sys.platform == "win32":
        return resolved.lower().endswith(".exe")
    # Do not execute shebang scripts or shell association types via PATH on Unix.
    try:
        with open(resolved, "rb") as stream:
            magic = stream.read(4)
        return os.access(resolved, os.X_OK) and magic in (b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe")
    except OSError:
        return False


def _safe_packaged(name: str, aumid: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+![A-Za-z0-9_.-]+", aumid):
        return False
    compact = re.sub(r"[^a-z0-9]", "", name.casefold())
    if (_EXECUTION_HOST.fullmatch(compact) or compact in ("commandprompt", "windowspowershell") or
            any(compact.startswith(host) for host in ("windowsterminal", "powershell", "python", "pypy"))):
        return False
    family = aumid.split("!", 1)[0].split("_", 1)[0]
    return not any(_EXECUTION_HOST.fullmatch(token) or
                   token.casefold().startswith(("windowsterminal", "powershell", "python", "pypy"))
                   for token in family.split("."))


def _document_args(args: Any) -> Optional[list[str]]:
    """Only literal existing document paths; never flags, URLs or scripts."""
    if not isinstance(args, Sequence) or isinstance(args, (str, bytes)) or len(args) > 16:
        return None
    result = []
    for arg in args:
        if (not isinstance(arg, str) or not arg or len(arg) > 1024 or
                any(c in arg for c in '\x00\r\n"') or arg.startswith(("-", "\\\\", "//"))):
            return None
        path = os.path.abspath(arg)
        if not os.path.isfile(path) or ntpath.splitext(path)[1].lower() in _SCRIPT_EXTENSIONS:
            return None
        result.append(path)
    return result


def _windows_api() -> tuple[Any, Any, Any]:
    """Lazy, pointer-width-correct Win32 handles (also safe to import on Linux)."""
    import ctypes
    from ctypes import wintypes as wt
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.GetForegroundWindow.restype = wt.HWND
    user.IsWindow.argtypes = [wt.HWND]
    user.IsWindowVisible.argtypes = [wt.HWND]
    user.GetWindowTextLengthW.argtypes = [wt.HWND]
    user.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
    user.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
    user.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    user.SetForegroundWindow.argtypes = [wt.HWND]
    user.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
    user.IsIconic.argtypes = [wt.HWND]
    user.BringWindowToTop.argtypes = [wt.HWND]
    user.BringWindowToTop.restype = wt.BOOL
    user.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
    user.AttachThreadInput.restype = wt.BOOL
    kernel.GetCurrentThreadId.restype = wt.DWORD
    kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel.OpenProcess.restype = wt.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    return ctypes, user, kernel


def _native_windows() -> list[dict[str, Any]]:
    if sys.platform != "win32":
        return []
    ctypes, user, kernel = _windows_api()
    from ctypes import wintypes as wt
    windows: list[dict[str, Any]] = []
    foreground = int(user.GetForegroundWindow() or 0)
    callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def visit(handle: Any, _: Any) -> bool:
        if len(windows) >= _MAX_ITEMS:
            return False
        if not user.IsWindowVisible(handle):
            return True
        length = min(4096, user.GetWindowTextLengthW(handle))
        if length <= 0:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user.GetWindowTextW(handle, title, len(title))
        pid, rect = wt.DWORD(), wt.RECT()
        user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
        user.GetWindowRect(handle, ctypes.byref(rect))
        path = ""
        process = kernel.OpenProcess(0x1000, False, pid.value)
        if process:
            try:
                buffer, size = ctypes.create_unicode_buffer(32768), wt.DWORD(32768)
                if kernel.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                    path = buffer.value
            finally:
                kernel.CloseHandle(process)
        windows.append({"window_id": int(handle), "title": title.value, "process_id": pid.value,
                        "process": ntpath.basename(path), "path": path,
                        "bounds": (rect.left, rect.top, rect.right, rect.bottom),
                        "foreground": int(handle) == foreground, "visible": True})
        return True

    callback = callback_type(visit)
    user.EnumWindows.argtypes = [callback_type, wt.LPARAM]
    if not user.EnumWindows(callback, 0) and len(windows) < _MAX_ITEMS:
        error = ctypes.get_last_error()
        if error:
            raise OSError(error, "EnumWindows failed")
    return windows


def _native_focus(window_id: int) -> None:
    ctypes, user, kernel = _windows_api()
    from ctypes import wintypes as wt
    if not user.IsWindow(window_id):
        raise RuntimeError("window no longer exists")
    if user.IsIconic(window_id):
        user.ShowWindow(window_id, 9)  # SW_RESTORE
    else:
        user.ShowWindow(window_id, 5)  # SW_SHOW
    fg = user.GetForegroundWindow()
    attached = False
    cur_tid = 0
    fg_tid = 0
    if fg and fg != window_id:
        try:
            cur_tid = kernel.GetCurrentThreadId()
            fg_pid = wt.DWORD()
            fg_tid = user.GetWindowThreadProcessId(fg, ctypes.byref(fg_pid))
            if cur_tid and fg_tid and cur_tid != fg_tid:
                user.AttachThreadInput(cur_tid, fg_tid, True)
                attached = True
        except Exception:
            attached = False
    try:
        user.BringWindowToTop(window_id)
        user.SetForegroundWindow(window_id)
    finally:
        if attached:
            try:
                user.AttachThreadInput(cur_tid, fg_tid, False)
            except Exception:
                pass


def _shortcut_details(path: str) -> Optional[dict[str, Any]]:
    """Read structured ShellLink properties, never execute shortcut command text.

    pywin32 is optional. Unresolved links are inventory-only, fail closed at launch.
    Resolve() is deliberately not called (it can search network paths / show UI).
    """
    try:
        import pythoncom  # type: ignore
        from win32com.shell import shell  # type: ignore
        pythoncom.CoInitialize()
        try:
            link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None,
                                              pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
            link.QueryInterface(pythoncom.IID_IPersistFile).Load(path)
            target = link.GetPath(4)[0]  # SLGP_RAWPATH
            arguments = link.GetArguments()
            return {"target": os.path.expandvars(target), "arguments": arguments}
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        return None


def _installed_applications() -> tuple[list[dict[str, Any]], list[str]]:
    """Read bounded App Paths, Start Menu links and packaged StartApps inventories."""
    items: list[dict[str, Any]] = []
    diagnostics: list[str] = []
    if sys.platform != "win32":
        return items, diagnostics
    # File Explorer is a Windows shell application, not reliably represented
    # by a resolvable Start Menu shortcut. Expose the fixed OS executable as a
    # structured discovered identity; no model-provided command is accepted.
    explorer = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")
    if os.path.isfile(explorer):
        items.append({"name": "File Explorer", "path": explorer, "source": "windows_shell",
                      "kind": "windows_shell", "launchable": True})
    try:
        import winreg
        subkey = r"Software\Microsoft\Windows\CurrentVersion\App Paths"
        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | view) as key:
                        for index in range(min(winreg.QueryInfoKey(key)[0], 1024)):
                            name = winreg.EnumKey(key, index)
                            try:
                                with winreg.OpenKey(key, name) as app:
                                    path = os.path.expandvars(str(winreg.QueryValueEx(app, "")[0])).strip('"')
                                if _safe_executable(path):
                                    items.append({"name": ntpath.splitext(name)[0], "path": path,
                                                  "source": "app_paths", "kind": "executable", "launchable": True})
                            except OSError:
                                continue
                except OSError:
                    continue
    except Exception as exc:
        diagnostics.append(f"App Paths unavailable: {exc}")
    visited = 0
    for variable in ("APPDATA", "PROGRAMDATA"):
        parent = os.environ.get(variable)
        if not parent:
            continue
        folder = os.path.join(parent, "Microsoft", "Windows", "Start Menu", "Programs")
        for directory, dirs, files in os.walk(folder, followlinks=False):
            dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(directory, d))]
            visited += 1
            if visited > 2048:
                break
            for filename in files:
                if not filename.lower().endswith(".lnk"):
                    continue
                path = os.path.join(directory, filename)
                details = _shortcut_details(path)
                safe = bool(details and not details["arguments"] and
                            _safe_executable(details["target"]))
                items.append({"name": filename[:-4], "path": path, "source": "start_menu",
                              "kind": "shortcut", "launchable": safe, **(details or {})})
                if len(items) >= 4096:
                    break
            if len(items) >= 4096:
                break
    # Fixed read-only script, trusted system executable, no query/model interpolation.
    powershell = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                              "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    try:
        import json
        response = subprocess.run([powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                                   "Get-StartApps | Select-Object -First 2048 Name,AppID | ConvertTo-Json -Compress"],
                                  capture_output=True, text=True, timeout=5, shell=False,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if response.returncode == 0 and len(response.stdout) <= 1024 * 1024:
            entries = json.loads(response.stdout or "[]")
            for entry in ([entries] if isinstance(entries, dict) else entries)[:2048]:
                appid = entry.get("AppID", "")
                # Only packaged AUMIDs, not arbitrary shell namespace commands or paths.
                name = str(entry.get("Name", appid))[:256]
                if isinstance(appid, str) and _safe_packaged(name, appid):
                    items.append({"name": name, "aumid": appid,
                                  "kind": "packaged", "source": "start_apps", "launchable": True})
        elif response.returncode:
            diagnostics.append("Get-StartApps unavailable")
    except Exception as exc:
        diagnostics.append(f"StartApps unavailable: {exc}")
    return items, diagnostics


class _ObservationBinding:
    def __init__(self, layer: Any, previous: Any):
        self.layer, self.previous = layer, previous

    def __enter__(self) -> Any:
        return self.layer

    def __exit__(self, *_: Any) -> None:
        self.layer._bound = self.previous


class DesktopActionLayer:
    """Bounded desktop inputs and inventory-grounded launches.

    ``bind_observation(obs)`` binds the model's decision snapshot immediately;
    it also returns a context manager which restores the previous binding.
    Bind a new snapshot for each decision. Without one, the first fresh capture
    is the decision snapshot. A second capture must still match before input.
    The optional guard accepts the action name (or no arguments), and may
    return False / raise to deny an effect. It is never retried on an error.
    An injected launcher is a trusted test/driver boundary, so executable
    existence is not required there; unsafe execution hosts/arguments still are.
    """

    def __init__(self, perception: Optional[DesktopPerception] = None,
                 backend: Any = None, observer: Optional[Callable[[], DesktopObservation]] = None,
                 launcher: Optional[Callable[[str, Sequence[str]], Any]] = None,
                 action_guard: Optional[Callable[..., Any]] = None,
                 window_provider: Optional[Callable[[], Sequence[Mapping[str, Any]]]] = None,
                 window_focuser: Optional[Callable[[int], Any]] = None):
        self.perception = perception or DesktopPerception()
        self.backend = backend
        self.observer = observer or self.perception.observe
        self.launcher = launcher or self._launch_process
        self._injected_launcher = launcher is not None
        self.action_guard = action_guard
        self.window_provider, self.window_focuser = window_provider, window_focuser
        self._bound: Optional[DesktopObservation] = None
        self._pending_clipboard: Optional[tuple[Any, str]] = None

    def bind_observation(self, observation: Optional[DesktopObservation]) -> _ObservationBinding:
        if observation is not None and not isinstance(observation, DesktopObservation):
            raise ValueError("decision binding requires a DesktopObservation or None")
        previous = self._bound
        # Deep copying prevents mutable capture adapters changing a past decision.
        self._bound = copy.deepcopy(observation)
        return _ObservationBinding(self, previous)

    def _fresh(self) -> DesktopObservation:
        try:
            result = self.observer()
            if isinstance(result, DesktopObservation):
                return copy.deepcopy(result)
            return DesktopObservation(FAILED, error="observer returned an invalid observation")
        except Exception as exc:
            return DesktopObservation(UNAVAILABLE, error=f"observation failed: {exc}")

    def _result(self, action: str, status: str, detail: str, before: Optional[DesktopObservation] = None,
                after: Optional[DesktopObservation] = None, error: Optional[str] = None) -> DesktopActionResult:
        return DesktopActionResult(status, action, detail, before, after, error,
                                   getattr(self.backend, "name", "") if self.backend else "")

    @staticmethod
    def _signature(observation: DesktopObservation, pixel_exact: bool = True, controls_exact: bool = True) -> tuple[Any, ...]:
        # IDs and timestamps identify captures, not their stable desktop state.
        # Keep geometry/window checks even when a provider's fingerprint omits them.
        return (getattr(observation, "state_fingerprint", None) if pixel_exact or (controls_exact and not observation.elements) else None,
                observation.width, observation.height, tuple(getattr(observation, "origin", (0, 0))),
                getattr(observation, "window_id", 0), tuple(getattr(observation, "window_bounds", ())),
                getattr(observation, "process_id", 0), observation.active_window, observation.active_process,
                observation.image_b64 if getattr(observation, "state_fingerprint", None) is None else None,
                tuple((e.element_id, e.text, e.role, tuple(e.bbox), e.visible, e.clickable,
                       getattr(e, "enabled", True), repr(getattr(e, "metadata", {})))
                      for e in observation.elements) if controls_exact else ())

    def _stale(self, current: DesktopObservation, expected: DesktopObservation, pixel_exact: bool = True, controls_exact: bool = True) -> Optional[str]:
        if current.status != SUCCESS or expected.status != SUCCESS:
            return "fresh successful observation required before input"
        try:
            if self._signature(current, pixel_exact, controls_exact) != self._signature(expected, pixel_exact, controls_exact):
                return "decision observation is stale: fingerprint, window or geometry changed"
        except Exception:
            return "observation grounding is invalid"
        return None

    def _guard(self, action: str) -> Optional[str]:
        if self.action_guard is None:
            return None
        try:
            signature = inspect.signature(self.action_guard)
            try:
                signature.bind(action)
                args = (action,)
            except TypeError:
                signature.bind()
                args = ()
            allowed = self.action_guard(*args)
            if allowed is False:
                return "action guard denied permission or cancellation was requested"
        except Exception as exc:
            return f"action guard failed closed: {exc}"
        return None

    def _driver(self) -> Any:
        if self.backend is None:
            self.backend = _default_backend()
        return self.backend

    def _perform(self, action: str, operation: Callable[[DesktopObservation], Any],
                 validate: Optional[Callable[[DesktopObservation], Optional[str]]] = None,
                 needs_driver: bool = True) -> DesktopActionResult:
        before = self._fresh()
        if before.status != SUCCESS:
            return self._result(action, before.status, "fresh pre-action observation unavailable", before, error=before.error)
        expected = self._bound if self._bound is not None else before
        # Coordinate decisions need pixel-exact snapshots. Semantic IDs, keys
        # and focus changes instead require identical native/control state;
        # a blinking caret alone must not make typing impossible.
        pixel_exact = action in {"click_normalized", "move_normalized", "drag"}
        # Inventory-grounded window operations do not target the old screen's
        # controls. Still require unchanged foreground identity/geometry and
        # revalidate the exact app/window/path, but tolerate a blinking spinner.
        controls_exact = action not in {
            "launch", "focus_window", "open_folder", "wait", "type_text", "hotkey", "scroll"
        }
        reason = self._stale(before, expected, pixel_exact, controls_exact)
        if not reason and validate:
            try:
                reason = validate(before)
            except Exception as exc:
                reason = f"invalid action parameters: {exc}"
        if reason:
            return self._result(action, BLOCKED, reason, before)
        if needs_driver and self._driver() is None:
            return self._result(action, UNAVAILABLE, "desktop input backend unavailable", before)
        current = self._fresh() if self._bound is not None else before
        reason = self._stale(current, expected, pixel_exact, controls_exact)
        if not reason and validate:
            try:
                reason = validate(current)
            except Exception as exc:
                reason = f"invalid action parameters: {exc}"
        reason = reason or self._guard(action)
        if reason:
            return self._result(action, BLOCKED, reason, before, current)
        try:
            operation(current)
        except ImportError as exc:
            return self._result(action, UNAVAILABLE, "optional desktop backend unavailable", before, self._fresh(), str(exc))
        except Exception as exc:
            # Input might already have happened; never retry, including TypeError.
            return self._result(action, FAILED, "desktop effect failed; input was not retried", before, self._fresh(), str(exc))
        after = self._fresh()
        return self._result(action, SUCCESS if after.status == SUCCESS else FAILED,
                            "action executed and state re-observed" if after.status == SUCCESS else
                            "action ran but post-action observation unavailable", before, after, after.error)

    def _with_observation(self, action: str, operation: Callable[[], Any],
                          validate: Optional[Callable[[DesktopObservation], Optional[str]]] = None) -> DesktopActionResult:
        return self._perform(action, lambda _: operation(), validate, needs_driver=False)

    @staticmethod
    def _find_element(observation: DesktopObservation, target: Any) -> Optional[DesktopElement]:
        if isinstance(target, (DesktopMark, DesktopElement)):
            target_id = target.element_id
        elif isinstance(target, Mapping):
            target_id = target.get("element_id", target.get("id"))
        elif isinstance(target, str):
            # An exact ID always wins, including a numeric-looking string ID.
            exact = next((e for e in observation.elements if e.element_id == target), None)
            if exact is not None:
                return exact
            target_id = None
        else:
            target_id = None
        if target_id is not None:
            return next((e for e in observation.elements if e.element_id == str(target_id)), None)
        if isinstance(target, bool):
            return None
        if isinstance(target, int) or (isinstance(target, str) and target.isdecimal()):
            mark = observation.manifest.get(int(target)) if observation.manifest else None
            matched = next((e for e in observation.elements if mark and e.element_id == mark.element_id), None)
            if matched is not None:
                return matched
        if isinstance(target, str) and target.strip():
            needle = target.casefold().strip()
            match = next((e for e in observation.elements if e.text and e.text.casefold().strip() == needle), None)
            if match is not None:
                return match
            sub = [e for e in observation.elements if e.text and needle in e.text.casefold().strip()]
            if len(sub) == 1:
                return sub[0]
        return None

    @staticmethod
    def _physical(observation: DesktopObservation, x: float, y: float) -> tuple[int, int]:
        px, py = denormalize_coordinates(x, y, observation.width, observation.height)
        origin = getattr(observation, "origin", (0, 0))
        if (not isinstance(origin, (tuple, list)) or len(origin) != 2 or
                any(isinstance(v, bool) or not isinstance(v, int) for v in origin)):
            raise ValueError("invalid physical display origin")
        return px + origin[0], py + origin[1]

    @staticmethod
    def _click_options(button: str, clicks: int) -> Optional[str]:
        if button not in ("left", "right", "middle") or isinstance(clicks, bool) or not isinstance(clicks, int) or not 1 <= clicks <= 3:
            return "click requires left/right/middle button and 1-3 clicks"
        return None

    def _click(self, point: tuple[int, int], button: str, clicks: int) -> None:
        if button == "left" and clicks == 1:
            self.backend.click(*point)  # Preserve the original minimal backend contract.
        else:
            self.backend.click(*point, button=button, clicks=clicks)

    def click_element(self, target: Any, button: str = "left", clicks: int = 1) -> DesktopActionResult:
        resolved: list[DesktopElement] = []

        def validate(observation: DesktopObservation) -> Optional[str]:
            option_error = self._click_options(button, clicks)
            if option_error:
                return option_error
            # Resolve a mark against the decision snapshot exactly once. Re-numbering
            # of marks in a later capture never changes which element was intended.
            expected = self._bound if self._bound is not None else observation
            if not resolved:
                element = self._find_element(expected, target)
                if element is None:
                    return "target element is absent from the decision snapshot"
                resolved.append(element)
            element = self._find_element(observation, {"element_id": resolved[0].element_id})
            if element is None or element != resolved[0]:
                return "target became stale before click"
            metadata = getattr(element, "metadata", {})
            if (not element.visible or not element.clickable or not getattr(element, "enabled", True) or
                    metadata.get("enabled") is False or metadata.get("is_enabled") is False or
                    metadata.get("disabled") is True):
                return "target element is disabled, invisible or not clickable"
            left, top, right, bottom = element.bbox
            if not (0 <= left < right <= observation.width and 0 <= top < bottom <= observation.height):
                return "target element is outside the observed display bounds"
            self._physical(observation, *normalize_coordinates(*element.center, observation.width, observation.height))
            return None

        def operation(observation: DesktopObservation) -> None:
            point = self._physical(observation, *normalize_coordinates(*resolved[0].center, observation.width, observation.height))
            self._click(point, button, clicks)

        return self._perform("click_element", operation, validate)

    def click_normalized(self, x: float, y: float, button: str = "left", clicks: int = 1) -> DesktopActionResult:
        def validate(observation: DesktopObservation) -> Optional[str]:
            self._physical(observation, x, y)
            return self._click_options(button, clicks)
        return self._perform("click_normalized", lambda obs: self._click(self._physical(obs, x, y), button, clicks), validate)

    def move_normalized(self, x: float, y: float) -> DesktopActionResult:
        def validate(observation: DesktopObservation) -> Optional[str]:
            self._physical(observation, x, y)
            return None
        def operation(observation: DesktopObservation) -> None:
            move = getattr(self.backend, "move", None) or getattr(self.backend, "moveTo", None)
            if move is None:
                raise RuntimeError("backend does not support pointer movement")
            move(*self._physical(observation, x, y))
        return self._perform("move_normalized", operation, validate)

    def type_text(self, text: str, paste: bool = False) -> DesktopActionResult:
        def validate(_: DesktopObservation) -> Optional[str]:
            return None if _safe_text(text) is not None else "text must be a NUL-free string of at most 4096 characters"

        def operation(observation: DesktopObservation) -> None:
            if not paste:
                self.backend.type_text(text)
                return
            import pyperclip  # type: ignore
            # Snapshot before modifying the clipboard; a failed read must not clobber it.
            previous = pyperclip.paste()
            if self._pending_clipboard is not None:
                # Retain the original contents across consecutive pending pastes.
                old_previous, old_payload = self._pending_clipboard
                if previous == old_payload:
                    previous = old_previous
            reason = self._guard("clipboard_write")
            if reason:
                raise RuntimeError(reason)
            pyperclip.copy(text)
            self._pending_clipboard = (previous, text)
            sent = False
            try:
                current = self._fresh()
                # Clipboard input is semantic keyboard input too: native UIA
                # trees may churn while the focused editor remains unchanged.
                reason = self._stale(current, observation, pixel_exact=False, controls_exact=False) or self._guard("paste_text")
                if reason:
                    raise RuntimeError(reason)
                self._send_hotkey(["ctrl", "v"])
                sent = True
                # A returned key dispatch is not proof the target consumed the
                # clipboard. Only a driver's explicit acknowledgement permits
                # restoration; otherwise keep the paste payload available.
                consumed = getattr(self.backend, "wait_for_paste", None)
                if consumed is not None and consumed(timeout=0.5) is True:
                    if pyperclip.paste() == text:
                        reason = self._guard("clipboard_restore")
                        if reason:
                            raise RuntimeError(reason)
                        pyperclip.copy(previous)
                        self._pending_clipboard = None
            finally:
                # On uncertain dispatch errors do not restore early or retry.
                # Before dispatch, however, the old clipboard can safely be restored.
                if not sent and 'reason' in locals() and reason and pyperclip.paste() == text:
                    if not self._guard("clipboard_restore"):
                        pyperclip.copy(previous)
                        self._pending_clipboard = None

        return self._perform("type_text", operation, validate)

    def paste_text(self, text: str) -> DesktopActionResult:
        return self.type_text(text, paste=True)

    def restore_clipboard(self, paste_consumed: bool = False) -> DesktopActionResult:
        """Restore a deferred text clipboard only after explicit consumption evidence.

        Generic Ctrl+V has no portable consumption acknowledgement. Drivers may
        implement ``wait_for_paste(timeout=0.5) -> bool`` for automatic restoration;
        otherwise the original text is retained here until the caller confirms
        consumption. Never overwrite clipboard changes made by another application.
        """
        def validate(_: DesktopObservation) -> Optional[str]:
            if paste_consumed is not True:
                return "paste consumption must be acknowledged before clipboard restoration"
            return None
        def operation(_: DesktopObservation) -> None:
            if self._pending_clipboard is None:
                return
            import pyperclip  # type: ignore
            previous, payload = self._pending_clipboard
            if pyperclip.paste() == payload:
                pyperclip.copy(previous)
            self._pending_clipboard = None
        return self._perform("clipboard_restore", operation, validate, needs_driver=False)

    def _send_hotkey(self, keys: Sequence[str]) -> None:
        """Determine calling convention BEFORE dispatch; never retry a TypeError."""
        method = self.backend.hotkey
        try:
            parameters = inspect.signature(method).parameters.values()
            variadic = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in parameters)
        except (TypeError, ValueError):
            variadic = False
        if variadic:
            method(*keys)
        else:
            method(keys)

    def hotkey(self, keys: Sequence[str]) -> DesktopActionResult:
        allowed = re.compile(r"(?:[a-z0-9]|ctrl|alt|shift|win|enter|tab|escape|esc|backspace|delete|space|home|end|up|down|left|right|f(?:[1-9]|1[0-2]))", re.I)
        normalized = [str(k).strip().lower() for k in keys] if isinstance(keys, Sequence) and not isinstance(keys, (str, bytes)) else []
        def validate(_: DesktopObservation) -> Optional[str]:
            if not normalized or len(normalized) > 4 or not all(allowed.fullmatch(k) for k in normalized):
                return "hotkey must contain 1-4 recognized keys"
            return None
        return self._perform("hotkey", lambda _: self._send_hotkey(normalized), validate)

    def scroll(self, amount: int) -> DesktopActionResult:
        def validate(_: DesktopObservation) -> Optional[str]:
            if isinstance(amount, bool) or not isinstance(amount, int) or amount == 0 or abs(amount) > 20:
                return "scroll amount must be an integer from -20 to 20 excluding zero"
            return None
        return self._perform("scroll", lambda _: self.backend.scroll(amount), validate)

    def drag(self, start: tuple[float, float], end: tuple[float, float], duration: float = 0.2) -> DesktopActionResult:
        def validate(observation: DesktopObservation) -> Optional[str]:
            if not self._bounded_seconds(duration, 5):
                return "drag duration must be between 0 and 5 seconds"
            self._physical(observation, *start)
            self._physical(observation, *end)
            return None
        return self._perform("drag", lambda obs: self.backend.drag(self._physical(obs, *start),
                             self._physical(obs, *end), float(duration)), validate)

    @staticmethod
    def _bounded_seconds(seconds: Any, maximum: float) -> bool:
        return (isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and
                math.isfinite(seconds) and 0 <= seconds <= maximum)

    def wait(self, seconds: float = 0.2) -> DesktopActionResult:
        # Waiting is deliberately allowed across a changing desktop, but a bound
        # decision must be current when it starts, like every other action.
        return self._perform("wait", lambda _: time.sleep(float(seconds)),
                             lambda _: None if self._bounded_seconds(seconds, 2) else
                             "wait duration must be between 0 and 2 seconds", needs_driver=False)

    def _window_inventory(self, observation: DesktopObservation) -> list[dict[str, Any]]:
        if self.window_provider is not None:
            raw = self.window_provider()
        elif getattr(observation, "windows", ()):
            raw = observation.windows
        elif self.backend is not None and callable(getattr(self.backend, "list_windows", None)):
            raw = self.backend.list_windows()
        elif sys.platform == "win32" and not self._injected_launcher:
            raw = _native_windows()
        else:
            raw = ()
        if isinstance(raw, Mapping):
            raw = raw.get("windows", ())
        result = []
        for item in list(raw)[:_MAX_ITEMS]:
            if not isinstance(item, Mapping):
                continue
            window = dict(item)
            window["window_id"] = window.get("window_id", window.get("hwnd", window.get("id", 0)))
            window["title"] = str(window.get("title", window.get("name", "")))[:4096]
            result.append(window)
        return result

    def list_windows(self) -> dict[str, Any]:
        observation = self._fresh()
        try:
            windows = self._window_inventory(observation)
        except Exception as exc:
            return {"status": UNAVAILABLE, "windows": [], "error": str(exc)}
        available = bool(windows or self.window_provider or sys.platform == "win32" or
                         getattr(observation, "windows", ()) or
                         callable(getattr(self.backend, "list_windows", None)))
        return {"status": SUCCESS if available else UNAVAILABLE, "windows": windows,
                "observation_id": getattr(observation, "observation_id", "")}

    @staticmethod
    def _window_matches(window: Mapping[str, Any], target: Any) -> bool:
        if isinstance(target, Mapping):
            identifier = target.get("window_id", target.get("hwnd", target.get("id")))
            if identifier:
                return str(window.get("window_id")) == str(identifier)
            if target.get("process_id"):
                return window.get("process_id") == target["process_id"]
            target = target.get("title", target.get("name", ""))
        if isinstance(target, int) and not isinstance(target, bool):
            return window.get("window_id") == target
        if isinstance(target, str) and target.strip():
            needle = target.casefold().strip()
            title = str(window.get("title", "")).casefold().strip()
            process = str(window.get("process", window.get("path", "")))
            p_base = ntpath.basename(process).casefold()
            p_stem = ntpath.splitext(p_base)[0].casefold()
            if needle in {"file explorer", "windows explorer", "explorer"} and p_stem == "explorer":
                if title and title != "program manager":
                    return True
            if needle in (title, p_base, p_stem):
                return True
            if len(needle) >= 3 and (needle in title or needle in p_base or needle in p_stem or p_stem in needle):
                return True
        return False

    @staticmethod
    def _foreground(observation: DesktopObservation, window: Mapping[str, Any]) -> bool:
        if observation.status != SUCCESS:
            return False
        identifier = window.get("window_id")
        if (window.get("process_id") and getattr(observation, "process_id", 0) and
                window["process_id"] != observation.process_id):
            if not (str(window.get("process", "")).casefold() == "applicationframehost.exe" or
                    str(observation.active_process).casefold() == "applicationframehost.exe"):
                return False
        if identifier and getattr(observation, "window_id", 0):
            return str(identifier) == str(observation.window_id)
        return bool(window.get("title") and observation.active_window == window["title"])

    def _request_focus(self, window_id: int) -> None:
        focuser = self.window_focuser or getattr(self.backend, "focus_window", None)
        if focuser is not None:
            focuser(window_id)
        elif sys.platform == "win32" and not self._injected_launcher:
            _native_focus(window_id)
        else:
            raise RuntimeError("window focus backend unavailable")

    def focus_window(self, target: Any, timeout: float = 2.0) -> DesktopActionResult:
        selected: list[dict[str, Any]] = []
        def validate(observation: DesktopObservation) -> Optional[str]:
            if not self._bounded_seconds(timeout, 5):
                return "focus timeout must be between 0 and 5 seconds"
            matches = [w for w in self._window_inventory(observation) if self._window_matches(w, target)]
            if sys.platform == "win32" and not self._injected_launcher:
                try:
                    import ctypes
                    user32 = ctypes.windll.user32
                    live = [w for w in matches if user32.IsWindow(w.get("window_id", 0))]
                    if live:
                        matches = live
                except Exception:
                    pass
            if len(matches) > 1:
                titles = [w for w in matches if w.get("title") and w["title"].strip() and w["title"].strip().casefold() != "popuphost"]
                if titles:
                    matches = titles
            if len(matches) > 1:
                exact_title = [w for w in matches if w.get("title", "").strip().casefold() == str(target).strip().casefold()]
                if exact_title:
                    matches = exact_title
            if len(matches) > 1:
                visible = [w for w in matches if w.get("visible", True) and not w.get("minimized", False) and
                           (w.get("bounds", (0, 0, 0, 0))[2] - w.get("bounds", (0, 0, 0, 0))[0]) > 20 and
                           (w.get("bounds", (0, 0, 0, 0))[3] - w.get("bounds", (0, 0, 0, 0))[1]) > 20]
                if visible:
                    matches = visible
            if len(matches) > 1:
                frames = [w for w in matches if str(w.get("process", "")).casefold() == "applicationframehost.exe"]
                if frames:
                    matches = frames
            if len(matches) > 1:
                matches = [matches[0]]
            if len(matches) != 1:
                return "window target is absent or ambiguous; use an exact window_id"
            window = matches[0]
            if not isinstance(window.get("window_id"), int) or window["window_id"] <= 0:
                return "window target has no valid native identity"
            if selected and (selected[0]["window_id"] != window["window_id"] or
                             selected[0].get("process_id") != window.get("process_id")):
                return "window identity changed before focus"
            selected[:] = [window]
            return None

        result = self._perform("focus_window", lambda _: self._request_focus(selected[0]["window_id"]),
                               validate, needs_driver=False)
        if result.status != SUCCESS:
            return result
        deadline = time.monotonic() + timeout
        after = result.after
        for _ in range(52):
            if after is not None and self._foreground(after, selected[0]):
                result.after, result.detail = after, "target window verified in the foreground"
                return result
            if time.monotonic() >= deadline:
                break
            reason = self._guard("focus_window_verify")
            if reason:
                return self._result("focus_window", BLOCKED, reason, result.before, after)
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            after = self._fresh()
        return self._result("focus_window", FAILED, "foreground focus was not verified; request was not retried",
                            result.before, after)

    def discover_applications(self, query: str = "") -> dict[str, Any]:
        """Return at most 200 structured matches. No launch or model shell evaluation."""
        if _safe_text(query, 256) is None:
            return {"status": BLOCKED, "items": [], "error": "query must be a bounded NUL-free string"}
        items, diagnostics = _installed_applications()
        inventory = self.list_windows()
        if (sys.platform == "win32" and not self._injected_launcher and self.window_provider is None and
                not callable(getattr(self.backend, "list_windows", None))):
            # Perception may intentionally expose only process basenames. Read
            # full executable paths here for discovered running applications.
            try:
                native = {w["window_id"]: w for w in _native_windows()}
                inventory["windows"] = [{**w, **native.get(w.get("window_id"), {})} for w in inventory["windows"]]
            except Exception as exc:
                diagnostics.append(f"running executable paths unavailable: {exc}")
        for window in inventory["windows"]:
            path = window.get("path", window.get("executable", ""))
            items.append({"name": window.get("title", ""), "path": path,
                          "process": window.get("process", ""),
                          "window_id": window.get("window_id"), "kind": "window", "source": "window",
                          "launchable": bool(path and _safe_executable(path))})
        if query and _safe_program(query):
            import shutil
            resolved = shutil.which(query)
            if resolved and _safe_executable(resolved):
                items.append({"name": ntpath.splitext(ntpath.basename(resolved))[0], "path": os.path.abspath(resolved),
                              "kind": "executable", "source": "path", "launchable": True})
        # Empty query also inventories bounded PATH directories, rather than a fixed app map.
        if not query:
            seen_paths: set[str] = set()
            for directory in os.get_exec_path()[:64]:
                try:
                    with os.scandir(directory) as entries:
                        for index, entry in enumerate(entries):
                            if index >= 2048 or len(seen_paths) >= 2048:
                                break
                            if entry.is_file() and _safe_executable(entry.path):
                                path = os.path.abspath(entry.path)
                                if path not in seen_paths:
                                    seen_paths.add(path)
                                    items.append({"name": ntpath.splitext(entry.name)[0], "path": path,
                                                  "kind": "executable", "source": "path", "launchable": True})
                except OSError:
                    continue
        needle, unique = query.casefold().strip(), {}
        for item in items:
            if needle and not any(needle in str(item.get(k, "")).casefold() for k in ("name", "path", "aumid", "process")):
                continue
            key = (item.get("kind"), str(item.get("path", item.get("aumid", item.get("window_id", "")))).casefold())
            unique.setdefault(key, item)
        def rank(item: Mapping[str, Any]) -> tuple[Any, ...]:
            path = str(item.get("path", ""))
            exact = needle and needle in (str(item.get("name", "")).casefold(), path.casefold(),
                                          str(item.get("aumid", "")).casefold(), ntpath.basename(path).casefold(),
                                          ntpath.splitext(ntpath.basename(path))[0].casefold())
            return (not exact, not item.get("launchable"), item.get("name", "").casefold())
        ordered = sorted(unique.values(), key=rank)
        return {"status": SUCCESS, "items": ordered[:_MAX_ITEMS], "truncated": len(ordered) > _MAX_ITEMS,
                "diagnostics": diagnostics}

    @staticmethod
    def _launch_process(program: str, args: Sequence[str]) -> Any:
        return subprocess.Popen([program, *args], shell=False, close_fds=True)

    @staticmethod
    def _shell_execute(path: str) -> None:
        import ctypes
        from ctypes import wintypes as wt
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.ShellExecuteW.argtypes = [wt.HWND, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, ctypes.c_int]
        shell.ShellExecuteW.restype = ctypes.c_void_p
        value = shell.ShellExecuteW(None, "open", path, None, None, 1)
        if int(value or 0) <= 32:
            raise OSError("OS ShellExecute rejected the discovered shortcut")

    def _resolve_application(self, program: str) -> Optional[dict[str, Any]]:
        if self._injected_launcher:
            # The injection exemption is for executable availability only.
            # Shortcut/AUMID trust still comes from OS inventory, never model text.
            if program.lower().endswith(".lnk") or "!" in program:
                return None
            return {"name": ntpath.splitext(ntpath.basename(program))[0], "path": program,
                    "kind": "executable", "launchable": True}
        # Existing absolute executable paths are allowed, never shell association
        # for documents/scripts. A .lnk must come from Start Menu discovery.
        if os.path.isabs(program) and _safe_executable(program):
            return {"name": ntpath.splitext(ntpath.basename(program))[0], "path": os.path.realpath(program),
                    "kind": "executable", "launchable": True}
        # Display-name packaged apps often have no matching executable alias.
        # Normalize a plain .exe suffix for inventory lookup, never for an
        # arbitrary path, shell string or application ID.
        display_alias = (program[:-4] if program.casefold().endswith(".exe") and
                         not any(separator in program for separator in ("/", "\\", ":", "!")) else program)
        discovered = self.discover_applications(display_alias)
        matches = []
        for item in discovered.get("items", ()):
            identifiers = (item.get("name", ""), item.get("path", ""), item.get("aumid", ""),
                           ntpath.basename(item.get("path", "")),
                           ntpath.splitext(ntpath.basename(item.get("path", "")))[0])
            aliases = {program.casefold()}
            if item.get("kind") == "packaged":
                aliases.add(display_alias.casefold())
            if (item.get("launchable") and item.get("kind") in {"executable", "packaged", "shortcut", "windows_shell"}
                    and aliases.intersection(str(x).casefold() for x in identifiers)):
                matches.append(item)
        # Prefer the same executable over a shortcut alias, but reject genuinely
        # ambiguous names rather than guessing which installation the user meant.
        identities = {str(i.get("target", i.get("path", i.get("aumid", "")))).casefold() for i in matches}
        if len(identities) != 1:
            # Windows may expose the same Microsoft app through App Paths,
            # PATH and a packaged AUMID. Treat those as one discovered family,
            # preferring the packaged identity because it avoids guessing an
            # executable host. Different families remain ambiguous and fail
            # closed.
            target_family = display_alias.casefold()
            families = {ntpath.splitext(ntpath.basename(str(item.get("name", ""))))[0].casefold()
                        for item in matches}
            if families == {target_family}:
                packaged = [item for item in matches if item.get("kind") == "packaged"]
                if packaged:
                    return packaged[0]
                executables = [item for item in matches if item.get("kind") == "executable"]
                if len(executables) == 1:
                    return executables[0]
            return None
        return sorted(matches, key=lambda i: i.get("kind") == "shortcut")[0] if matches else None

    @staticmethod
    def _application_foreground(observation: DesktopObservation, app: Mapping[str, Any], pid: Any = None) -> bool:
        if observation.status != SUCCESS:
            return False
        if isinstance(pid, int) and pid > 0 and getattr(observation, "process_id", 0) == pid:
            return True
        executable = app.get("target", app.get("path", ""))
        actual = observation.active_process
        if executable and actual and actual != "none":
            if ntpath.basename(actual).casefold() == ntpath.basename(executable).casefold():
                return True
        name = str(app.get("name", "")).casefold().strip()
        title = observation.active_window.casefold().strip()
        if not name or name in ("desktop", "none"):
            return False
        if title == name or title.endswith(" - " + name) or title.startswith(name + " - "):
            return True
        if str(actual).casefold() == "applicationframehost.exe" and (name in title or title in name):
            return True
        if len(name) >= 3 and (name in title or (actual and name in actual.casefold())):
            return True
        return False

    def launch(self, program: str, args: Sequence[str] = (), timeout: float = 3.0) -> DesktopActionResult:
        selected: list[dict[str, Any]] = []
        clean_args: list[str] = []
        launched: list[Any] = []

        def validate(_: DesktopObservation) -> Optional[str]:
            packaged = isinstance(program, str) and re.fullmatch(r"[A-Za-z0-9_.-]+![A-Za-z0-9_.-]+", program)
            windows_shell_alias = isinstance(program, str) and program.casefold().strip() in {
                "explorer", "explorer.exe", "file explorer"
            }
            if not _safe_program(program) and not packaged and not windows_shell_alias:
                return "program must name a safe discovered application, not an execution host or shell text"
            if not self._bounded_seconds(timeout, 5):
                return "launch timeout must be between 0 and 5 seconds"
            documents = _document_args(args)
            if documents is None:
                return "launch arguments must be existing document paths; flags, scripts and URLs are blocked"
            clean_args[:] = documents
            if not selected:
                app = self._resolve_application(program.strip())
                if app is None:
                    return "application not discovered or name is ambiguous"
                selected.append(app)
            app = selected[0]
            if app["kind"] == "packaged" and not _safe_packaged(app.get("name", ""), app.get("aumid", "")):
                return "packaged execution hosts and unsafe application identities are blocked"
            if app["kind"] in ("shortcut", "packaged") and clean_args:
                return "shortcuts and packaged applications accept no model launch arguments"
            if app["kind"] == "shortcut":
                details = _shortcut_details(app["path"])
                if (not details or details.get("arguments") or details.get("target") != app.get("target") or
                        not _safe_executable(details.get("target"))):
                    return "shortcut target is unsafe, unresolved or changed"
            elif app["kind"] not in {"packaged", "windows_shell"}:
                path = app.get("path", "")
                if not _safe_program(path) or (not self._injected_launcher and not _safe_executable(path)):
                    return "discovered executable is unsafe or no longer exists"
            elif app["kind"] == "windows_shell":
                if os.path.normcase(app.get("path", "")) != os.path.normcase(
                        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")):
                    return "unrecognized Windows shell identity"
            return None

        def operation(_: DesktopObservation) -> None:
            app = selected[0]
            if app["kind"] == "shortcut":
                self._shell_execute(app["path"])
            elif app["kind"] == "packaged":
                explorer = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")
                launched.append(self.launcher(explorer, ["shell:AppsFolder\\" + app["aumid"]]))
            elif app["kind"] == "windows_shell":
                launched.append(self.launcher(app["path"], []))
            else:
                launched.append(self.launcher(app["path"], clean_args))

        result = self._perform("launch", operation, validate, needs_driver=False)
        if result.status != SUCCESS:
            return result
        pid = getattr(launched[0], "pid", None) if launched and selected[0]["kind"] != "packaged" else None
        after, deadline, focused = result.after, time.monotonic() + timeout, False
        for _ in range(52):
            if after is not None and self._application_foreground(after, selected[0], pid):
                result.after, result.detail = after, "application foreground verified after launch"
                return result
            if time.monotonic() >= deadline:
                break
            reason = self._guard("launch_verify")
            if reason:
                return self._result("launch", BLOCKED, reason, result.before, after)
            # A launcher return value/PID alone is never success. If startup is
            # delayed, identify the application's actual window and focus once.
            if not focused and after is not None:
                try:
                    windows = self._window_inventory(after)
                    matches = [w for w in windows if
                               (pid and w.get("process_id") == pid) or
                               (selected[0].get("path") and w.get("path") and
                                os.path.normcase(w["path"]) == os.path.normcase(selected[0]["path"])) or
                               self._window_matches(w, selected[0]["name"])]
                    if len(matches) > 1:
                        visible = [w for w in matches if w.get("visible", True) and not w.get("minimized", False)]
                        if len(visible) == 1:
                            matches = visible
                        else:
                            frames = [w for w in matches if str(w.get("process", "")).casefold() == "applicationframehost.exe"]
                            if len(frames) == 1:
                                matches = frames
                    if len(matches) == 1 and matches[0].get("window_id"):
                        current = self._fresh()
                        reason = self._stale(current, after) or self._guard("launch_focus")
                        if reason:
                            return self._result("launch", BLOCKED, reason, result.before, current)
                        focused = True  # Set before dispatch so an uncertain error is never retried.
                        self._request_focus(matches[0]["window_id"])
                except Exception as exc:
                    return self._result("launch", FAILED, "startup focus failed; request was not retried",
                                        result.before, self._fresh(), str(exc))
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            after = self._fresh()
        return self._result("launch", FAILED, "launch dispatched but application foreground was not verified",
                             result.before, after)

    def open_folder(self, path: str) -> DesktopActionResult:
        """Open a literal existing directory, never an Explorer command line."""
        def validate(_: DesktopObservation) -> Optional[str]:
            if sys.platform != "win32":
                return "Windows File Explorer unavailable on this platform"
            if (not isinstance(path, str) or len(path) > 1024 or any(c in path for c in '\x00\r\n"') or
                    path.startswith(("\\\\", "//")) or not os.path.isdir(path)):
                return "folder must be a literal existing local directory"
            return None
        def operation(_: DesktopObservation) -> None:
            if sys.platform == "win32" and not self._injected_launcher:
                self._shell_execute(os.path.abspath(path))
            else:
                executable = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")
                self.launcher(executable, [os.path.abspath(path)])
        # This is an action receipt, not a folder-navigation verification.
        return self._perform("open_folder", operation, validate, needs_driver=False)


desktop_actions = DesktopActionLayer()


__all__ = ["DesktopActionLayer", "DesktopActionResult", "desktop_actions"]
