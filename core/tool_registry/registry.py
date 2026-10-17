"""
AEGIS Unified Tool Registry
============================
Single source of truth for callable tools in the AEGIS ecosystem.
Exposes stable tool IDs, validated Pydantic schemas, risk boundaries,
timeouts, cancellation handling, and structured results.
"""

import asyncio
import time
import inspect
from typing import Dict, Any, Optional, Type, Callable, List
from dataclasses import dataclass, field
import pydantic
from pydantic import BaseModel, Field

from core.tool_registry.base_tool import BaseTool
from core.orchestration.agent_contract import StructuredError, ErrorCode


class EmptySchema(BaseModel):
    pass


@dataclass
class ToolDefinition:
    tool_id: str
    description: str
    input_schema: Type[BaseModel]
    handler: Callable
    risk_level: str = "SAFE"  # SAFE, MODERATE, DANGEROUS
    timeout_seconds: int = 30
    capability: str = "general"
    verification_strategy: str = "output_check"
    requires_approval: bool = False

    def to_dict(self) -> Dict[str, Any]:
        schema_dict = {}
        if hasattr(self.input_schema, "model_json_schema"):
            schema_dict = self.input_schema.model_json_schema()
        elif hasattr(self.input_schema, "schema"):
            schema_dict = self.input_schema.schema()

        return {
            "tool_id": self.tool_id,
            "description": self.description,
            "input_schema": schema_dict,
            "risk_level": self.risk_level,
            "timeout_seconds": self.timeout_seconds,
            "capability": self.capability,
            "verification_strategy": self.verification_strategy,
            "requires_approval": self.requires_approval,
        }


@dataclass
class ToolResult:
    tool_id: str
    status: str  # "SUCCESS", "FAILED", "TIMEOUT", "PERMISSION_REQUIRED", "VALIDATION_ERROR"
    data: Any = None
    error: Optional[StructuredError] = None
    duration_ms: float = 0.0
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status == "SUCCESS"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_id": self.tool_id,
            "status": self.status,
            "data": self.data,
            "error": self.error.to_dict() if self.error else None,
            "duration_ms": self.duration_ms,
            "evidence": self.evidence,
        }


class ToolRegistry:
    """Canonical Tool Registry across the AEGIS multi-agent platform."""

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._load_core_tools()

    def register(self, tool_def: ToolDefinition):
        self._tools[tool_def.tool_id.lower()] = tool_def

    def register_base_tool(self, tool_instance: BaseTool):
        t_id = tool_instance.name.lower()
        self._tools[t_id] = ToolDefinition(
            tool_id=t_id,
            description=tool_instance.description,
            input_schema=tool_instance.input_schema,
            handler=tool_instance.execute,
            risk_level=tool_instance.risk_level,
            timeout_seconds=tool_instance.timeout_seconds,
            capability=t_id.split(".")[0] if "." in t_id else "general",
        )

    def get_tool(self, tool_id: str) -> Optional[ToolDefinition]:
        return self._tools.get(tool_id.lower())

    def list_tools(self) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in self._tools.values()]

    def has_tool(self, tool_id: str) -> bool:
        return tool_id.lower() in self._tools

    async def execute(
        self,
        tool_id: str,
        arguments: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
        bypass_approval: bool = False,
    ) -> ToolResult:
        """Executes a tool with input schema validation, timeouts, and error classification."""
        start_time = time.time()
        tid = tool_id.lower()
        tool = self.get_tool(tid)

        if not tool:
            duration_ms = (time.time() - start_time) * 1000
            return ToolResult(
                tool_id=tool_id,
                status="FAILED",
                error=StructuredError(
                    error_code=ErrorCode.TOOL_UNAVAILABLE.value,
                    message=f"Tool '{tool_id}' not found in ToolRegistry.",
                    details={"available_tools": list(self._tools.keys())},
                ),
                duration_ms=duration_ms,
            )

        # 1. Approval Gate
        if tool.risk_level == "DANGEROUS" and not bypass_approval:
            duration_ms = (time.time() - start_time) * 1000
            return ToolResult(
                tool_id=tool_id,
                status="PERMISSION_REQUIRED",
                error=StructuredError(
                    error_code=ErrorCode.ACTION_REJECTED.value,
                    message=f"Tool '{tool_id}' has DANGEROUS risk level and requires operator approval.",
                    details={"risk_level": tool.risk_level, "arguments": arguments},
                    recoverable=True,
                ),
                duration_ms=duration_ms,
            )

        # 2. Schema Validation
        validated_kwargs = arguments
        if tool.input_schema and issubclass(tool.input_schema, BaseModel):
            try:
                validated_model = tool.input_schema(**arguments)
                if hasattr(validated_model, "model_dump"):
                    validated_kwargs = validated_model.model_dump()
                elif hasattr(validated_model, "dict"):
                    validated_kwargs = validated_model.dict()
            except pydantic.ValidationError as ve:
                duration_ms = (time.time() - start_time) * 1000
                return ToolResult(
                    tool_id=tool_id,
                    status="VALIDATION_ERROR",
                    error=StructuredError(
                        error_code=ErrorCode.PRECONDITION_FAILED.value,
                        message=f"Input arguments for tool '{tool_id}' failed schema validation.",
                        details={"validation_errors": ve.errors(), "raw_args": arguments},
                        recoverable=False,
                    ),
                    duration_ms=duration_ms,
                )

        # 3. Execution under timeout
        exec_timeout = timeout or tool.timeout_seconds
        try:
            handler = tool.handler
            if inspect.iscoroutinefunction(handler):
                result_data = await asyncio.wait_for(handler(**validated_kwargs), timeout=exec_timeout)
            else:
                # Synchronous handler: run in thread pool
                loop = asyncio.get_running_loop()
                result_data = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: handler(**validated_kwargs)),
                    timeout=exec_timeout,
                )

            duration_ms = (time.time() - start_time) * 1000
            final_status = "SUCCESS"
            if isinstance(result_data, dict) and str(result_data.get("status", "")).upper() in {
                "FAILED", "ERROR", "BLOCKED", "UNAVAILABLE", "UNVERIFIED",
                "VERIFICATION_FAILED", "TIMEOUT", "CANCELLED", "SAFETY_BLOCK",
            }:
                final_status = result_data["status"]
            evidence = {"validated_args": list(validated_kwargs.keys())}
            return ToolResult(
                tool_id=tool_id,
                status=final_status,
                data=result_data,
                duration_ms=duration_ms,
                evidence=evidence,
            )

        except asyncio.TimeoutError:
            duration_ms = (time.time() - start_time) * 1000
            return ToolResult(
                tool_id=tool_id,
                status="TIMEOUT",
                error=StructuredError(
                    error_code=ErrorCode.TIMEOUT.value,
                    message=f"Tool '{tool_id}' execution timed out after {exec_timeout}s.",
                    details={"timeout_seconds": exec_timeout},
                    recoverable=True,
                ),
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            return ToolResult(
                tool_id=tool_id,
                status="FAILED",
                error=StructuredError(
                    error_code=ErrorCode.EXECUTION_FAILED.value,
                    message=f"Tool '{tool_id}' raised exception: {str(e)}",
                    details={"exception_type": type(e).__name__},
                    recoverable=True,
                ),
                duration_ms=duration_ms,
            )

    def _load_core_tools(self):
        """Loads canonical fleet tools into the registry."""
        try:
            from core.tools.filesystem_tools import ReadFileTool, WriteFileTool
            self.register_base_tool(ReadFileTool())
            self.register_base_tool(WriteFileTool())
        except Exception:
            pass

        try:
            from core.orchestration import agent_tools

            class RunCommandSchema(BaseModel):
                command: str = Field(..., description="The shell command to run")
                cwd: str = Field(".", description="Working directory")

            self.register(ToolDefinition(
                tool_id="terminal.run",
                description="Run terminal command",
                input_schema=RunCommandSchema,
                handler=agent_tools.run_bash_command,
                risk_level="MODERATE",
                timeout_seconds=30,
                capability="terminal",
            ))

            class GrepSearchSchema(BaseModel):
                pattern: str = Field(..., description="Pattern to search for")
                search_path: str = Field(".", description="Path to search within")

            self.register(ToolDefinition(
                tool_id="filesystem.grep",
                description="Search for pattern in files",
                input_schema=GrepSearchSchema,
                handler=agent_tools.grep_search,
                risk_level="SAFE",
                timeout_seconds=20,
                capability="filesystem",
            ))

            class WebSearchSchema(BaseModel):
                url: str = Field("", description="Search query")
                query: str = Field("", description="Search query alias")
                max_results: int = Field(5, description="Maximum results to return")

            self.register(ToolDefinition(
                tool_id="web.search",
                description="Search the web",
                input_schema=WebSearchSchema,
                handler=agent_tools.web_search,
                risk_level="SAFE",
                timeout_seconds=15,
                capability="web",
            ))

            class LaunchAppSchema(BaseModel):
                app_name: str = Field(..., description="Application name to launch")

            self.register(ToolDefinition(
                tool_id="desktop.launch_app",
                description="Launch a native desktop application",
                input_schema=LaunchAppSchema,
                handler=agent_tools.launch_desktop_app,
                risk_level="SAFE",
                timeout_seconds=10,
                capability="desktop",
            ))

            class InteractDesktopSchema(BaseModel):
                action: str = Field(..., description="Interaction action (click, type, press, etc)")
                target: str = Field("", description="Target window or element name")
                text: str = Field("", description="Text to type")
                key: str = Field("", description="Key or shortcut to press")
                x: int = Field(0, description="X coordinate")
                y: int = Field(0, description="Y coordinate")

            self.register(ToolDefinition(
                tool_id="desktop.interact",
                description="Perform desktop interaction (mouse/keyboard)",
                input_schema=InteractDesktopSchema,
                handler=agent_tools.interact_desktop,
                risk_level="MODERATE",
                timeout_seconds=15,
                capability="desktop",
            ))

            class InspectScreenSchema(BaseModel):
                focus_target: str = Field("", description="Optional window or app to inspect")
            class UniversalDesktopGoalSchema(BaseModel):
                goal: str = Field(..., min_length=1, max_length=4000, description="Concrete desktop or application GUI goal")
                surface: Optional[str] = Field(None, description="Optional execution surface: desktop or browser")

            async def execute_universal_desktop_goal(goal: str, surface: Optional[str] = None):
                from core.execution.universal_dispatch import execute_universal_goal

                return await execute_universal_goal(goal, surface=surface)

            self.register(ToolDefinition(
                tool_id="desktop.universal_goal",
                description=(
                    "Execute a desktop or application goal through the universal "
                    "observe-plan-act-verify runtime with fresh observations."
                ),
                input_schema=UniversalDesktopGoalSchema,
                handler=execute_universal_desktop_goal,
                risk_level="MODERATE",
                timeout_seconds=120,
                capability="desktop",
                verification_strategy="fresh_observation_and_worker_verification",
            ))


            self.register(ToolDefinition(
                tool_id="screen.inspect",
                description="Capture and inspect current desktop screen",
                input_schema=InspectScreenSchema,
                handler=agent_tools.inspect_screen,
                risk_level="SAFE",
                timeout_seconds=10,
                capability="perception",
            ))

            class RunTestsSchema(BaseModel):
                target: str = Field("tests", description="Test directory or file path")

            self.register(ToolDefinition(
                tool_id="tests.run",
                description="Run automated test suite",
                input_schema=RunTestsSchema,
                handler=agent_tools.run_tests,
                risk_level="SAFE",
                timeout_seconds=60,
                capability="verification",
            ))

            # Phase 3.2: Semantic Windows UI Automation Tools
            from core.execution.semantic_ui_controller import semantic_ui_controller
            from core.execution.adaptive_execution_router import adaptive_execution_router

            class InspectWindowSchema(BaseModel):
                target: str = Field(..., description="Target HWND or window title substring")

            self.register(ToolDefinition(
                tool_id="desktop.inspect_window",
                description="Inspect window metadata, title, class, and bounds",
                input_schema=InspectWindowSchema,
                handler=lambda target: semantic_ui_controller.inspect_window(int(target) if str(target).isdigit() else target),
                risk_level="SAFE",
                timeout_seconds=10,
                capability="desktop",
            ))

            class GetUITreeSchema(BaseModel):
                target: str = Field(..., description="Target HWND or window title substring")
                max_depth: int = Field(3, description="Maximum tree traversal depth")
                max_elements: int = Field(50, description="Maximum elements to retrieve")

            self.register(ToolDefinition(
                tool_id="desktop.get_ui_tree",
                description="Retrieve structured UI Automation element tree of window",
                input_schema=GetUITreeSchema,
                handler=lambda target, max_depth=3, max_elements=50: semantic_ui_controller.get_ui_tree(
                    int(target) if str(target).isdigit() else target, max_depth=max_depth, max_elements=max_elements
                ),
                risk_level="SAFE",
                timeout_seconds=15,
                capability="desktop",
            ))

            class FindUIElementSchema(BaseModel):
                target: str = Field(..., description="Target HWND or window title substring")
                automation_id: Optional[str] = Field(None, description="AutomationId of element")
                name: Optional[str] = Field(None, description="Name/Label of element")
                control_type: Optional[str] = Field(None, description="ControlType (e.g. ButtonControl, EditControl)")

            self.register(ToolDefinition(
                tool_id="desktop.find_ui_element",
                description="Locate element matching criteria inside window",
                input_schema=FindUIElementSchema,
                handler=lambda target, automation_id=None, name=None, control_type=None: semantic_ui_controller.find_ui_element(
                    int(target) if str(target).isdigit() else target,
                    {"automation_id": automation_id, "name": name, "control_type": control_type}
                ),
                risk_level="SAFE",
                timeout_seconds=10,
                capability="desktop",
            ))

            class InvokeUIElementSchema(BaseModel):
                target: str = Field(..., description="Target HWND or window title substring")
                automation_id: Optional[str] = Field(None, description="AutomationId to invoke")
                name: Optional[str] = Field(None, description="Name/Label to invoke")
                control_type: Optional[str] = Field("ButtonControl", description="ControlType")

            self.register(ToolDefinition(
                tool_id="desktop.invoke_ui_element",
                description="Invoke button or control via UIA pattern or click",
                input_schema=InvokeUIElementSchema,
                handler=lambda target, automation_id=None, name=None, control_type="ButtonControl": semantic_ui_controller.invoke_ui_element(
                    int(target) if str(target).isdigit() else target,
                    {"automation_id": automation_id, "name": name, "control_type": control_type}
                ),
                risk_level="MODERATE",
                timeout_seconds=10,
                capability="desktop",
            ))

            class ReadUIElementSchema(BaseModel):
                target: str = Field(..., description="Target HWND or window title substring")
                automation_id: Optional[str] = Field(None, description="AutomationId to read")
                name: Optional[str] = Field(None, description="Name/Label to read")
                control_type: Optional[str] = Field(None, description="ControlType")

            self.register(ToolDefinition(
                tool_id="desktop.read_ui_element",
                description="Read value or text from UI element",
                input_schema=ReadUIElementSchema,
                handler=lambda target, automation_id=None, name=None, control_type=None: semantic_ui_controller.read_ui_element(
                    int(target) if str(target).isdigit() else target,
                    {"automation_id": automation_id, "name": name, "control_type": control_type}
                ),
                risk_level="SAFE",
                timeout_seconds=10,
                capability="desktop",
            ))

            class SetUIValueSchema(BaseModel):
                target: str = Field(..., description="Target HWND or window title substring")
                value: str = Field(..., description="Text value to enter")
                automation_id: Optional[str] = Field(None, description="AutomationId of edit control")
                name: Optional[str] = Field(None, description="Name of edit control")

            self.register(ToolDefinition(
                tool_id="desktop.set_ui_value",
                description="Set text value into editable control via UIA ValuePattern",
                input_schema=SetUIValueSchema,
                handler=lambda target, value, automation_id=None, name=None: semantic_ui_controller.set_ui_value(
                    int(target) if str(target).isdigit() else target,
                    {"automation_id": automation_id, "name": name},
                    value=value
                ),
                risk_level="MODERATE",
                timeout_seconds=10,
                capability="desktop",
            ))

            class SelectUIItemSchema(BaseModel):
                target: str = Field(..., description="Target HWND or window title substring")
                name: Optional[str] = Field(None, description="Name of item to select")
                automation_id: Optional[str] = Field(None, description="AutomationId of item")

            self.register(ToolDefinition(
                tool_id="desktop.select_ui_item",
                description="Select item in list or tree via UIA SelectionItemPattern",
                input_schema=SelectUIItemSchema,
                handler=lambda target, name=None, automation_id=None: semantic_ui_controller.select_ui_item(
                    int(target) if str(target).isdigit() else target,
                    {"name": name, "automation_id": automation_id}
                ),
                risk_level="MODERATE",
                timeout_seconds=10,
                capability="desktop",
            ))

            class AdaptiveExecuteSchema(BaseModel):
                task_description: str = Field(..., description="Task intent description")
                target_app: str = Field(..., description="Application name or executable or URL")
                action: str = Field("invoke", description="Action to perform (invoke, read, set_value, select, run)")
                params: Dict[str, Any] = Field(default_factory=dict, description="Action parameters")

            self.register(ToolDefinition(
                tool_id="desktop.adaptive_execute",
                description="Adaptively routes action across Native API, Semantic UIA, Visual, or Browser",
                input_schema=AdaptiveExecuteSchema,
                handler=adaptive_execution_router.execute_adaptively,
                risk_level="MODERATE",
                timeout_seconds=30,
                capability="orchestration",
            ))

            # Phase 4: Browser Automation and Ticket Booking Tools
            from core.perception.browser_driver import browser_driver
            from core.booking.intent_parser import parse_booking_intent
            from core.ticket_booking_agent import jarvis_ticket_booker

            class BrowserNavigateSchema(BaseModel):
                url: str = Field(..., description="Web URL to navigate to")
                timeout_seconds: int = Field(30, description="Navigation timeout in seconds")

            self.register(ToolDefinition(
                tool_id="browser.navigate",
                description="Navigate active browser session to URL with readiness verification",
                input_schema=BrowserNavigateSchema,
                handler=lambda url, timeout_seconds=30: browser_driver.navigate_to(url, timeout=timeout_seconds * 1000),
                risk_level="SAFE",
                timeout_seconds=35,
                capability="browser",
            ))

            class BrowserClickSchema(BaseModel):
                selector: Optional[str] = Field(None, description="CSS or text selector")
                text: Optional[str] = Field(None, description="Accessible element text")
                role: Optional[str] = Field(None, description="ARIA role (button, link, searchbox)")

            self.register(ToolDefinition(
                tool_id="browser.click",
                description="Click interactive element in browser using CSS or semantic locators",
                input_schema=BrowserClickSchema,
                handler=lambda selector=None, text=None, role=None: (
                    browser_driver.click_semantic(text=text, role=role) if (text or role)
                    else browser_driver.click_element(selector or "")
                ),
                risk_level="MODERATE",
                timeout_seconds=15,
                capability="browser",
            ))

            class BrowserFillSchema(BaseModel):
                text: str = Field(..., description="Text value to enter")
                selector: Optional[str] = Field(None, description="CSS or text selector")
                placeholder: Optional[str] = Field(None, description="Input placeholder text")

            self.register(ToolDefinition(
                tool_id="browser.fill",
                description="Fill text into input element using CSS or placeholder locators",
                input_schema=BrowserFillSchema,
                handler=lambda text, selector=None, placeholder=None: (
                    browser_driver.fill_semantic(placeholder=placeholder, text=text) if placeholder
                    else browser_driver.fill_element(selector or "", text)
                ),
                risk_level="MODERATE",
                timeout_seconds=15,
                capability="browser",
            ))

            class BrowserInspectSchema(BaseModel):
                pass

            self.register(ToolDefinition(
                tool_id="browser.inspect_dom",
                description="Extract interactive accessibility and clickable element boundaries from browser",
                input_schema=BrowserInspectSchema,
                handler=lambda: browser_driver.get_active_elements(),
                risk_level="SAFE",
                timeout_seconds=10,
                capability="browser",
            ))

            class BookingParseIntentSchema(BaseModel):
                query: str = Field(..., description="Natural language booking query")
                default_city: Optional[str] = Field(None, description="Optional default city if not in query")

            self.register(ToolDefinition(
                tool_id="booking.parse_intent",
                description="Parse structured booking intent from natural language",
                input_schema=BookingParseIntentSchema,
                handler=lambda query, default_city=None: parse_booking_intent(query, default_city=default_city).model_dump(),
                risk_level="SAFE",
                timeout_seconds=5,
                capability="booking",
            ))

            class BookingExecuteSchema(BaseModel):
                command: str = Field(..., description="Natural language booking command or prompt")
                site_url: Optional[str] = Field(None, description="Target ticketing URL or mock server")
                auto_authorize: bool = Field(False, description="Whether operator grants immediate final submission authorization")

            self.register(ToolDefinition(
                tool_id="booking.execute",
                description="Execute end-to-end verified ticket booking flow halting safely at confirmation gate",
                input_schema=BookingExecuteSchema,
                handler=lambda command, site_url=None, auto_authorize=False: jarvis_ticket_booker.execute_booking_flow(
                    parse_booking_intent(command),
                    site_url=site_url,
                    auto_authorize=auto_authorize
                ),
                risk_level="MODERATE",
                timeout_seconds=60,
                capability="booking",
            ))

        except Exception:
            pass



tool_registry = ToolRegistry()
