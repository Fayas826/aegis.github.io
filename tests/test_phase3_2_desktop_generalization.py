"""
AEGIS Phase 3.2: Cross-Application Adaptive Desktop Execution Suite
===================================================================
Comprehensive acceptance tests covering:
  - Test A: Calculator (UIA control pattern invocation, math evaluation, result verification)
  - Test B: File Explorer (Navigation, semantic control discovery, item selection verification)
  - Test C: Terminal Execution (Native API/CLI execution, exit codes, working directory isolation)
  - Test D: Fallback & Recovery (Element not found, uninstalled app, focus mismatch, visual fallback)

Preserves canonical 112-agent architecture, tool registry contracts, and foreground security boundaries.
"""

import os
import sys
import time
import json
import asyncio
import shutil
import tempfile
import traceback
from typing import Dict, Any

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import core

from action.desktop_control.desktop_controller import desktop_controller
from core.execution.universal_desktop_controller import universal_controller
from core.perception.screen_capture import screen_capturer
from core.execution.semantic_ui_controller import semantic_ui_controller
from core.execution.adaptive_execution_router import adaptive_router, ExecutionMethod, TierFallbackProfile
from core.tool_registry.registry import tool_registry

SCRATCH_DIR = os.path.join(PROJECT_ROOT, "tests", "scratch")
os.makedirs(SCRATCH_DIR, exist_ok=True)


async def test_a_calculator() -> Dict[str, Any]:
    """Test A: Calculator Acceptance.
    
    1. Launch Calculator via tool registry / desktop controller.
    2. Verify window and process identity.
    3. Discover controls via UI Automation.
    4. Enter '23 * 17 = ' via InvokePattern.
    5. Verify displayed result is '391'.
    6. Capture screenshot evidence and clean up test instance.
    """
    print("\n" + "=" * 60)
    print("TEST A: Calculator Semantic UI Automation Acceptance")
    print("=" * 60)
    result = {"test": "Test A - Calculator", "status": "FAILED", "steps": {}}
    calc_hwnd = None
    spawned_pid = None

    try:
        # Step 1: Launch Calculator
        print("[Step 1] Launching Calculator via AEGIS desktop controller...")
        launch_res = await desktop_controller.open_app("calc.exe", auto_focus=True)
        result["steps"]["launch"] = launch_res
        if launch_res.get("status") not in ("SUCCESS", "ALREADY_OPEN"):
            raise RuntimeError(f"Calculator failed to launch: {launch_res}")
        spawned_pid = launch_res.get("pid")
        await asyncio.sleep(1.0)

        # Step 2: Acquire and verify window identity
        print("[Step 2] Verifying Calculator window identity...")
        windows = universal_controller.list_visible_windows()
        for hwnd, title in windows:
            if "calculator" in title.lower():
                calc_hwnd = hwnd
                break

        if not calc_hwnd:
            raise RuntimeError("Calculator window could not be located in visible windows.")

        universal_controller.force_foreground_window(calc_hwnd)
        await asyncio.sleep(0.5)

        inspect_res = semantic_ui_controller.inspect_window(hwnd=calc_hwnd)
        result["steps"]["inspect_window"] = inspect_res
        print(f" -> Window confirmed: HWND={calc_hwnd}, Title='{inspect_res.get('title')}', Class='{inspect_res.get('class_name')}'")

        # Step 3: Discover available controls
        print("[Step 3] Discovering UI elements via UI Automation...")
        tree_res = semantic_ui_controller.get_ui_tree(hwnd=calc_hwnd, max_depth=3)
        result["steps"]["control_count"] = tree_res.get("total_elements", 0)
        print(f" -> Discovered {tree_res.get('total_elements', 0)} UI controls in window tree.")

        # Step 4: Input expression: 23 * 17 =
        # In Win11 Calculator: num2Button, num3Button, multiplyButton, num1Button, num7Button, equalButton
        print("[Step 4] Invoking semantic controls for expression: 23 * 17 = ...")
        button_sequence = [
            ("num2Button", "2"),
            ("num3Button", "3"),
            ("multiplyButton", "*"),
            ("num1Button", "1"),
            ("num7Button", "7"),
            ("equalButton", "="),
        ]

        invocation_log = []
        for auto_id, symbol in button_sequence:
            inv_res = semantic_ui_controller.invoke_ui_element(
                hwnd=calc_hwnd,
                automation_id=auto_id,
                verify_focus=False # Win11 calculator child buttons don't have separate HWNDs
            )
            invocation_log.append({"id": auto_id, "symbol": symbol, "status": inv_res.get("status")})
            if inv_res.get("status") != "SUCCESS":
                raise RuntimeError(f"Failed to invoke control '{auto_id}': {inv_res}")
            await asyncio.sleep(0.25)

        result["steps"]["invocations"] = invocation_log

        # Step 5: Read and verify calculation result
        print("[Step 5] Reading calculation result from Calculator display...")
        await asyncio.sleep(0.3)
        read_res = semantic_ui_controller.read_ui_element(
            hwnd=calc_hwnd,
            automation_id="CalculatorResults"
        )
        result["steps"]["read_result"] = read_res
        display_text = read_res.get("value") or read_res.get("name") or ""
        print(f" -> Display raw value: '{display_text}'")

        # In Windows 11 Calculator, text is 'Display is 391' or '391'
        if "391" not in display_text:
            raise AssertionError(f"Expected 391 in calculator display, but got: '{display_text}'")
        
        result["steps"]["verification"] = {
            "expected": "391",
            "actual": display_text,
            "verified": True
        }
        print(" -> Outcome verified: Display correctly equals 391.")

        # Step 6: Evidence capture
        print("[Step 6] Capturing screenshot evidence...")
        screenshot_path = os.path.join(SCRATCH_DIR, "aegis_calc_acceptance.jpg")
        universal_controller.take_screenshot_safely(screenshot_path)
        result["steps"]["screenshot"] = screenshot_path
        print(f" -> Evidence saved to: {screenshot_path}")

        result["status"] = "PASSED"

    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(f" [!] Test A Error: {e}")

    finally:
        # Clean up Calculator instance safely
        print("[Cleanup] Closing Calculator test instance...")
        if calc_hwnd and sys.platform == "win32":
            try:
                import win32gui, win32con
                win32gui.PostMessage(calc_hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
        if spawned_pid:
            desktop_controller.cleanup_spawned_processes([spawned_pid])

    return result


async def test_b_file_explorer() -> Dict[str, Any]:
    """Test B: File Explorer Acceptance.
    
    1. Create isolated temporary test directory and known test file.
    2. Open File Explorer to exact test directory via AEGIS.
    3. Verify displayed window location semantically.
    4. Find and select the known test file via UI Automation.
    5. Verify selected item identity.
    6. Capture evidence and clean up test directory and window.
    """
    print("\n" + "=" * 60)
    print("TEST B: File Explorer Semantic UI Automation Acceptance")
    print("=" * 60)
    result = {"test": "Test B - File Explorer", "status": "FAILED", "steps": {}}
    explorer_hwnd = None
    spawned_pid = None
    test_dir = os.path.join(SCRATCH_DIR, f"explorer_test_{int(time.time())}")
    test_file_name = "target_acceptance_sample.txt"
    test_file_path = os.path.join(test_dir, test_file_name)

    try:
        # Step 1: Create isolated directory & file
        print(f"[Step 1] Creating isolated test directory: '{test_dir}'...")
        os.makedirs(test_dir, exist_ok=True)
        with open(test_file_path, "w", encoding="utf-8") as f:
            f.write("AEGIS Phase 3.2 Semantic File Item Verification")
        result["steps"]["test_dir"] = test_dir
        result["steps"]["test_file"] = test_file_path

        # Step 2: Open File Explorer
        print(f"[Step 2] Opening File Explorer to test directory...")
        launch_res = await desktop_controller.open_app(f"explorer.exe {test_dir}", auto_focus=True)
        result["steps"]["launch"] = launch_res
        spawned_pid = launch_res.get("pid")
        await asyncio.sleep(2.5)

        # Step 3: Acquire and verify Explorer window
        print("[Step 3] Locating File Explorer window...")
        target_dir_name = os.path.basename(test_dir).lower()
        windows = universal_controller.list_visible_windows()
        for hwnd, title in windows:
            t_low = title.lower()
            if target_dir_name in t_low or ("explorer" in t_low and "file explorer" in t_low):
                explorer_hwnd = hwnd
                break

        if not explorer_hwnd:
            # Fallback by window class CabinetWClass
            if sys.platform == "win32":
                import win32gui
                def enum_cb(h, _):
                    nonlocal explorer_hwnd
                    if win32gui.IsWindowVisible(h) and win32gui.GetClassName(h) == "CabinetWClass":
                        explorer_hwnd = h
                win32gui.EnumWindows(enum_cb, None)

        if not explorer_hwnd:
            raise RuntimeError("File Explorer window could not be acquired.")

        universal_controller.force_foreground_window(explorer_hwnd)
        await asyncio.sleep(0.5)

        inspect_res = semantic_ui_controller.inspect_window(hwnd=explorer_hwnd)
        result["steps"]["inspect_window"] = inspect_res
        print(f" -> Window acquired: HWND={explorer_hwnd}, Title='{inspect_res.get('title')}', Class='{inspect_res.get('class_name')}'")

        # Step 4: Find known test file element
        print(f"[Step 4] Searching for UI element '{test_file_name}' via UIA...")
        find_res = semantic_ui_controller.find_ui_element(
            hwnd=explorer_hwnd,
            name=test_file_name,
            timeout=8.0,
            max_depth=7
        )
        result["steps"]["find_element"] = find_res
        if find_res.get("status") != "SUCCESS":
            raise RuntimeError(f"Could not locate element '{test_file_name}' in Explorer tree: {find_res}")
        print(f" -> Found UI element: Type='{find_res.get('control_type')}', Name='{find_res.get('name')}', BoundingBox={find_res.get('bounding_box')}")

        # Step 5: Select known test file
        print(f"[Step 5] Selecting '{test_file_name}' via SelectionItemPattern...")
        select_res = semantic_ui_controller.select_ui_item(
            hwnd=explorer_hwnd,
            name=test_file_name,
            timeout=8.0,
            max_depth=7
        )
        result["steps"]["select_item"] = select_res
        if select_res.get("status") != "SUCCESS":
            raise RuntimeError(f"Selection item invocation failed: {select_res}")
        print(f" -> Selection result: {select_res.get('detail')}")

        # Step 6: Verify item selection identity
        print("[Step 6] Verifying selected state of target item...")
        is_selected = select_res.get("is_selected", False)
        if not is_selected:
            raise AssertionError(f"Item '{test_file_name}' did not confirm IsSelected=True")
        result["steps"]["verification"] = {
            "target_item": test_file_name,
            "is_selected": True,
            "verified": True
        }
        print(" -> Outcome verified: File item correctly selected in File Explorer.")

        # Step 7: Evidence capture
        print("[Step 7] Capturing screenshot evidence...")
        screenshot_path = os.path.join(SCRATCH_DIR, "aegis_explorer_acceptance.jpg")
        universal_controller.take_screenshot_safely(screenshot_path)
        result["steps"]["screenshot"] = screenshot_path
        print(f" -> Evidence saved to: {screenshot_path}")

        result["status"] = "PASSED"

    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(f" [!] Test B Error: {e}")

    finally:
        # Clean up Explorer window
        print("[Cleanup] Closing File Explorer window and deleting test directory...")
        if explorer_hwnd and sys.platform == "win32":
            try:
                import win32gui, win32con
                win32gui.PostMessage(explorer_hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
        # Clean up test artifacts
        if os.path.exists(test_dir):
            try:
                shutil.rmtree(test_dir, ignore_errors=True)
                print(f" -> Removed test directory: {test_dir}")
            except Exception as e:
                print(f" -> Error removing test dir: {e}")

    return result


async def test_c_terminal_command() -> Dict[str, Any]:
    """Test C: Command Execution (Native API / CLI Execution).
    
    1. Invoke native terminal execution through tool router / controller.
    2. Run harmless command 'python --version'.
    3. Capture exit code and output.
    4. Distinguish successful command from unavailable executable.
    5. Verify working directory and environment fidelity.
    """
    print("\n" + "=" * 60)
    print("TEST C: Terminal & Native API Execution Acceptance")
    print("=" * 60)
    result = {"test": "Test C - Terminal Command", "status": "FAILED", "steps": {}}

    try:
        # Step 1: Run python --version in project root
        print(f"[Step 1] Executing 'python --version' in working directory: '{PROJECT_ROOT}'...")
        exec_res = await desktop_controller.run_command("python --version")
        result["steps"]["valid_command"] = exec_res

        # Step 2: Verify exit code and output
        print(f"[Step 2] Validating exit code and stdout/stderr...")
        if exec_res.get("status") != "SUCCESS":
            raise RuntimeError(f"Command execution failed: {exec_res}")
        output = exec_res.get("output", "")
        print(f" -> Raw output: '{output.strip()}'")

        if "python" not in output.lower():
            raise AssertionError(f"Expected python version in output, got: '{output}'")
        
        expected_py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
        if expected_py_ver not in output:
            raise AssertionError(f"Active runtime {expected_py_ver} not matched in output: '{output}'")
        print(f" -> Output verified: matches active runtime {expected_py_ver}")

        # Step 3: Test working directory fidelity
        print("[Step 3] Verifying working directory execution fidelity...")
        cwd_res = await desktop_controller.run_command('python -c "import os; print(os.getcwd())"')
        reported_cwd = cwd_res.get("output", "").strip()
        print(f" -> Subprocess reported cwd: '{reported_cwd}'")
        if not os.path.exists(reported_cwd):
            raise AssertionError(f"Reported cwd does not exist: '{reported_cwd}'")
        result["steps"]["cwd_verification"] = {"reported_cwd": reported_cwd, "valid": True}

        # Step 4: Test unavailable executable distinction
        print("[Step 4] Executing invalid/unavailable executable to test distinct failure reporting...")
        bogus_cmd = "non_existent_executable_aegis_99999 --help"
        bogus_res = await desktop_controller.run_command(bogus_cmd)
        result["steps"]["invalid_command"] = bogus_res
        print(f" -> Bogus command result: {bogus_res}")

        # Must report ERROR, not SUCCESS
        if bogus_res.get("status") == "SUCCESS":
            raise AssertionError("Bogus executable returned SUCCESS unexpectedly.")
        print(" -> Correctly distinguished unavailable executable as ERROR.")

        result["status"] = "PASSED"

    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(f" [!] Test C Error: {e}")

    return result


async def test_d_fallback_and_recovery() -> Dict[str, Any]:
    """Test D: Fallback, Diagnostics and Recovery.
    
    1. UI Automation cannot find requested control -> returns structured ELEMENT_NOT_FOUND error.
    2. Requested application not installed -> returns structured APPLICATION_NOT_FOUND error.
    3. Active window focus mismatch -> suppresses input, returns FOCUS_MISMATCH.
    4. Adaptive execution router capabilities and fallback ladder logic.
    """
    print("\n" + "=" * 60)
    print("TEST D: Fallback, Recovery and Safety Architecture Acceptance")
    print("=" * 60)
    result = {"test": "Test D - Fallback & Recovery", "status": "FAILED", "steps": {}}

    try:
        # Case 1: Control not found in UIA
        print("[Case 1] Querying non-existent control in desktop window...")
        bogus_find = semantic_ui_controller.find_ui_element(
            name="non_existent_control_button_123456789",
            automation_id="phantom_control_id_xyz"
        )
        result["steps"]["element_not_found"] = bogus_find
        if bogus_find.get("status") != "ELEMENT_NOT_FOUND":
            raise AssertionError(f"Expected ELEMENT_NOT_FOUND, got: {bogus_find.get('status')}")
        print(f" -> Correctly returned structured error: {bogus_find.get('error')}")

        # Case 2: Uninstalled application launch
        print("[Case 2] Launching non-existent application...")
        bogus_app = await desktop_controller.open_app("non_existent_fantasy_app_999.exe")
        result["steps"]["uninstalled_app"] = bogus_app
        if bogus_app.get("status") != "ERROR":
            raise AssertionError(f"Expected ERROR for uninstalled app, got: {bogus_app.get('status')}")
        print(f" -> Correctly reported error: {bogus_app.get('error')}")

        # Case 3: Focus mismatch protection
        print("[Case 3] Testing FOCUS_MISMATCH input protection...")
        # Supply a fictitious HWND that is guaranteed not to be foreground
        mismatch_res = semantic_ui_controller.invoke_ui_element(
            hwnd=9999999,
            name="dummy",
            verify_focus=True
        )
        result["steps"]["focus_mismatch"] = mismatch_res
        if mismatch_res.get("status") != "FOCUS_MISMATCH":
            raise AssertionError(f"Expected FOCUS_MISMATCH, got: {mismatch_res.get('status')}")
        print(f" -> Correctly blocked execution with: {mismatch_res.get('error')}")

        # Case 4: Adaptive router tier selection and fallback ladder
        print("[Case 4] Testing Adaptive Router profiling and fallback selection...")
        plan = adaptive_router.select_execution_tier(
            task_description="Run python test script",
            app_identity="python",
            is_cli_intent=True
        )
        result["steps"]["router_plan_cli"] = {
            "tier": plan.tier.value,
            "target": plan.target,
            "can_fallback": plan.can_fallback
        }
        if plan.tier != ExecutionMethod.NATIVE_API:
            raise AssertionError(f"Expected NATIVE_API for CLI task, got {plan.tier}")
        print(f" -> Router correctly routed CLI task to: {plan.tier.value}")

        plan_gui = adaptive_router.select_execution_tier(
            task_description="Click Save button in Notepad",
            app_identity="notepad",
            is_cli_intent=False
        )
        result["steps"]["router_plan_gui"] = {
            "tier": plan_gui.tier.value,
            "target": plan_gui.target,
            "fallback_tiers": [f.value for f in plan_gui.fallback_tiers]
        }
        if plan_gui.tier != ExecutionMethod.WINDOWS_UIA:
            raise AssertionError(f"Expected WINDOWS_UIA for standard Windows app, got {plan_gui.tier}")
        if ExecutionMethod.VISUAL_COMPUTER_USE not in plan_gui.fallback_tiers:
            raise AssertionError("Expected VISUAL_COMPUTER_USE in fallback ladder for Windows GUI")
        print(f" -> Router correctly selected {plan_gui.tier.value} with fallback ladder: {[f.value for f in plan_gui.fallback_tiers]}")

        result["status"] = "PASSED"

    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = str(e)
        result["traceback"] = traceback.format_exc()
        print(f" [!] Test D Error: {e}")

    return result


async def main():
    print("=" * 70)
    print("AEGIS PHASE 3.2 — CROSS-APPLICATION ACCEPTANCE SUITE")
    print("=" * 70)

    start_time = time.time()
    results = {}

    results["test_a"] = await test_a_calculator()
    results["test_b"] = await test_b_file_explorer()
    results["test_c"] = await test_c_terminal_command()
    results["test_d"] = await test_d_fallback_and_recovery()

    total_elapsed = time.time() - start_time

    summary = {
        "timestamp": time.time(),
        "total_elapsed_seconds": round(total_elapsed, 2),
        "results": {k: v.get("status") for k, v in results.items()},
        "all_passed": all(v.get("status") == "PASSED" for v in results.values())
    }

    print("\n" + "=" * 70)
    print("SUITE SUMMARY:")
    print(json.dumps(summary, indent=2))
    print("=" * 70)

    # Save test results to scratch directory
    result_file = os.path.join(SCRATCH_DIR, "aegis_phase3_2_acceptance_results.json")
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "details": results}, f, indent=2)
    print(f"Detailed results written to: {result_file}")

    if not summary["all_passed"]:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
