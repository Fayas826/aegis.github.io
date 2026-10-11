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
            return ToolResult(
                tool_id=tool_id,
                status="SUCCESS",
                data=result_data,
                duration_ms=duration_ms,
                evidence={"validated_args": list(validated_kwargs.keys())},
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

        except Exception:
            pass


tool_registry = ToolRegistry()
