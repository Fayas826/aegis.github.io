import sys
import ctypes

# Windows Station Attachment MUST happen before any GDI, COM, or PIL initialization
if sys.platform == "win32":
    try:
        user32 = ctypes.windll.user32
        h_winsta = user32.OpenWindowStationW("WinSta0", False, 0x037F)
        if h_winsta:
            user32.SetProcessWindowStation(h_winsta)
        h_desk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if h_desk:
            user32.SetThreadDesktop(h_desk)
    except Exception:
        pass

import os
import base64
import time
from PIL import Image
from io import BytesIO
from typing import Dict, Any

try:
    import pyautogui
except ImportError:
    pyautogui = None

try:
    import pygetwindow as gw
except ImportError:
    gw = None

from core.perception.visual_state import ScreenFrame
from core.perception.browser_driver import browser_driver

class ScreenCapturer:
    """Perception Capture interface extracting dynamic desktop images and contexts."""
    
    def capture_frame(self) -> ScreenFrame:
        if pyautogui is None:
            raise RuntimeError("PyAutoGUI driver missing.")
            
        # 1. Capture screen image (attach thread to active default desktop and WinSta0)
        try:
            import ctypes
            user32 = ctypes.windll.user32
            h_winsta = user32.OpenWindowStationW("WinSta0", False, 0x037F)
            if h_winsta:
                user32.SetProcessWindowStation(h_winsta)
            h_desk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if h_desk:
                user32.SetThreadDesktop(h_desk)
        except Exception as desk_e:
            print(f"[SCREEN_CAPTURE] Warning: Failed to attach thread to Default desktop: {desk_e}")

        # Try MSS first (fastest and immune to GDI lock issues), fallback to PIL/pyautogui
        screenshot = None
        try:
            import mss
            with mss.mss() as sct:
                monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
                sct_img = sct.grab(monitor)
                screenshot = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        except Exception:
            try:
                screenshot = pyautogui.screenshot()
            except Exception:
                from PIL import ImageGrab
                screenshot = ImageGrab.grab()

        width, height = screenshot.size
        
        # Fast JPEG encoding for 10x lower latency and smaller transfer payload
        buffered = BytesIO()
        if screenshot.mode in ("RGBA", "P"):
            screenshot = screenshot.convert("RGB")
        screenshot.save(buffered, format="JPEG", quality=80)
        img_bytes = buffered.getvalue()
        img_b64 = base64.b64encode(img_bytes).decode("utf-8")
        
        # 2. Identify active window name and process
        active_window_title = "Desktop"
        active_process_name = "none"
        if sys.platform == "win32":
            try:
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buff, length + 1)
                        if buff.value.strip():
                            active_window_title = buff.value.strip()
                    try:
                        import ctypes.wintypes
                        import psutil
                        pid = ctypes.wintypes.DWORD()
                        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                        if pid.value > 0:
                            active_process_name = psutil.Process(pid.value).name().lower()
                    except Exception:
                        pass
            except Exception:
                pass
        elif gw is not None:
            try:
                active_win = gw.getActiveWindow()
                if active_win and active_win.title:
                    active_window_title = active_win.title
            except Exception as e:
                from core.reliability.system_logger import system_logger
                system_logger.log('ERROR', 'screen_capture', f'Unhandled exception: {e}')
                pass
                
        # 3. Compile ScreenFrame representation
        return ScreenFrame(
            image_b64=img_b64,
            width=width,
            height=height,
            active_window=active_window_title,
            active_process=active_process_name
        )

    async def capture_frame_async(self) -> ScreenFrame:
        """Asynchronous capture frame populating active DOM elements from Playwright."""
        frame = self.capture_frame()
        # Query active DOM elements if Playwright is running
        dom_elements = await browser_driver.get_active_elements()
        if dom_elements:
            frame.browser_context = {"elements": dom_elements}
        return frame

screen_capturer = ScreenCapturer()
