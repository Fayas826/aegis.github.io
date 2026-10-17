from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List

class BaseLLMProvider(ABC):
    """
    JARVIS O.M.E.G.A. Model Gateway Base Interface
    Ensures all models (Local, Groq, Gemini, Mistral) conform to the exact same I/O schema.
    """

    @abstractmethod
    async def chat(self, text: str, system_prompt: str, context: str = "") -> Optional[Dict[str, Any]]:
        """
        Execute a standard chat completion.
        Must return standard JARVIS protocol JSON: {"type": "CHAT", "intent": "...", "response": "..."}
        """
        pass

    @abstractmethod
    async def vision(self, text: str, image_path: str, context: str = "") -> Optional[Dict[str, Any]]:
        """
        Execute a vision analysis completion.
        """
        pass

    @property
    @abstractmethod
    def is_active(self) -> bool:
        """Return True if the provider is configured and reachable."""
        pass
