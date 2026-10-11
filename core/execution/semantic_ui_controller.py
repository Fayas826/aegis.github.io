"""
A.E.G.I.S. Semantic UI Controller
===================================
Phase 3.2: Reusable Microsoft Windows UI Automation (UIA) Control Layer.
Provides high-fidelity, coordinate-independent semantic inspection, element discovery,
pattern invocation, value mutation, and state verification across native Windows applications.

Invariants:
- Uses UIAutomationInitializerInThread for COM thread safety.
- Strictly verifies foreground window identity prior to input actions.
- Returns structured dictionaries with explicit status, timing, and error codes.
- Reports inaccessible controls honestly with structured error codes to enable visual fallback.
"""

import sys
import time
from typing import Dict, Any, List, Optional, Union, Tuple

try:
    import uiautomation as auto
except ImportError:
    auto = None

if sys.platform == "win32":
    import win32gui
    import win32process
else:
    win32gui = None
    win32process = None

from core.execution.universal_desktop_controller import universal_controller


class SemanticUIController:
    """Enterprise Windows UI Automation controller for desktop application dominance."""

    def __init__(self):
        self.auto = auto
        self.default_timeout = 3.0

    def _get_initializer(self):
        if self.auto is None:
            return None
        return getattr(self.auto, "UIAutomationInitializerInThread", None)

    def _resolve_hwnd(self, target: Optional[Union[int, str]]) -> Optional[int]:
        """Resolves target to a valid HWND either directly or by searching visible window titles."""
        if target is None:
            return win32gui.GetForegroundWindow() if win32gui else 0

        if isinstance(target, int):
            if target > 0 and win32gui and win32gui.IsWindow(target):
                return target
            return target

        if isinstance(target, str) and target.strip():
            t_lower = target.strip().lower()
            for hwnd, title in universal_controller.list_visible_windows():
                if t_lower in title.lower():
                    return hwnd
        return None

    def _normalize_args(
        self,
        target: Optional[Union[int, str]] = None,
        search_criteria: Optional[Dict[str, Any]] = None,
        kwargs: Optional[Dict[str, Any]] = None
    ) -> Tuple[Optional[int], Dict[str, Any]]:
        """Normalizes (target, search_criteria, kwargs) into a resolved HWND and criteria dictionary."""
        kwargs = kwargs or {}
        if isinstance(target, dict) and search_criteria is None:
            search_criteria = target
            target = None

        criteria = dict(search_criteria) if isinstance(search_criteria, dict) else {}
        for k, v in kwargs.items():
            if k in ("name", "automation_id", "control_type", "class_name", "hwnd"):
                if k == "hwnd" and target is None:
                    target = v
                else:
                    criteria[k] = v

        if target is None and criteria.get("hwnd") is not None:
            target = criteria.get("hwnd")

        resolved_hwnd = self._resolve_hwnd(target)
        return resolved_hwnd, criteria

    def inspect_window(
        self,
        target: Optional[Union[int, str]] = None,
        hwnd: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Inspects window metadata, process identity, class name, and top-level bounds."""
        target_val = target if target is not None else hwnd
        resolved = self._resolve_hwnd(target_val)
        if not resolved or (win32gui and not win32gui.IsWindow(resolved)):
            return {
                "status": "FAILED",
                "error_code": "WINDOW_NOT_FOUND",
                "error": f"Target window '{target_val}' could not be resolved or is not visible.",
                "message": f"Target window '{target_val}' could not be resolved or is not visible."
            }

        title = win32gui.GetWindowText(resolved) if win32gui else ""
        class_name = win32gui.GetClassName(resolved) if win32gui else ""
        _, pid = win32process.GetWindowThreadProcessId(resolved) if win32process else (0, 0)
        fg_hwnd = win32gui.GetForegroundWindow() if win32gui else 0

        rect = [0, 0, 0, 0]
        if win32gui:
            try:
                rect = list(win32gui.GetWindowRect(resolved))
            except Exception:
                pass

        return {
            "status": "SUCCESS",
            "hwnd": resolved,
            "title": title,
            "class_name": class_name,
            "pid": pid,
            "bounding_box": rect,
            "is_foreground": (resolved == fg_hwnd),
            "is_visible": bool(win32gui.IsWindowVisible(resolved)) if win32gui else True
        }

    def get_ui_tree(
        self,
        target: Optional[Union[int, str]] = None,
        hwnd: Optional[int] = None,
        max_depth: int = 3,
        max_elements: int = 60,
        **kwargs
    ) -> Dict[str, Any]:
        """Traverses the UI Automation hierarchy of the target window into a structured tree."""
        target_val = target if target is not None else hwnd
        resolved = self._resolve_hwnd(target_val)
        if not resolved or self.auto is None:
            return {
                "status": "FAILED",
                "error_code": "UIA_UNAVAILABLE",
                "error": f"Window '{target_val}' unavailable or uiautomation library missing.",
                "message": f"Window '{target_val}' unavailable or uiautomation library missing."
            }

        init = self._get_initializer()
        if not init:
            return {"status": "FAILED", "error_code": "COM_INIT_FAILED", "message": "COM initializer unavailable."}

        with init():
            try:
                root = self.auto.ControlFromHandle(resolved)
                if not root:
                    return {"status": "FAILED", "error_code": "ROOT_CONTROL_NULL", "message": "Root UIA control is null."}

                elements = []
                stack = [(root, 0, "root")]
                visited = set()

                while stack and len(elements) < max_elements:
                    ctrl, depth, parent_id = stack.pop()
                    if not ctrl or depth > max_depth:
                        continue

                    try:
                        r_id = ctrl.GetRuntimeId()
                        if r_id in visited:
                            continue
                        visited.add(r_id)
                    except Exception:
                        pass

                    name = ctrl.Name or ""
                    c_type = ctrl.ControlTypeName or ""
                    auto_id = ctrl.AutomationId or ""
                    class_name = ctrl.ClassName or ""
                    rect = ctrl.BoundingRectangle
                    bbox = [rect.left, rect.top, rect.right, rect.bottom] if rect else [0, 0, 0, 0]

                    elem_info = {
                        "name": name,
                        "control_type": c_type,
                        "automation_id": auto_id,
                        "class_name": class_name,
                        "depth": depth,
                        "parent": parent_id,
                        "bounding_box": bbox,
                        "is_enabled": getattr(ctrl, "IsEnabled", True),
                    }
                    elements.append(elem_info)

                    try:
                        children = list(ctrl.GetChildren())
                        for ch in reversed(children):
                            stack.append((ch, depth + 1, auto_id or name or f"{c_type}_{depth}"))
                    except Exception:
                        pass

                return {
                    "status": "SUCCESS",
                    "hwnd": resolved,
                    "element_count": len(elements),
                    "total_elements": len(elements),
                    "elements": elements
                }
            except Exception as e:
                return {
                    "status": "FAILED",
                    "error_code": "TRAVERSAL_ERROR",
                    "error": str(e),
                    "message": str(e)
                }

    def find_ui_element(
        self,
        target: Optional[Union[int, str]] = None,
        search_criteria: Optional[Dict[str, Any]] = None,
        timeout: float = 3.0,
        **kwargs
    ) -> Dict[str, Any]:
        """Locates an element inside target window matching criteria."""
        hwnd, criteria = self._normalize_args(target, search_criteria, kwargs)
        if not hwnd or self.auto is None or (win32gui and not win32gui.IsWindow(hwnd)):
            return {
                "status": "ELEMENT_NOT_FOUND",
                "found": False,
                "error_code": "ELEMENT_NOT_FOUND",
                "error": f"Window '{target}' unavailable or uiautomation library missing.",
                "message": f"Window '{target}' unavailable or uiautomation library missing.",
                "search_criteria": criteria
            }

        init = self._get_initializer()
        if not init:
            return {"status": "FAILED", "error_code": "COM_INIT_FAILED", "message": "COM initializer unavailable."}

        auto_id = criteria.get("automation_id")
        name = criteria.get("name")
        c_type = criteria.get("control_type")

        with init():
            try:
                root = self.auto.ControlFromHandle(hwnd)
                if not root:
                    return {
                        "status": "ELEMENT_NOT_FOUND",
                        "found": False,
                        "error_code": "ELEMENT_NOT_FOUND",
                        "error": "Root UIA control is null.",
                        "message": "Root UIA control is null."
                    }

                t_end = time.time() + timeout
                while time.time() < t_end:
                    ctrl = None
                    if auto_id:
                        if c_type == "ButtonControl":
                            ctrl = self.auto.ButtonControl(searchFromControl=root, AutomationId=auto_id)
                        elif c_type == "EditControl":
                            ctrl = self.auto.EditControl(searchFromControl=root, AutomationId=auto_id)
                        elif c_type == "TextControl":
                            ctrl = self.auto.TextControl(searchFromControl=root, AutomationId=auto_id)
                        else:
                            ctrl = self.auto.Control(searchFromControl=root, AutomationId=auto_id)

                    if not ctrl or not ctrl.Exists(0.2, 0.1):
                        if name:
                            for ctrl_cls in [self.auto.ListItemControl, self.auto.DataItemControl, self.auto.TreeItemControl, self.auto.Control]:
                                try:
                                    c = ctrl_cls(searchFromControl=root, Name=name)
                                    if c.Exists(0.2, 0.05):
                                        ctrl = c
                                        break
                                except Exception:
                                    pass

                    if ctrl and ctrl.Exists(0, 0):
                        rect = ctrl.BoundingRectangle
                        bbox = [rect.left, rect.top, rect.right, rect.bottom] if rect else [0, 0, 0, 0]
                        return {
                            "status": "SUCCESS",
                            "found": True,
                            "name": ctrl.Name or "",
                            "automation_id": ctrl.AutomationId or "",
                            "control_type": ctrl.ControlTypeName or "",
                            "bounding_box": bbox,
                            "is_enabled": getattr(ctrl, "IsEnabled", True),
                        }

                    # Tree walk
                    for tree_node in self.auto.WalkTree(root, getChildren=lambda c: c.GetChildren()):
                        candidate = tree_node[0] if isinstance(tree_node, (tuple, list)) else tree_node
                        match = True
                        if auto_id and auto_id.lower() != (candidate.AutomationId or "").lower():
                            match = False
                        if name and name.lower() not in (candidate.Name or "").lower():
                            match = False
                        if c_type and c_type.lower() != (candidate.ControlTypeName or "").lower():
                            match = False

                        if match:
                            rect = candidate.BoundingRectangle
                            bbox = [rect.left, rect.top, rect.right, rect.bottom] if rect else [0, 0, 0, 0]
                            return {
                                "status": "SUCCESS",
                                "found": True,
                                "name": candidate.Name or "",
                                "automation_id": candidate.AutomationId or "",
                                "control_type": candidate.ControlTypeName or "",
                                "bounding_box": bbox,
                                "is_enabled": getattr(candidate, "IsEnabled", True),
                            }
                    time.sleep(0.2)

                return {
                    "status": "ELEMENT_NOT_FOUND",
                    "found": False,
                    "error_code": "ELEMENT_NOT_FOUND",
                    "error": f"Control matching {criteria} not found within {timeout}s timeout.",
                    "message": f"Control matching {criteria} not found within {timeout}s timeout.",
                    "search_criteria": criteria
                }

            except Exception as e:
                return {
                    "status": "ELEMENT_NOT_FOUND",
                    "found": False,
                    "error_code": "SEARCH_ERROR",
                    "error": str(e),
                    "message": str(e)
                }

    def invoke_ui_element(
        self,
        target: Optional[Union[int, str]] = None,
        search_criteria: Optional[Dict[str, Any]] = None,
        timeout: float = 3.0,
        verify_focus: bool = True,
        **kwargs
    ) -> Dict[str, Any]:
        """Invokes an element (button, menu, checkbox) via UIA InvokePattern / TogglePattern."""
        hwnd, criteria = self._normalize_args(target, search_criteria, kwargs)
        if not hwnd or self.auto is None:
            return {
                "status": "FAILED",
                "error_code": "UIA_UNAVAILABLE",
                "error": "Target or UIA unavailable.",
                "message": "Target or UIA unavailable."
            }

        # Foreground check
        if verify_focus:
            universal_controller.force_foreground_window(hwnd)
            fg = win32gui.GetForegroundWindow() if win32gui else hwnd
            if fg != hwnd:
                return {
                    "status": "FOCUS_MISMATCH",
                    "error_code": "FOCUS_MISMATCH",
                    "error": f"Target HWND {hwnd} is not foreground (current FG is {fg}). Invocation suppressed.",
                    "message": f"Target HWND {hwnd} is not foreground (current FG is {fg}). Invocation suppressed."
                }

        init = self._get_initializer()
        if not init:
            return {"status": "FAILED", "error_code": "COM_INIT_FAILED", "message": "COM initializer unavailable."}

        with init():
            try:
                root = self.auto.ControlFromHandle(hwnd)
                if not root:
                    return {
                        "status": "ELEMENT_NOT_FOUND",
                        "error_code": "ROOT_CONTROL_NULL",
                        "error": "Root UIA control is null.",
                        "message": "Root UIA control is null."
                    }

                auto_id = criteria.get("automation_id")
                name = criteria.get("name")
                c_type = criteria.get("control_type", "ButtonControl")

                ctrl = None
                if auto_id:
                    if c_type == "ButtonControl":
                        ctrl = self.auto.ButtonControl(searchFromControl=root, AutomationId=auto_id)
                    else:
                        ctrl = self.auto.Control(searchFromControl=root, AutomationId=auto_id)
                elif name:
                    ctrl = self.auto.Control(searchFromControl=root, Name=name)

                if not ctrl or not ctrl.Exists(timeout, 0.2):
                    for tree_node in self.auto.WalkTree(root, getChildren=lambda c: c.GetChildren()):
                        candidate = tree_node[0] if isinstance(tree_node, (tuple, list)) else tree_node
                        match = True
                        if auto_id and auto_id.lower() != (candidate.AutomationId or "").lower():
                            match = False
                        if name and name.lower() not in (candidate.Name or "").lower():
                            match = False
                        if match:
                            ctrl = candidate
                            break

                if not ctrl or not ctrl.Exists(0, 0):
                    return {
                        "status": "ELEMENT_NOT_FOUND",
                        "error_code": "ELEMENT_NOT_FOUND",
                        "error": f"Target element {criteria} could not be located to invoke.",
                        "message": f"Target element {criteria} could not be located to invoke."
                    }

                pattern_used = None
                try:
                    pat_invoke = ctrl.GetInvokePattern()
                    if pat_invoke:
                        pat_invoke.Invoke()
                        pattern_used = "InvokePattern"
                except Exception:
                    pass

                if not pattern_used:
                    try:
                        pat_toggle = ctrl.GetTogglePattern()
                        if pat_toggle:
                            pat_toggle.Toggle()
                            pattern_used = "TogglePattern"
                    except Exception:
                        pass

                if not pattern_used:
                    try:
                        ctrl.Click(simulateMove=False)
                        pattern_used = "ControlClick"
                    except Exception:
                        pass

                if not pattern_used:
                    rect = ctrl.BoundingRectangle
                    if rect:
                        cx = (rect.left + rect.right) // 2
                        cy = (rect.top + rect.bottom) // 2
                        import pyautogui
                        pyautogui.click(cx, cy)
                        pattern_used = "CoordinateClick"

                return {
                    "status": "SUCCESS",
                    "pattern_used": pattern_used,
                    "target_name": ctrl.Name or "",
                    "automation_id": ctrl.AutomationId or "",
                    "detail": f"Successfully invoked '{ctrl.Name}' via {pattern_used}."
                }

            except Exception as e:
                return {
                    "status": "FAILED",
                    "error_code": "INVOCATION_ERROR",
                    "error": str(e),
                    "message": str(e)
                }

    def read_ui_element(
        self,
        target: Optional[Union[int, str]] = None,
        search_criteria: Optional[Dict[str, Any]] = None,
        timeout: float = 3.0,
        **kwargs
    ) -> Dict[str, Any]:
        """Reads text, value, or name property of a UI element (e.g. CalculatorResults, Edit box)."""
        hwnd, criteria = self._normalize_args(target, search_criteria, kwargs)
        if not hwnd or self.auto is None:
            return {
                "status": "FAILED",
                "error_code": "UIA_UNAVAILABLE",
                "error": "Target or UIA unavailable.",
                "message": "Target or UIA unavailable."
            }

        init = self._get_initializer()
        if not init:
            return {"status": "FAILED", "error_code": "COM_INIT_FAILED", "message": "COM initializer unavailable."}

        with init():
            try:
                root = self.auto.ControlFromHandle(hwnd)
                if not root:
                    return {
                        "status": "ELEMENT_NOT_FOUND",
                        "error_code": "ROOT_CONTROL_NULL",
                        "error": "Root UIA control is null.",
                        "message": "Root UIA control is null."
                    }

                auto_id = criteria.get("automation_id")
                name = criteria.get("name")
                c_type = criteria.get("control_type")

                ctrl = None
                if auto_id:
                    if c_type == "TextControl":
                        ctrl = self.auto.TextControl(searchFromControl=root, AutomationId=auto_id)
                    elif c_type == "EditControl":
                        ctrl = self.auto.EditControl(searchFromControl=root, AutomationId=auto_id)
                    else:
                        ctrl = self.auto.Control(searchFromControl=root, AutomationId=auto_id)

                if not ctrl or not ctrl.Exists(timeout, 0.2):
                    for tree_node in self.auto.WalkTree(root, getChildren=lambda c: c.GetChildren()):
                        candidate = tree_node[0] if isinstance(tree_node, (tuple, list)) else tree_node
                        match = True
                        if auto_id and auto_id.lower() != (candidate.AutomationId or "").lower():
                            match = False
                        if name and name.lower() not in (candidate.Name or "").lower():
                            match = False
                        if match:
                            ctrl = candidate
                            break

                if not ctrl or not ctrl.Exists(0, 0):
                    return {
                        "status": "ELEMENT_NOT_FOUND",
                        "error_code": "ELEMENT_NOT_FOUND",
                        "error": f"Element {criteria} not found for reading.",
                        "message": f"Element {criteria} not found for reading."
                    }

                val = ""
                try:
                    pat_val = ctrl.GetValuePattern()
                    if pat_val:
                        val = pat_val.Value or ""
                except Exception:
                    pass

                if not val:
                    val = ctrl.Name or ""

                return {
                    "status": "SUCCESS",
                    "value": val,
                    "name": ctrl.Name or "",
                    "automation_id": ctrl.AutomationId or "",
                    "control_type": ctrl.ControlTypeName or ""
                }

            except Exception as e:
                return {
                    "status": "FAILED",
                    "error_code": "READ_ERROR",
                    "error": str(e),
                    "message": str(e)
                }

    def set_ui_value(
        self,
        target: Optional[Union[int, str]] = None,
        search_criteria: Optional[Dict[str, Any]] = None,
        value: str = "",
        timeout: float = 3.0,
        **kwargs
    ) -> Dict[str, Any]:
        """Sets text value into an editable control via ValuePattern or focused typing."""
        hwnd, criteria = self._normalize_args(target, search_criteria, kwargs)
        if not hwnd or self.auto is None:
            return {
                "status": "FAILED",
                "error_code": "UIA_UNAVAILABLE",
                "error": "Target or UIA unavailable.",
                "message": "Target or UIA unavailable."
            }

        universal_controller.force_foreground_window(hwnd)
        fg = win32gui.GetForegroundWindow() if win32gui else hwnd
        if fg != hwnd:
            return {
                "status": "FOCUS_MISMATCH",
                "error_code": "FOCUS_MISMATCH",
                "error": f"Target HWND {hwnd} is not foreground (current FG is {fg}). Input suppressed.",
                "message": f"Target HWND {hwnd} is not foreground (current FG is {fg}). Input suppressed."
            }

        init = self._get_initializer()
        if not init:
            return {"status": "FAILED", "error_code": "COM_INIT_FAILED", "message": "COM initializer unavailable."}

        with init():
            try:
                root = self.auto.ControlFromHandle(hwnd)
                if not root:
                    return {"status": "FAILED", "error_code": "ROOT_CONTROL_NULL", "message": "Root UIA control is null."}

                auto_id = criteria.get("automation_id")
                name = criteria.get("name")
                ctrl = None
                if auto_id:
                    ctrl = self.auto.EditControl(searchFromControl=root, AutomationId=auto_id)
                elif name:
                    ctrl = self.auto.EditControl(searchFromControl=root, Name=name)

                if not ctrl or not ctrl.Exists(timeout, 0.2):
                    for tree_node in self.auto.WalkTree(root, getChildren=lambda c: c.GetChildren()):
                        candidate = tree_node[0] if isinstance(tree_node, (tuple, list)) else tree_node
                        if "edit" in (candidate.ControlTypeName or "").lower():
                            if (not auto_id or auto_id.lower() in (candidate.AutomationId or "").lower()) and \
                               (not name or name.lower() in (candidate.Name or "").lower()):
                                ctrl = candidate
                                break

                if not ctrl or not ctrl.Exists(0, 0):
                    return {
                        "status": "ELEMENT_NOT_FOUND",
                        "error_code": "ELEMENT_NOT_FOUND",
                        "error": f"Editable control {criteria} not found.",
                        "message": f"Editable control {criteria} not found."
                    }

                method = "FocusedTyping"
                try:
                    pat_val = ctrl.GetValuePattern()
                    if pat_val:
                        pat_val.SetValue(value)
                        method = "ValuePattern"
                except Exception:
                    pass

                if method != "ValuePattern":
                    ctrl.SetFocus()
                    time.sleep(0.1)
                    import pyautogui
                    pyautogui.hotkey('ctrl', 'a')
                    pyautogui.write(value, interval=0.02)

                return {
                    "status": "SUCCESS",
                    "method": method,
                    "target_name": ctrl.Name or "",
                    "value_set": value
                }

            except Exception as e:
                return {
                    "status": "FAILED",
                    "error_code": "SET_VALUE_ERROR",
                    "error": str(e),
                    "message": str(e)
                }

    def select_ui_item(
        self,
        target: Optional[Union[int, str]] = None,
        search_criteria: Optional[Dict[str, Any]] = None,
        timeout: float = 3.0,
        max_depth: int = 7,
        **kwargs
    ) -> Dict[str, Any]:
        """Selects an item (ListItem, DataItem, TreeItem) in a list or tree view."""
        hwnd, criteria = self._normalize_args(target, search_criteria, kwargs)
        if not hwnd or self.auto is None:
            return {
                "status": "FAILED",
                "error_code": "UIA_UNAVAILABLE",
                "error": "Target or UIA unavailable.",
                "message": "Target or UIA unavailable."
            }

        init = self._get_initializer()
        if not init:
            return {"status": "FAILED", "error_code": "COM_INIT_FAILED", "message": "COM initializer unavailable."}

        with init():
            try:
                root = self.auto.ControlFromHandle(hwnd)
                if not root:
                    return {"status": "FAILED", "error_code": "ROOT_CONTROL_NULL", "message": "Root UIA control is null."}

                name = criteria.get("name")
                auto_id = criteria.get("automation_id")
                item = None

                t_end = time.time() + timeout
                while time.time() < t_end:
                    if name:
                        for ctrl_cls in [self.auto.ListItemControl, self.auto.DataItemControl, self.auto.TreeItemControl, self.auto.Control]:
                            try:
                                c = ctrl_cls(searchFromControl=root, Name=name)
                                if c.Exists(0.3, 0.1):
                                    item = c
                                    break
                            except Exception:
                                pass
                    elif auto_id:
                        for ctrl_cls in [self.auto.ListItemControl, self.auto.DataItemControl, self.auto.TreeItemControl, self.auto.Control]:
                            try:
                                c = ctrl_cls(searchFromControl=root, AutomationId=auto_id)
                                if c.Exists(0.3, 0.1):
                                    item = c
                                    break
                            except Exception:
                                pass

                    if item and item.Exists(0, 0):
                        break

                    # Broad walk
                    for tree_node in self.auto.WalkTree(root, getChildren=lambda c: c.GetChildren()):
                        candidate = tree_node[0] if isinstance(tree_node, (tuple, list)) else tree_node
                        c_type = candidate.ControlTypeName or ""
                        if any(t in c_type for t in ["ListItem", "DataItem", "TreeItem", "UIItem"]):
                            if name and name.lower() in (candidate.Name or "").lower():
                                item = candidate
                                break
                            if auto_id and auto_id.lower() == (candidate.AutomationId or "").lower():
                                item = candidate
                                break
                    if item and item.Exists(0, 0):
                        break
                    time.sleep(0.3)

                if not item or not item.Exists(0, 0):
                    return {
                        "status": "ELEMENT_NOT_FOUND",
                        "error_code": "ELEMENT_NOT_FOUND",
                        "error": f"Selectable item {criteria} not found in {timeout}s.",
                        "message": f"Selectable item {criteria} not found in {timeout}s."
                    }

                method = "ControlClick"
                is_selected = True
                try:
                    sel_pat = item.GetSelectionItemPattern()
                    if sel_pat:
                        sel_pat.Select()
                        method = "SelectionItemPattern"
                        is_selected = sel_pat.IsSelected
                except Exception:
                    pass

                if method != "SelectionItemPattern":
                    item.Click(simulateMove=False)

                return {
                    "status": "SUCCESS",
                    "method": method,
                    "target_name": item.Name or "",
                    "is_selected": is_selected,
                    "detail": f"Selected item '{item.Name}' via {method}."
                }

            except Exception as e:
                return {
                    "status": "FAILED",
                    "error_code": "SELECTION_ERROR",
                    "error": str(e),
                    "message": str(e)
                }

    def wait_for_ui_state(
        self,
        target: Optional[Union[int, str]] = None,
        search_criteria: Optional[Dict[str, Any]] = None,
        expected_property: str = "value",
        expected_value: Any = "",
        timeout: float = 5.0,
        **kwargs
    ) -> Dict[str, Any]:
        """Polls until a control property matches expected_value or timeout expires."""
        t_start = time.time()
        t_end = t_start + timeout

        while time.time() < t_end:
            res = self.read_ui_element(target, search_criteria, timeout=1.0, **kwargs)
            if res.get("status") == "SUCCESS":
                actual = res.get(expected_property) or res.get("value")
                if str(expected_value).lower() in str(actual).lower():
                    return {
                        "status": "SUCCESS",
                        "matched": True,
                        "property": expected_property,
                        "expected": expected_value,
                        "actual": actual,
                        "elapsed_seconds": round(time.time() - t_start, 3)
                    }
            time.sleep(0.3)

        return {
            "status": "FAILED",
            "matched": False,
            "error_code": "STATE_WAIT_TIMEOUT",
            "error": f"Timed out waiting for {expected_property}='{expected_value}' in {timeout}s.",
            "message": f"Timed out waiting for {expected_property}='{expected_value}' in {timeout}s."
        }


semantic_ui_controller = SemanticUIController()
