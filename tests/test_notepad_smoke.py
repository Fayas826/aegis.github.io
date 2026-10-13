import os
import sys

sys.path.insert(0, os.path.abspath('.'))
import core

import asyncio
import base64
import json
import time
from typing import Dict, Any

import psutil
import pyautogui
import pyperclip

if sys.platform == "win32":
    import win32gui
    import win32process
    try:
        import uiautomation as auto
    except ImportError:
        auto = None

from action.desktop_control.desktop_controller import DesktopController
from core.execution.universal_desktop_controller import universal_controller
from core.perception.screen_capture import screen_capturer

async def run_smoke_test() -> Dict[str, Any]:
    print("=" * 60)
    print("AEGIS Phase 3.1: Native Desktop Control Live Acceptance Test")
    print("=" * 60)
    
    dc = DesktopController()
    diagnostics = {}
    test_file = os.path.abspath(os.path.join("tests", "scratch", "aegis_smoke_test.txt"))
    os.makedirs(os.path.dirname(test_file), exist_ok=True)
    if os.path.exists(test_file):
        try:
            os.remove(test_file)
        except Exception:
            pass

    target_hwnd = None
    target_pid = None
    stub_pid = None
    test_phrase = f"AEGIS OS Control Verified [{int(time.time())}]"

    try:
        # Step 1: Capture initial desktop
        print("\n[STEP 1/10] Capturing initial desktop state...")
        try:
            initial_frame = screen_capturer.capture_frame()
            diagnostics["step_1"] = {
                "status": "PASSED",
                "resolution": f"{initial_frame.width}x{initial_frame.height}",
                "active_window": initial_frame.active_window,
                "active_process": initial_frame.active_process
            }
            print(f" -> Initial desktop captured ({initial_frame.width}x{initial_frame.height}), Active: '{initial_frame.active_window}' ({initial_frame.active_process})")
        except Exception as e_capture:
            msg = str(e_capture)
            print(f" -> Step 1 BLOCKED: {msg}")
            diagnostics["step_1"] = {"status": "BLOCKED", "error": msg}
            return {
                "status": "BLOCKED",
                "failed_step": 1,
                "step_name": "capture_initial_desktop",
                "reason": "DESKTOP_SESSION_UNAVAILABLE",
                "diagnostic_evidence": diagnostics
            }

        # Step 2: Launch Notepad using real AEGIS tool path
        print("\n[STEP 2/10] Launching Notepad via AEGIS open_app tool...")
        t0 = time.time()
        launch_res = await dc.open_app("Notepad", auto_focus=True)
        elapsed_launch = time.time() - t0
        print(f" -> open_app result in {elapsed_launch:.2f}s: {launch_res}")
        
        if launch_res.get("status") == "BLOCKED":
            diagnostics["step_2"] = launch_res
            return {
                "status": "BLOCKED",
                "failed_step": 2,
                "step_name": "launch_notepad",
                "reason": launch_res.get("error", "DESKTOP_SESSION_UNAVAILABLE"),
                "diagnostic_evidence": diagnostics
            }
        
        if launch_res.get("status") != "SUCCESS":
            diagnostics["step_2"] = launch_res
            return {
                "status": "FAILED",
                "failed_step": 2,
                "step_name": "launch_notepad",
                "diagnostic_evidence": diagnostics
            }

        target_hwnd = launch_res.get("hwnd")
        target_pid = launch_res.get("pid")
        stub_pid = dc._last_spawned_pid
        diagnostics["step_2"] = {
            "status": "PASSED",
            "hwnd": target_hwnd,
            "pid": target_pid,
            "elapsed_seconds": round(elapsed_launch, 3)
        }

        # Step 3: Verify visible window and process identity
        print("\n[STEP 3/10] Verifying visible window and process identity...")

        if not target_hwnd or not win32gui.IsWindow(target_hwnd) or not win32gui.IsWindowVisible(target_hwnd):
            diagnostics["step_3"] = {"status": "FAILED", "error": f"Invalid or invisible HWND: {target_hwnd}"}
            return {
                "status": "FAILED",
                "failed_step": 3,
                "step_name": "verify_window_and_process",
                "diagnostic_evidence": diagnostics
            }

        win_title = win32gui.GetWindowText(target_hwnd)
        _, actual_win_pid = win32process.GetWindowThreadProcessId(target_hwnd)
        proc_name = psutil.Process(actual_win_pid).name() if psutil.pid_exists(actual_win_pid) else "unknown"

        if "notepad" not in proc_name.lower():
            diagnostics["step_3"] = {"status": "FAILED", "error": f"Process mismatch: {proc_name} for HWND {target_hwnd}"}
            return {
                "status": "FAILED",
                "failed_step": 3,
                "step_name": "verify_window_and_process",
                "diagnostic_evidence": diagnostics
            }

        diagnostics["step_3"] = {
            "status": "PASSED",
            "hwnd": target_hwnd,
            "title": win_title,
            "pid": actual_win_pid,
            "process_name": proc_name
        }
        print(f" -> Window HWND {target_hwnd} verified: Title='{win_title}', PID={actual_win_pid}, Process='{proc_name}'")

        # Step 4: Verify intended window is foregrounded
        print("\n[STEP 4/10] Verifying intended window is foregrounded...")
        await asyncio.sleep(0.3)
        fg_hwnd = win32gui.GetForegroundWindow()
        if fg_hwnd != target_hwnd:
            # Attempt re-focus
            universal_controller.force_foreground_window(target_hwnd)
            await asyncio.sleep(0.3)
            fg_hwnd = win32gui.GetForegroundWindow()

        fg_title = win32gui.GetWindowText(fg_hwnd) if fg_hwnd else "None"
        if fg_hwnd != target_hwnd:
            diagnostics["step_4"] = {
                "status": "FAILED",
                "expected_hwnd": target_hwnd,
                "actual_fg_hwnd": fg_hwnd,
                "actual_fg_title": fg_title
            }
            return {
                "status": "FAILED",
                "failed_step": 4,
                "step_name": "verify_foreground",
                "diagnostic_evidence": diagnostics
            }

        diagnostics["step_4"] = {"status": "PASSED", "fg_hwnd": fg_hwnd, "fg_title": fg_title}
        print(f" -> Foreground verified: HWND {fg_hwnd} ('{fg_title}') matches target")

        # Step 5: Type harmless phrase
        print(f"\n[STEP 5/10] Typing test phrase: '{test_phrase}'...")
        universal_controller.force_foreground_window(target_hwnd)
        await asyncio.sleep(0.3)
        try:
            if auto:
                win_ctl = auto.ControlFromHandle(target_hwnd)
                doc_ctl = win_ctl.DocumentControl()
                if doc_ctl.Exists(0, 0):
                    doc_ctl.SetFocus()
                else:
                    edit_ctl = win_ctl.EditControl()
                    if edit_ctl.Exists(0, 0):
                        edit_ctl.SetFocus()
        except Exception as e_foc:
            print(f" -> UIA editor focus note: {e_foc}")

        await asyncio.sleep(0.2)
        pyautogui.hotkey('ctrl', 'a')
        await asyncio.sleep(0.1)
        type_res = await dc.automate_ui({
            "action": "type",
            "text": test_phrase,
            "press_enter": True
        })
        print(f" -> Type operation result: {type_res}")
        diagnostics["step_5"] = type_res
        if type_res.get("status") != "SUCCESS":
            return {
                "status": "FAILED",
                "failed_step": 5,
                "step_name": "type_phrase",
                "diagnostic_evidence": diagnostics
            }

        # Step 6: Verify phrase appears in Notepad
        print("\n[STEP 6/10] Verifying phrase appears in Notepad via UI Automation...")
        await asyncio.sleep(0.5)
        text_verified = False
        captured_text = ""
        try:
            if auto:
                win_ctl = auto.ControlFromHandle(target_hwnd)
                doc_ctl = win_ctl.DocumentControl()
                if doc_ctl.Exists(0, 0):
                    captured_text = doc_ctl.GetValuePattern().Value or ""
                else:
                    edit_ctl = win_ctl.EditControl()
                    if edit_ctl.Exists(0, 0):
                        captured_text = edit_ctl.GetValuePattern().Value or ""
            text_verified = test_phrase in captured_text
        except Exception as e_uia:
            print(f" -> UI Automation query notice: {e_uia}")

        diagnostics["step_6"] = {
            "status": "PASSED" if text_verified else "FAILED",
            "captured_text": captured_text[:120],
            "verified": text_verified
        }
        if not text_verified:
            return {
                "status": "FAILED",
                "failed_step": 6,
                "step_name": "verify_phrase_in_notepad",
                "diagnostic_evidence": diagnostics
            }
        print(f" -> Text verified in Notepad editor: '{captured_text.strip()}'")

        # Step 7: Save dedicated test file to explicit test location
        print(f"\n[STEP 7/10] Saving test file to: '{test_file}'...")
        universal_controller.force_foreground_window(target_hwnd)
        await asyncio.sleep(0.3)
        pyautogui.hotkey('ctrl', 'shift', 's')
        await asyncio.sleep(1.0)
        pyperclip.copy(test_file)
        pyautogui.hotkey('ctrl', 'v')
        await asyncio.sleep(0.3)
        pyautogui.press('enter')
        await asyncio.sleep(1.2)
        # Handle overwrite prompt if present
        pyautogui.hotkey('alt', 'y')
        await asyncio.sleep(0.5)

        diagnostics["step_7"] = {"status": "PASSED", "target_path": test_file}
        print(f" -> Save command executed for '{test_file}'")

        # Step 8: Independently verify file exists and contains expected text
        print("\n[STEP 8/10] Independently verifying file existence and contents...")
        file_exists = os.path.exists(test_file)
        file_content = ""
        if file_exists:
            with open(test_file, 'r', encoding='utf-8', errors='replace') as f:
                file_content = f.read()
        content_matches = test_phrase in file_content

        diagnostics["step_8"] = {
            "status": "PASSED" if (file_exists and content_matches) else "FAILED",
            "file_exists": file_exists,
            "content_verified": content_matches,
            "file_content": file_content
        }
        print(f" -> File exists: {file_exists}, Content matches: {content_matches}")
        if not file_exists or not content_matches:
            return {
                "status": "FAILED",
                "failed_step": 8,
                "step_name": "verify_saved_file",
                "diagnostic_evidence": diagnostics
            }

        # Step 9: Record final screenshot
        print("\n[STEP 9/10] Recording final desktop screenshot...")
        final_frame = screen_capturer.capture_frame()
        screenshot_path = os.path.abspath(os.path.join("tests", "scratch", "aegis_final_acceptance.jpg"))
        with open(screenshot_path, "wb") as f_img:
            f_img.write(base64.b64decode(final_frame.image_b64))
        diagnostics["step_9"] = {
            "status": "PASSED",
            "screenshot_path": screenshot_path,
            "resolution": f"{final_frame.width}x{final_frame.height}"
        }
        print(f" -> Final screenshot saved to: '{screenshot_path}'")

        # Step 10: Clean up only test artifact and processes created by test
        print("\n[STEP 10/10] Cleaning up only test processes and artifacts...")
        if os.path.exists(test_file):
            try:
                os.remove(test_file)
                print(f" -> Removed test file: {test_file}")
            except Exception as e_del:
                print(f" -> Note on file cleanup: {e_del}")

        pids_to_clean = [p for p in [actual_win_pid, target_pid, stub_pid] if p]
        dc.cleanup_spawned_processes(pids_to_clean)
        diagnostics["step_10"] = {
            "status": "PASSED",
            "cleaned_pids": pids_to_clean,
            "cleaned_file": test_file
        }
        print(f" -> Terminated only test PIDs: {pids_to_clean}")

        return {
            "status": "PASSED",
            "diagnostic_evidence": diagnostics
        }

    except Exception as e_global:
        diagnostics["fatal_exception"] = str(e_global)
        return {
            "status": "FAILED",
            "error": str(e_global),
            "diagnostic_evidence": diagnostics
        }
    finally:
        # Guarantee process and artifact cleanup even on unexpected failure
        if os.path.exists(test_file):
            try:
                os.remove(test_file)
            except Exception:
                pass
        pids_final = [p for p in [target_pid, stub_pid] if p]
        if pids_final:
            dc.cleanup_spawned_processes(pids_final)

def test_notepad_smoke():
    result = asyncio.run(run_smoke_test())
    assert result.get("status") in ("PASSED", "BLOCKED")

if __name__ == "__main__":
    result = asyncio.run(run_smoke_test())
    print("\n" + "=" * 60)
    print("FINAL SMOKE TEST RESULT:")
    print(json.dumps(result, indent=2))
    print("=" * 60)
    if result.get("status") == "PASSED":
        sys.exit(0)
    elif result.get("status") == "BLOCKED":
        sys.exit(2)
    else:
        sys.exit(1)
