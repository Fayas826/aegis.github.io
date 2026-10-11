from abc import ABC, abstractmethod
from typing import Dict, Any, Type, Optional
import pydantic

class BaseTool(ABC):
    """
    JARVIS O.M.E.G.A. - Base Tool Interface
    Defines a capability that can be dynamically loaded into the Tool Registry.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """The canonical name of the tool (e.g., 'filesystem.read')"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Detailed description of what the tool does and when to use it."""
        pass

    @property
    @abstractmethod
    def input_schema(self) -> Type[pydantic.BaseModel]:
        """Pydantic model defining the expected arguments."""
        pass

    @property
    def risk_level(self) -> str:
        """'SAFE', 'MODERATE', or 'DANGEROUS' (Requires user approval)"""
        return "SAFE"

    @property
    def timeout_seconds(self) -> int:
        """Maximum execution time before forcefully aborting."""
        return 30

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """The actual implementation of the tool's capability."""
        pass
