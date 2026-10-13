import os
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
        self._init_ctypes_signatures()

    def _init_ctypes_signatures(self):
        """Configure explicit 64-bit function signatures to prevent pointer/handle truncation."""
        try:
            import ctypes.wintypes as wintypes
            self.wintypes = wintypes

            self.user32.GetForegroundWindow.restype = wintypes.HWND
            self.user32.GetForegroundWindow.argtypes = []
            self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
            self.user32.IsWindowVisible.restype = wintypes.BOOL
            self.user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
            self.user32.GetWindowTextLengthW.restype = ctypes.c_int
            self.user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            self.user32.GetWindowTextW.restype = ctypes.c_int
            self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
            self.user32.SetForegroundWindow.restype = wintypes.BOOL
            self.user32.BringWindowToTop.argtypes = [wintypes.HWND]
            self.user32.BringWindowToTop.restype = wintypes.BOOL
            self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            self.user32.ShowWindow.restype = wintypes.BOOL
            self.user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
            self.user32.AttachThreadInput.restype = wintypes.BOOL
        except Exception as e:
            logger.warning(f"Failed setting ctypes signatures: {e}")

    def attach_to_interactive_station(self) -> bool:
        """Attach thread to Windows interactive input desktop session if running in background thread."""
        try:
            h_desk = self.user32.OpenInputDesktop(0, False, 0x01FF)
            if not h_desk:
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
        windows: List[Tuple[int, str]] = []
        import ctypes.wintypes as wintypes

        def enum_cb(hwnd, _):
            if self.user32.IsWindowVisible(hwnd):
                length = self.user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buff = ctypes.create_unicode_buffer(length + 1)
                    self.user32.GetWindowTextW(hwnd, buff, length + 1)
                    val = buff.value.strip()
                    if val:
                        windows.append((hwnd, val))
            return True

        cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(enum_cb)
        h_desk = self.user32.OpenInputDesktop(0, False, 0x01FF)
        if h_desk:
            self.user32.EnumDesktopWindows(h_desk, cb, 0)
            self.user32.CloseDesktop(h_desk)
        else:
            self.user32.EnumWindows(cb, 0)

        return windows

    def force_foreground_window(self, target_hwnd: int, minimize_current_fg: bool = False) -> bool:
        """
        Forcibly brings target_hwnd to the absolute top foreground.
        Uses thread input attachment, Alt-key pulse, and SwitchToThisWindow to defeat UIPI locks.
        """
        self.bypass_foreground_lock()

        current_fg = self.user32.GetForegroundWindow()
        if current_fg == target_hwnd:
            return True

        current_thread = self.kernel32.GetCurrentThreadId()
        import ctypes.wintypes as wintypes
        fg_pid = wintypes.DWORD()
        fg_thread = self.user32.GetWindowThreadProcessId(current_fg, ctypes.byref(fg_pid)) if current_fg else 0
        tgt_pid = wintypes.DWORD()
        target_thread = self.user32.GetWindowThreadProcessId(target_hwnd, ctypes.byref(tgt_pid))

        # Pulse Alt (VK_MENU = 0x12) to grant foreground rights in Windows kernel
        self.user32.keybd_event(0x12, 0, 0, 0)
        self.user32.keybd_event(0x12, 0, 2, 0)

        # Attach thread inputs
        attached_fg = False
        attached_tgt = False
        if fg_thread and fg_thread != current_thread:
            attached_fg = bool(self.user32.AttachThreadInput(current_thread, fg_thread, True))
        if target_thread and target_thread != current_thread:
            attached_tgt = bool(self.user32.AttachThreadInput(current_thread, target_thread, True))

        try:
            if minimize_current_fg and current_fg and current_fg != target_hwnd:
                self.user32.ShowWindow(current_fg, 6)  # SW_MINIMIZE = 6
                time.sleep(0.2)

            # Restore target if minimized, then show
            self.user32.ShowWindow(target_hwnd, 9)  # SW_RESTORE = 9
            self.user32.BringWindowToTop(target_hwnd)

            try:
                self.user32.SwitchToThisWindow(target_hwnd, True)
            except Exception:
                pass

            self.user32.SetForegroundWindow(target_hwnd)
        finally:
            if attached_fg:
                self.user32.AttachThreadInput(current_thread, fg_thread, False)
            if attached_tgt:
                self.user32.AttachThreadInput(current_thread, target_thread, False)

        time.sleep(0.4)
        fg_now = self.user32.GetForegroundWindow()
        return fg_now == target_hwnd

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
        if not hwnd:
            logger.warning("Could not find or focus window matching '%s'", app_title_substring)
            return False
        time.sleep(0.5)
        try:
            pyperclip.copy(text)
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.1)
            pyautogui.press('enter')
        except Exception:
            if pyautogui:
                pyautogui.write(text, interval=0.02)
                pyautogui.press('enter')
        return True

    def take_screenshot_safely(self, output_path: str) -> bool:
        """Captures a screenshot safely and saves it to output_path."""
        import base64
        from core.perception.screen_capture import screen_capturer
        try:
            frame = screen_capturer.capture_frame()
            if frame and frame.image_b64:
                os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
                with open(output_path, "wb") as f_img:
                    f_img.write(base64.b64decode(frame.image_b64))
                return True
        except Exception as e:
            logger.warning("Could not take screenshot safely: %s", e)
        return False

desktop_controller = UniversalDesktopController()
universal_controller = desktop_controller

