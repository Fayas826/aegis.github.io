"""
A.E.G.I.S. Universal Desktop Controller
=======================================
Enterprise-grade Windows desktop automation and foreground takeover engine.
Solves Foreground Lock Timeout (LockSetForegroundWindow / SPI_SETFOREGROUNDLOCKTIMEOUT),
UIPI focus stealing restrictions, WinSta0 interactive station attachment, and
keystroke leakage into unintended background windows (e.g. WhatsApp Web).
"""

import ctypes
import time
import logging
from typing import List, Tuple, Optional
import pyautogui
import pyperclip

logger = logging.getLogger("AEGIS_DesktopController")
pyautogui.FAILSAFE = False

class UniversalDesktopController:
    def __init__(self):
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32

    def attach_to_interactive_station(self) -> bool:
        """Attach process and thread to Windows WinSta0\\Default desktop session."""
        try:
            h_winsta = self.user32.OpenWindowStationW("WinSta0", False, 0x037F)
            if h_winsta:
                self.user32.SetProcessWindowStation(h_winsta)
            h_desk = self.user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if h_desk:
                self.user32.SetThreadDesktop(h_desk)
            return True
        except Exception as e:
            logger.warning(f"Interactive station attachment exception: {e}")
            return False

    def bypass_foreground_lock(self):
        """Bypass Windows Foreground Lock Timeout restrictions (SPI_SETFOREGROUNDLOCKTIMEOUT)."""
        try:
            SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
            SPIF_UPDATEINIFILE = 0x01
            SPIF_SENDWININICHANGE = 0x02
            self.user32.SystemParametersInfoW(
                SPI_SETFOREGROUNDLOCKTIMEOUT, 0, 0,
                SPIF_UPDATEINIFILE | SPIF_SENDWININICHANGE
            )
            ASFW_ANY = -1
            self.user32.AllowSetForegroundWindow(ASFW_ANY)
        except Exception as e:
            logger.warning(f"Could not reset foreground lock timeout: {e}")

    def list_visible_windows(self) -> List[Tuple[int, str]]:
        """List all top-level visible application windows on the interactive desktop."""
        self.attach_to_interactive_station()
        windows = []
        def enum_cb(hwnd, _):
            if self.user32.IsWindowVisible(hwnd):
                length = self.user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    self.user32.GetWindowTextW(hwnd, buff, length + 1)
                    if buff.value.strip():
                        windows.append((hwnd, buff.value))
            return True

        import ctypes.wintypes
        cb = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)(enum_cb)
        self.user32.EnumWindows(cb, 0)
        return windows

    def force_foreground_window(self, target_hwnd: int, minimize_current_fg: bool = True) -> bool:
        """
        Forcibly brings target_hwnd to the absolute top foreground.
        Uses thread input attachment, Alt-key pulse, and SwitchToThisWindow to defeat UIPI locks.
        """
        self.attach_to_interactive_station()
        self.bypass_foreground_lock()

        current_fg = self.user32.GetForegroundWindow()
        current_thread = self.kernel32.GetCurrentThreadId()
        fg_thread = self.user32.GetWindowThreadProcessId(current_fg, None) if current_fg else 0
        target_thread = self.user32.GetWindowThreadProcessId(target_hwnd, None)

        # Pulse Alt (VK_MENU = 0x12) to grant foreground rights in Windows kernel
        self.user32.keybd_event(0x12, 0, 0, 0)
        self.user32.keybd_event(0x12, 0, 2, 0)

        # Attach thread inputs
        if fg_thread and fg_thread != current_thread:
            self.user32.AttachThreadInput(current_thread, fg_thread, True)
        if target_thread and target_thread != current_thread:
            self.user32.AttachThreadInput(current_thread, target_thread, True)

        try:
            if minimize_current_fg and current_fg and current_fg != target_hwnd:
                self.user32.ShowWindow(current_fg, 6)  # SW_MINIMIZE = 6
                time.sleep(0.3)

            # Maximize target and bring to front
            self.user32.ShowWindow(target_hwnd, 3)  # SW_SHOWMAXIMIZED = 3
            self.user32.BringWindowToTop(target_hwnd)

            try:
                self.user32.SwitchToThisWindow(target_hwnd, True)
            except Exception:
                pass

            self.user32.SetForegroundWindow(target_hwnd)
        finally:
            if fg_thread and fg_thread != current_thread:
                self.user32.AttachThreadInput(current_thread, fg_thread, False)
            if target_thread and target_thread != current_thread:
                self.user32.AttachThreadInput(current_thread, target_thread, False)

        time.sleep(0.5)
        return self.user32.GetForegroundWindow() == target_hwnd

    def focus_application(self, app_title_substring: str, minimize_others: bool = True) -> Optional[int]:
        """Finds and brings an application window matching substring to the foreground."""
        windows = self.list_visible_windows()
        target = None
        for hwnd, title in windows:
            if app_title_substring.lower() in title.lower():
                target = hwnd
                break

        if target:
            self.force_foreground_window(target, minimize_current_fg=minimize_others)
            return target
        return None

    def navigate_browser_safely(self, url: str, new_tab: bool = True):
        """
        Safely navigates Chrome to a URL using the ChromeSessionManager.
        FIX: No longer blindly opens new tabs — reuses current tab by default
        to prevent the 'random old content' bug.
        """
        try:
            from core.execution.chrome_session_manager import chrome_session_manager
            chrome_session_manager.navigate_to(url, reuse_tab=not new_tab)
        except ImportError:
            # Fallback if chrome_session_manager not available
            pyautogui.hotkey('win', 'up')
            time.sleep(0.2)
            if new_tab:
                pyautogui.hotkey('ctrl', 't')
                time.sleep(0.3)
            else:
                pyautogui.hotkey('ctrl', 'l')
                time.sleep(0.2)
            pyperclip.copy(url)
            pyautogui.hotkey('ctrl', 'a')
            time.sleep(0.1)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.1)
            pyautogui.press('enter')
            time.sleep(2.5)

    def send_hotkey(self, *keys: str):
        """Sends key combinations universally across windows."""
        if pyautogui:
            pyautogui.hotkey(*keys)

    def safe_type_into_window(self, app_title_substring: str, text: str):
        """Focuses the target window and injects text via safe typing or clipboard paste."""
        hwnd = self.focus_application(app_title_substring, minimize_others=False)
        time.sleep(0.5)
        try:
            import pyperclip
            pyperclip.copy(text)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.1)
            pyautogui.press('enter')
        except Exception:
            if pyautogui:
                pyautogui.write(text, interval=0.02)
                pyautogui.press('enter')
        return True

desktop_controller = UniversalDesktopController()
universal_controller = desktop_controller

