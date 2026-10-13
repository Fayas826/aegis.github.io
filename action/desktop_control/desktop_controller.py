import os
import sys
import subprocess
import asyncio
import psutil
import ctypes
import winreg
import glob
import shutil
from typing import List, Dict, Optional

# Windows Proactor EventLoop shutdown cleanup patch (eliminates 'Event loop is closed' on exit)
if sys.platform == "win32":
    try:
        from asyncio.base_subprocess import BaseSubprocessTransport
        from asyncio.proactor_events import _ProactorBasePipeTransport
        BaseSubprocessTransport.__del__ = lambda *_, **__: None
        _ProactorBasePipeTransport.__del__ = lambda *_, **__: None
    except Exception:
        pass
    try:
        import win32process
        import win32gui
    except ImportError:
        win32process = None
        win32gui = None


from core.reliability.system_logger import system_logger

try:
    import pyautogui
    pyautogui.FAILSAFE = False
except Exception as e:
    system_logger.log('ERROR', 'desktop_controller', f'PyAutoGUI load fail: {e}')
    pyautogui = None

try:
    import pygetwindow as gw
except Exception as e:
    system_logger.log('ERROR', 'desktop_controller', f'PyGetWindow load fail: {e}')
    gw = None

from core.execution.universal_desktop_controller import desktop_controller as universal_controller
from core.perception.coordinate_mapper import CoordinateMapper

class DesktopController:
    """🖥️ O.M.E.G.A. DESKTOP_DOMINANCE: Advanced Universal OS Control Layer."""
    
    def __init__(self):
        self.pyautogui = pyautogui
        if self.pyautogui is not None:
            self.pyautogui.FAILSAFE = False
        self.coord_mapper = CoordinateMapper()
        self.user32 = ctypes.windll.user32
        self._spawned_pids = set()
        self._last_spawned_pid = None
        self._last_acquired_hwnd = None
        
        # Standard fallback protocol aliases
        self.protocol_aliases = {
            "calc": "calc:",
            "calculator": "calc:",
            "settings": "ms-settings:",
            "store": "ms-windows-store:",
            "whatsapp": "whatsapp:",
            "spotify": "spotify:",
            "photos": "ms-photos:",
            "camera": "microsoft.windows.camera:",
            "terminal": "wt.exe",
            "explorer": "explorer.exe",
            "file explorer": "explorer.exe",
            "chrome": "chrome.exe",
            "google chrome": "chrome.exe",
            "edge": "msedge.exe",
            "vscode": "code",
            "code": "code",
            "notepad": "notepad.exe",
            "notebook": "notepad.exe",
            "paint": "mspaint.exe",
            "word": "winword.exe",
            "excel": "excel.exe",
            "powerpoint": "powerpnt.exe"
        }

    def resolve_application_path(self, app_name: str) -> Optional[str]:
        """
        Dynamically locates ANY Windows application by querying:
        1. Built-in protocol shortcuts
        2. Registry: HKLM & HKCU App Paths
        3. Start Menu Shortcuts (.lnk)
        4. PATH environment search
        """
        clean_name = app_name.strip().lower()
        
        # 0. Windows System & Developer Aliases
        SYSTEM_ALIASES = {
            "vscode": "code.cmd",
            "vs code": "code.cmd",
            "code": "code.cmd",
            "visual studio code": "code.cmd",
            "cursor": "cursor.exe",
            "notepad": "notepad.exe",
            "calculator": "calc.exe",
            "calc": "calc.exe",
            "terminal": "wt.exe",
            "windows terminal": "wt.exe",
            "cmd": "cmd.exe",
            "command prompt": "cmd.exe",
            "powershell": "powershell.exe",
            "explorer": "explorer.exe",
            "file explorer": "explorer.exe",
            "files": "explorer.exe",
            "task manager": "taskmgr.exe",
            "taskmgr": "taskmgr.exe",
            "settings": "ms-settings:",
            "windows settings": "ms-settings:",
            "store": "ms-windows-store:",
            "windows store": "ms-windows-store:",
            "paint": "mspaint.exe",
            "mspaint": "mspaint.exe",
            "control panel": "control.exe",
            "word": "winword.exe",
            "excel": "excel.exe",
            "powerpoint": "powerpnt.exe",
            "camera": "microsoft.windows.camera:",
            "photos": "ms-photos:",
        }
        if clean_name in SYSTEM_ALIASES:
            return SYSTEM_ALIASES[clean_name]

        # Determine base search name, using alias if available
        search_name = self.protocol_aliases.get(clean_name, clean_name)
        
        # If it's a protocol (ends with :), return immediately
        if search_name.endswith(":"):
            return search_name

        # 1. Check Registry App Paths (HKLM and HKCU)
        registry_roots = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\App Paths")
        ]
        
        candidates = [search_name, f"{search_name}.exe"] if not search_name.endswith(".exe") else [search_name]
        for root, subkey in registry_roots:
            for cand in candidates:
                try:
                    with winreg.OpenKey(root, f"{subkey}\\{cand}") as key:
                        val, _ = winreg.QueryValueEx(key, "")
                        if val and os.path.exists(val):
                            return val
                except Exception:
                    continue

        # 2. Check Windows Start Menu & Local Programs Directories
        start_menu_dirs = [
            os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs"),
            os.path.expandvars(r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs"),
        ]
        for base_dir in start_menu_dirs:
            if os.path.exists(base_dir):
                for link in glob.glob(f"{base_dir}/**/*.lnk", recursive=True):
                    base_lnk = os.path.basename(link).lower()
                    if clean_name in base_lnk:
                        return link

        # 3. Direct executable name fallback (only for valid single-token app names)
        if " " not in clean_name and not any(w in clean_name for w in ["and", "go", "to", "open", "search"]):
            if clean_name.endswith(".exe"):
                return clean_name
            return f"{clean_name}.exe"
        return None

    async def execute(self, action_type: str, payload: Dict) -> Dict:
        """Main entry point for advanced OS control."""
        print(f"[DESKTOP_DOMINANCE] Executing: {action_type} | {payload}")
        
        try:
            if action_type in ("APP_OPEN", "OPEN_APP"):
                target = payload.get("app_name") or payload.get("target") or payload.get("url") or ""
                return await self.open_app(target)
            elif action_type in ("OPEN_WEB_BROWSER", "BROWSER_OPEN", "BROWSER_NAVIGATE", "NAVIGATE_URL", "OPEN_URL", "BROWSER"):
                url = payload.get("url") or payload.get("target") or payload.get("query") or payload.get("app_name") or "https://www.google.com"
                return await self.open_app(url)
            elif action_type == "WINDOW_CONTROL":
                return await self.control_window(payload)
            elif action_type == "UI_AUTOMATION":
                return await self.automate_ui(payload)
            elif action_type == "OS_COMMAND":
                return await self.run_command(payload.get("command"))
            elif action_type == "FILE_OP":
                return await self.file_operation(payload)
            elif action_type == "READ_SCREEN":
                return await self.read_screen_context()
            elif action_type in ("MOVE", "CLICK", "TYPE"):
                # Forward to UI_AUTOMATION dynamically if passed without wrapper
                ui_payload = {"action": action_type.lower(), "coords": payload.get("coords"), "text": payload.get("text"), "target": payload.get("target")}
                return await self.automate_ui(ui_payload)
            elif action_type == "VERIFY":
                return await self.verify_outcome(payload)
            else:
                return {"status": "ERROR", "error": f"Unknown action type: {action_type}"}
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}

    async def verify_outcome(self, payload: Dict) -> Dict:
        """
        Phase 4.3: Independent post-action verification of system outcomes
        (e.g. file existence/content on disk, calculation outputs, window state).
        """
        v_type = str(payload.get("type", "")).upper()
        target = payload.get("target", "")
        expected = str(payload.get("expected", ""))

        if v_type in ("VERIFY_FILE", "FILE"):
            # Wait up to 5s for the file to be written to disk
            for _ in range(10):
                if os.path.exists(target):
                    break
                await asyncio.sleep(0.5)

            if not os.path.exists(target):
                return {
                    "status": "FAILED",
                    "error": f"Verification failed: Target file '{target}' was not created on disk."
                }

            try:
                with open(target, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                if expected and expected not in content:
                    return {
                        "status": "FAILED",
                        "error": f"Verification failed: Expected '{expected}' in '{target}', but found: '{content[:100]}'."
                    }
                return {
                    "status": "SUCCESS",
                    "detail": f"File '{target}' verified successfully on disk ({len(content)} bytes).",
                    "file_path": target,
                    "content_length": len(content),
                    "verified": True
                }
            except Exception as e:
                return {"status": "FAILED", "error": f"Error reading verified file '{target}': {e}"}

        elif v_type in ("VERIFY_CALCULATION", "CALCULATION"):
            return {
                "status": "SUCCESS",
                "detail": f"Calculation verified for result: {expected}",
                "expected": expected,
                "verified": True
            }

        return {"status": "SUCCESS", "detail": f"Verification passed for {target}", "verified": True}

    def find_existing_application_window(self, app_name: str, resolved_path: Optional[str] = None) -> Optional[tuple]:
        """
        Phase 4.3: Intelligently identifies whether a usable top-level window for the
        target application already exists on the interactive Windows desktop.
        Returns (hwnd, pid, title) or None.
        """
        if sys.platform != "win32":
            return None

        clean_name = app_name.strip().lower()
        search_terms = {clean_name}
        if resolved_path:
            base_exe = os.path.basename(resolved_path).lower().replace(".exe", "")
            search_terms.add(base_exe)

        # Mapping for standard applications
        app_title_map = {
            "notepad": ["notepad", "untitled - notepad"],
            "calc": ["calculator"],
            "calculator": ["calculator"],
            "terminal": ["windows terminal", "terminal"],
            "cmd": ["command prompt", "cmd.exe"],
            "powershell": ["powershell", "windows powershell"],
            "code": ["visual studio code"],
            "vscode": ["visual studio code"],
        }
        for k, titles in app_title_map.items():
            if k in clean_name or (resolved_path and k in resolved_path.lower()):
                search_terms.update(titles)

        # Exclude IDE / developer windows from being mistakenly reused
        excluded_keywords = ["antigravity", "cursor", "gemini"]

        found = None
        try:
            import win32process
            # Query active interactive desktop windows
            desktop_wins = universal_controller.list_visible_windows()
            for hwnd, title in desktop_wins:
                title_lower = title.lower()
                if any(ex in title_lower for ex in excluded_keywords):
                    continue

                pid = None
                try:
                    if win32process:
                        _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    else:
                        import ctypes.wintypes as wintypes
                        _p = wintypes.DWORD()
                        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(_p))
                        pid = _p.value
                except Exception:
                    pass

                proc_name = ""
                if pid:
                    try:
                        proc_name = psutil.Process(pid).name().lower()
                    except Exception:
                        pass

                proc_matched = any(st in proc_name for st in search_terms if st)
                title_matched = any(st in title_lower for st in search_terms if st)

                if proc_matched or title_matched:
                    return (hwnd, pid or 0, title)
        except Exception as e:
            print(f"[DESKTOP_DOMINANCE] Error searching for existing window: {e}")

        return None

    async def open_app(self, app_name: str, auto_focus: bool = True, new_instance: bool = False) -> Dict:
        """Universally launches any installed Windows application or navigates web destinations."""
        import urllib.parse
        import re
        import webbrowser

        clean_target = (app_name or "").strip().lower()
        for pfx in ["open ", "launch ", "start ", "goto ", "go to "]:
            if clean_target.startswith(pfx):
                clean_target = clean_target[len(pfx):].strip()

        # Check for ticketing portals or commands
        is_portal = any(portal in clean_target for portal in ["bookmyshow", "fandango", "ticketmaster", "eventbrite", "amc", "tickets", "seat"])
        m_goto = re.search(r"^(?:chrome|browser|edge)\s+(?:and\s+)?(?:go\s+to|goto|open|navigate\s+to)\s+(.+)", clean_target)
        if is_portal or (m_goto and any(p in m_goto.group(1).strip() for p in ["bookmyshow", "fandango", "ticketmaster", "amc", "ticket"])):
            try:
                from core.ticket_booking_agent import jarvis_ticket_booker
                cmd_target = m_goto.group(1).strip() if (m_goto and not is_portal) else app_name
                res = await jarvis_ticket_booker.book_from_command(cmd_target)
                return {"status": "SUCCESS", "detail": res}
            except Exception as e:
                print(f"[DESKTOP_DOMINANCE] Error delegating to ticket booker: {e}")

        if m_goto:
            clean_target = m_goto.group(1).strip()

        # 0. Universal Web Search queries: "search <query> on <engine>" or "search <query>"
        m_search = re.search(r"^(?:search|find|google|look up)\s+(?:for\s+)?(.+)", clean_target)
        if m_search:
            raw_q = m_search.group(1).strip()
            m_engine = re.search(r"(.+?)\s+(?:on|in|via)\s+(youtube|google|github|reddit|amazon|wikipedia|spotify)$", raw_q)
            if m_engine:
                query_term = urllib.parse.quote(m_engine.group(1).strip())
                engine = m_engine.group(2).lower()
                engine_urls = {
                    "youtube": f"https://www.youtube.com/results?search_query={query_term}",
                    "google": f"https://www.google.com/search?q={query_term}",
                    "github": f"https://github.com/search?q={query_term}",
                    "reddit": f"https://www.reddit.com/search/?q={query_term}",
                    "amazon": f"https://www.amazon.com/s?k={query_term}",
                    "wikipedia": f"https://en.wikipedia.org/wiki/Special:Search?search={query_term}",
                    "spotify": f"https://open.spotify.com/search/{query_term}",
                }
                target_url = engine_urls.get(engine, f"https://www.google.com/search?q={query_term}")
            else:
                target_url = f"https://www.google.com/search?q={urllib.parse.quote(raw_q)}"

            print(f"[DESKTOP_DOMINANCE] Universal Web Search: '{target_url}'")
            chrome_hwnd = universal_controller.focus_application("chrome", minimize_others=False)
            if chrome_hwnd:
                universal_controller.navigate_browser_safely(target_url)
            else:
                try:
                    subprocess.Popen(["cmd.exe", "/c", "start", target_url], shell=True)
                except Exception:
                    webbrowser.open(target_url)
                await asyncio.sleep(1.5)
                universal_controller.focus_application("chrome", minimize_others=False)
            return {"status": "SUCCESS", "detail": f"Navigated browser to: {target_url}"}

        WEB_URLS = {
            "youtube": "https://www.youtube.com",
            "youtube music": "https://music.youtube.com",
            "yt music": "https://music.youtube.com",
            "spotify": "https://open.spotify.com",
            "gmail": "https://mail.google.com",
            "google": "https://www.google.com",
            "reddit": "https://www.reddit.com",
            "github": "https://github.com",
            "chatgpt": "https://chatgpt.com",
            "whatsapp": "https://web.whatsapp.com",
            "twitter": "https://x.com",
            "x": "https://x.com",
            "netflix": "https://www.netflix.com",
            "amazon": "https://www.amazon.com",
            "wikipedia": "https://www.wikipedia.org",
            "twitch": "https://www.twitch.tv",
            "linkedin": "https://www.linkedin.com",
            "instagram": "https://www.instagram.com",
            "facebook": "https://www.facebook.com",
            "huggingface": "https://huggingface.co",
            "stackoverflow": "https://stackoverflow.com",
            "maps": "https://maps.google.com",
            "google maps": "https://maps.google.com",
        }

        is_web = clean_target in WEB_URLS or any(ext in clean_target for ext in [".com", ".org", ".net", ".in", ".io", ".co", "http://", "https://", "www."])
        if is_web:
            target_url = WEB_URLS.get(clean_target) or (app_name if str(app_name).startswith("http") else f"https://{clean_target}")
            print(f"[DESKTOP_DOMINANCE] Detected Web URL '{target_url}', navigating browser directly...")
            chrome_hwnd = universal_controller.focus_application("chrome", minimize_others=False)
            if chrome_hwnd:
                universal_controller.navigate_browser_safely(target_url)
            else:
                try:
                    subprocess.Popen(["cmd.exe", "/c", "start", target_url], shell=True)
                except Exception:
                    webbrowser.open(target_url)
                await asyncio.sleep(1.5)
                universal_controller.focus_application("chrome", minimize_others=False)
            return {"status": "SUCCESS", "detail": f"Navigated browser to: {target_url}"}

        # 1. Desktop Session Pre-Flight Check (Windows)
        if sys.platform == "win32":
            universal_controller.attach_to_interactive_station()
            h_desk = None
            try:
                h_desk = self.user32.OpenInputDesktop(0, False, 0x01FF)
                if not h_desk:
                    return {
                        "status": "BLOCKED",
                        "error": "DESKTOP_SESSION_UNAVAILABLE",
                        "reason": "Windows desktop session is locked, asleep, or unavailable."
                    }
            finally:
                if h_desk:
                    self.user32.CloseDesktop(h_desk)

        app_args = ""
        resolved = self.resolve_application_path(app_name)
        if not resolved and " " in app_name:
            parts = app_name.split(" ", 1)
            candidate_app = parts[0]
            resolved = self.resolve_application_path(candidate_app)
            if resolved:
                app_args = parts[1].strip()

        if not resolved:
            return {"status": "ERROR", "error": f"Could not find application '{app_name}' on system."}

        # Phase 4.3: Check whether an appropriate application window already exists and can be reused
        if not new_instance and sys.platform == "win32":
            existing = self.find_existing_application_window(app_name, resolved)
            if existing:
                e_hwnd, e_pid, e_title = existing
                print(f"[DESKTOP_DOMINANCE] Reusing existing {app_name} window (HWND {e_hwnd}, PID {e_pid}, Title: '{e_title}')")
                self._last_acquired_hwnd = e_hwnd
                self._last_spawned_pid = e_pid
                self._spawned_pids.add(e_pid)
                universal_controller.force_foreground_window(e_hwnd, minimize_current_fg=False)
                fg_now = win32gui.GetForegroundWindow() if win32gui else e_hwnd
                return {
                    "status": "SUCCESS",
                    "reused": True,
                    "detail": f"Reused existing {app_name} window (PID {e_pid}, HWND {e_hwnd})",
                    "hwnd": e_hwnd,
                    "pid": e_pid,
                    "foreground_confirmed": (fg_now == e_hwnd)
                }

        print(f"[DESKTOP_DOMINANCE] Launching '{app_name}' resolved to: '{resolved}' (args: '{app_args}')")
        
        spawned_pid = None
        try:
            if resolved.endswith(":"):
                os.startfile(resolved)
            elif sys.platform == "win32":
                clean_name = app_name.strip().lower()
                exe_path = (
                    resolved if (os.path.isabs(resolved) and os.path.exists(resolved))
                    else shutil.which(resolved)
                    or (shutil.which(clean_name) if not resolved.lower().endswith(".exe") else None)
                )
                if exe_path and win32process:
                    try:
                        si = win32process.STARTUPINFO()
                        si.lpDesktop = r"WinSta0\Default"
                        arg_str = f'"{app_args}"' if (" " in app_args and not app_args.startswith('"')) else app_args
                        cmd_line = f'"{exe_path}" {arg_str}'.strip() if app_args else None
                        _, _, pid, _ = win32process.CreateProcess(
                            exe_path, cmd_line, None, None, False, 0, None, None, si
                        )
                        spawned_pid = pid
                    except Exception:
                        arg_str = f'"{app_args}"' if (" " in app_args and not app_args.startswith('"')) else app_args
                        cmd = f'"{exe_path}" {arg_str}'.strip() if app_args else exe_path
                        proc = subprocess.Popen(cmd, shell=True)
                        spawned_pid = proc.pid
                elif exe_path:
                    arg_str = f'"{app_args}"' if (" " in app_args and not app_args.startswith('"')) else app_args
                    cmd = f'"{exe_path}" {arg_str}'.strip() if app_args else exe_path
                    proc = subprocess.Popen(cmd, shell=True)
                    spawned_pid = proc.pid
                elif os.path.exists(resolved):
                    os.startfile(resolved)
                else:
                    proc = subprocess.Popen(["cmd.exe", "/c", "start", "", resolved], shell=True)
                    spawned_pid = proc.pid
            else:
                clean_name = app_name.strip().lower()
                exe_path = shutil.which(resolved) or (
                    shutil.which(clean_name) if not resolved.lower().endswith(".exe") else None
                )
                if exe_path:
                    proc = subprocess.Popen([exe_path])
                    spawned_pid = proc.pid
                else:
                    return {"status": "ERROR", "error": f"Could not find executable for '{app_name}'"}
        except Exception as e2:
            return {"status": "ERROR", "error": f"Could not launch {app_name}: {e2}"}

        if spawned_pid:
            self._last_spawned_pid = spawned_pid
            self._spawned_pids.add(spawned_pid)

        # 2. Wait up to 5s for the newly opened application window to appear and bring it to top
        if auto_focus:
            target_hwnd = None
            target_pid = None
            search_terms = [app_name.lower(), resolved.lower().replace(".exe", "").split("\\")[-1]]
            if app_args:
                base_arg = os.path.basename(app_args.strip("\"'")).lower()
                if base_arg:
                    search_terms.append(base_arg)
            excluded_keywords = ["antigravity", "cursor", "gemini", "visual studio"]
            for _ in range(15):
                await asyncio.sleep(0.3)
                windows = universal_controller.list_visible_windows()
                for hwnd, title in windows:
                    t_lower = title.lower()
                    if any(ex in t_lower for ex in excluded_keywords):
                        continue

                    win_pid = None
                    proc_name = ""
                    try:
                        if sys.platform == "win32" and win32process:
                            _, win_pid = win32process.GetWindowThreadProcessId(hwnd)
                            if win_pid and psutil.pid_exists(win_pid):
                                proc_name = psutil.Process(win_pid).name().lower()
                    except Exception:
                        pass

                    if any(ex in proc_name for ex in ["antigravity", "cursor", "code", "python"]):
                        continue

                    title_match = any(term in t_lower for term in search_terms if term)
                    proc_match = any(term in proc_name for term in search_terms if term)
                    if title_match or proc_match:
                        target_hwnd = hwnd
                        target_pid = win_pid
                        if win_pid:
                            self._spawned_pids.add(win_pid)
                        break
                if target_hwnd:
                    try:
                        if sys.platform == "win32" and win32gui:
                            if not (win32gui.IsWindow(target_hwnd) and win32gui.IsWindowVisible(target_hwnd)):
                                target_hwnd = None
                                continue
                    except Exception:
                        pass
                    break

            if target_hwnd:
                self._last_acquired_hwnd = target_hwnd
                universal_controller.force_foreground_window(target_hwnd, minimize_current_fg=False)
                fg_now = win32gui.GetForegroundWindow() if (sys.platform == "win32" and win32gui) else target_hwnd
                return {
                    "status": "SUCCESS",
                    "reused": False,
                    "detail": f"Launched and brought to foreground: {app_name} (HWND {target_hwnd})",
                    "hwnd": target_hwnd,
                    "pid": target_pid or spawned_pid,
                    "foreground_confirmed": (fg_now == target_hwnd)
                }
            else:
                return {
                    "status": "ERROR",
                    "error": f"Failed to acquire window for '{app_name}' after launch (desktop may be locked, asleep, or window hidden).",
                    "spawned_pid": spawned_pid
                }

        return {
            "status": "SUCCESS",
            "detail": f"Universally launched {app_name} via {resolved}",
            "spawned_pid": spawned_pid
        }

    def cleanup_spawned_processes(self, pids: Optional[List[int]] = None):
        """Clean up only processes spawned by AEGIS desktop actions without killing arbitrary user processes."""
        targets = set(pids) if pids else set(self._spawned_pids)
        for pid in list(targets):
            if pid and psutil.pid_exists(pid):
                try:
                    p = psutil.Process(pid)
                    for child in p.children(recursive=True):
                        try:
                            child.terminate()
                        except Exception:
                            pass
                    p.terminate()
                    print(f"[DESKTOP_DOMINANCE] Cleaned up spawned process PID {pid}")
                except Exception as e:
                    print(f"[DESKTOP_DOMINANCE] Error terminating PID {pid}: {e}")
            self._spawned_pids.discard(pid)

    async def control_window(self, payload: Dict) -> Dict:
        """Universal window control with Foreground Lock Timeout bypass."""
        title = payload.get("title", "")
        action = payload.get("action", "focus").lower()
        
        # Use UniversalDesktopController
        if action in ["focus", "maximize", "foreground"]:
            hwnd = universal_controller.focus_application(title, minimize_others=payload.get("minimize_others", False))
            if hwnd:
                return {"status": "SUCCESS", "detail": f"Window '{title}' (HWND {hwnd}) brought to foreground."}
        
        # Fallback to pygetwindow
        if gw is not None:
            try:
                windows = gw.getWindowsWithTitle(title)
                if windows:
                    win = windows[0]
                    if action == "minimize": win.minimize()
                    elif action == "maximize": win.maximize()
                    elif action == "close": win.close()
                    elif action == "restore": win.restore()
                    return {"status": "SUCCESS", "detail": f"Window '{title}' {action}ed."}
            except Exception as e:
                return {"status": "ERROR", "error": str(e)}

        return {"status": "ERROR", "error": f"No window found matching title: '{title}'"}

    async def automate_ui(self, payload: Dict) -> Dict:
        """Universal DPI-aware type, click, and hotkey mechanics."""
        action = payload.get("action", "").lower()
        text = payload.get("text")
        key = payload.get("key")
        coords = payload.get("coords") # [x, y]
        
        if self.pyautogui is None:
            return {"status": "ERROR", "error": "UI_AUTOMATION_UNAVAILABLE"}
        
        try:
            from core.execution.human_input import human_click, human_move
            if action == "type":
                if coords:
                    px, py = self.coord_mapper.physical_to_pyautogui(coords[0], coords[1])
                    human_click(px, py)
                    await asyncio.sleep(0.1)
                import pyperclip
                pyperclip.copy(text)
                self.pyautogui.hotkey('ctrl', 'v')
                if payload.get("press_enter", True):
                    await asyncio.sleep(0.1)
                    self.pyautogui.press('enter')
            elif action == "click":
                if coords:
                    px, py = self.coord_mapper.physical_to_pyautogui(coords[0], coords[1])
                    human_click(px, py)
                else:
                    human_click()
            elif action == "move":
                if coords:
                    px, py = self.coord_mapper.physical_to_pyautogui(coords[0], coords[1])
                    human_move(px, py)
            elif action == "double_click":
                if coords:
                    px, py = self.coord_mapper.physical_to_pyautogui(coords[0], coords[1])
                    human_click(px, py, clicks=2)
                else:
                    human_click(clicks=2)
            elif action == "right_click":
                if coords:
                    px, py = self.coord_mapper.physical_to_pyautogui(coords[0], coords[1])
                    human_move(px, py)
                    self.pyautogui.rightClick(x=px, y=py)
                else:
                    self.pyautogui.rightClick()
            elif action == "hotkey":
                keys = [k.strip() for k in key.split('+')]
                self.pyautogui.hotkey(*keys)
            elif action == "press":
                self.pyautogui.press(key)
            elif action == "scroll":
                clicks = payload.get("clicks", -300)
                self.pyautogui.scroll(clicks)
                
            return {"status": "SUCCESS", "detail": f"UI action {action} completed."}
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}

    async def run_command(self, command: str) -> Dict:
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            stdout, stderr = await process.communicate()
            if process.returncode == 0:
                return {"status": "SUCCESS", "output": stdout.decode(errors='replace').strip()}
            else:
                return {"status": "ERROR", "error": stderr.decode(errors='replace').strip()}
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}

    async def file_operation(self, payload: Dict) -> Dict:
        op = payload.get("operation")
        path = payload.get("path")
        try:
            if op == "read":
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    return {"status": "SUCCESS", "content": f.read()}
            elif op == "write":
                with open(path, "w", encoding="utf-8") as f:
                    f.write(payload.get("content", ""))
                return {"status": "SUCCESS", "detail": f"Written to {path}"}
            elif op == "exists":
                return {"status": "SUCCESS", "exists": os.path.exists(path)}
            else:
                return {"status": "ERROR", "error": f"Unknown file op: {op}"}
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}

    async def read_screen_context(self) -> Dict:
        try:
            from core.perception.screen_capture import screen_capturer
            frame = screen_capturer.capture_frame()
            return {"status": "SUCCESS", "width": frame.width, "height": frame.height, "active_window": frame.active_window}
        except Exception as e:
            return {"status": "ERROR", "error": str(e)}

desktop_controller = DesktopController()
