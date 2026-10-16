"""Optional Gemini Computer Use browser loop.

Google's Computer Use tool currently exposes a browser environment, not a
general Windows desktop environment. This adapter uses it where supported and
keeps AEGIS's native desktop worker for non-browser applications. Every model
action is executed against the live Playwright page and followed by a fresh
observation; model prose is never treated as proof.
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from typing import Any, Callable, Mapping, Optional


IRREVERSIBLE_TERMS = (
    "delete", "remove", "send", "submit", "purchase", "buy", "pay", "transfer", "post", "publish", "change password"
)


def _normalized_pixel(value: Any, size: int) -> int:
    numeric = float(value)
    if not 0 <= numeric <= 1000:
        raise ValueError("computer-use coordinate must be between 0 and 1000")
    return max(0, min(max(0, size - 1), round(numeric * max(0, size - 1) / 1000)))


class GeminiComputerUseBrowserAgent:
    def __init__(self, *, browser_runtime: Any, client: Any = None, model: Optional[str] = None,
                 confirmation: Optional[Callable[[str], bool]] = None, max_steps: int = 20):
        self.browser_runtime = browser_runtime
        self.client = client
        self.model = model or os.getenv("AEGIS_BROWSER_AGENT_MODEL", "gemini-2.5-computer-use-preview-10-2025")
        self.confirmation = confirmation
        self.max_steps = max(1, min(int(max_steps), 20))

    def _client(self) -> Any:
        if self.client is not None:
            return self.client
        key = os.getenv("GEMINI_API_KEY", "").strip()
        if not key or key.upper() in {"YOUR_GEMINI_API_KEY", "CHANGEME"}:
            return None
        try:
            from google import genai
            self.client = genai.Client(api_key=key)
            return self.client
        except Exception:
            return None

    @staticmethod
    def _computer_tool() -> Any:
        from google.genai import types
        return types.Tool(computer_use=types.ComputerUse(environment=types.Environment.ENVIRONMENT_BROWSER))

    async def _observe(self) -> dict[str, Any]:
        return await self.browser_runtime.observe_state()

    async def _screenshot_part(self) -> Any:
        from google.genai import types
        data = await self.browser_runtime.page.screenshot(type="png", full_page=False)
        return types.Part.from_bytes(data=data, mime_type="image/png")

    async def _page_size(self) -> tuple[int, int]:
        try:
            size = await self.browser_runtime.page.evaluate("() => ({width: innerWidth, height: innerHeight})")
            return int(size["width"]), int(size["height"])
        except Exception:
            return 1280, 720

    async def _action(self, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        page = self.browser_runtime.page
        width, height = await self._page_size()
        if name in {"click_at", "type_text_at", "hover_at"}:
            x = _normalized_pixel(args.get("x"), width)
            y = _normalized_pixel(args.get("y"), height)
            await page.mouse.move(x, y)
            if name == "click_at":
                await page.mouse.click(x, y)
            elif name == "hover_at":
                await page.mouse.move(x, y)
            else:
                await page.mouse.click(x, y)
                await page.keyboard.insert_text(str(args.get("text", "")))
                if args.get("press_enter"):
                    await page.keyboard.press("Enter")
        elif name == "scroll_document":
            amount = max(-1200, min(1200, int(args.get("amount", args.get("delta_y", 500)))))
            await page.mouse.wheel(0, amount)
        elif name in {"key", "keypress"}:
            await page.keyboard.press(str(args.get("key", "")))
        elif name == "drag_and_drop":
            sx = _normalized_pixel(args.get("x1", args.get("start_x")), width)
            sy = _normalized_pixel(args.get("y1", args.get("start_y")), height)
            ex = _normalized_pixel(args.get("x2", args.get("end_x")), width)
            ey = _normalized_pixel(args.get("y2", args.get("end_y")), height)
            await page.mouse.move(sx, sy)
            await page.mouse.down()
            await page.mouse.move(ex, ey)
            await page.mouse.up()
        else:
            return {"status": "BLOCKED", "error": f"Unsupported Computer Use action: {name}"}
        return {"status": "ACTION_SUCCEEDED", "action": name}

    async def execute_task(self, goal: str, *, headless: bool = False, max_steps: Optional[int] = None) -> dict[str, Any]:
        del headless  # browser_runtime owns launch mode
        client = self._client()
        if client is None:
            return {"status": "UNAVAILABLE", "verification_status": "UNAVAILABLE", "error": "Gemini client is not configured."}
        if not self.browser_runtime.page or self.browser_runtime.page.is_closed():
            return {"status": "UNAVAILABLE", "verification_status": "UNAVAILABLE", "error": "Browser page is not active."}
        if any(term in goal.casefold() for term in IRREVERSIBLE_TERMS):
            if not self.confirmation or not self.confirmation(goal):
                return {"status": "BLOCKED", "verification_status": "BLOCKED", "error": "Operator confirmation is required for this browser goal."}

        from google.genai import types

        observation = await self._observe()
        contents = [types.Content(role="user", parts=[types.Part.from_text(text=f"GOAL: {goal}\nOBSERVATION: {observation}"), await self._screenshot_part()])]
        config = types.GenerateContentConfig(
            tools=[self._computer_tool()],
            system_instruction="Use one browser Computer Use action at a time. After each action inspect the fresh screenshot. Never claim success from action status alone.",
            temperature=0.1,
        )
        actions = []
        limit = max_steps or self.max_steps
        for step in range(1, max(1, min(int(limit), self.max_steps)) + 1):
            try:
                response = client.models.generate_content(model=self.model, contents=contents, config=config)
            except Exception as exc:
                return {"status": "UNVERIFIED", "verification_status": "UNVERIFIED", "error": str(exc), "observation": observation, "actions": actions}
            candidate = response.candidates[0] if getattr(response, "candidates", None) else None
            if candidate is None or getattr(candidate, "content", None) is None:
                return {"status": "UNVERIFIED", "verification_status": "UNVERIFIED", "error": "No model response content.", "observation": observation, "actions": actions}
            contents.append(candidate.content)
            calls = [part.function_call for part in getattr(candidate.content, "parts", ()) if getattr(part, "function_call", None) is not None]
            if not calls:
                return {"status": "UNVERIFIED", "verification_status": "UNVERIFIED", "detail": "Model stopped; no deterministic goal predicate was supplied.", "observation": observation, "actions": actions}
            responses = []
            for call in calls:
                name = str(getattr(call, "name", ""))
                result = await self._action(name, getattr(call, "args", {}) or {})
                observation = await self._observe()
                actions.append({"step": step, "action": name, "result": result, "observation": observation})
                responses.append(types.Part(function_response=types.FunctionResponse(name=name, response={"result": result, "observation": observation})))
                responses.append(await self._screenshot_part())
                if result.get("status") == "BLOCKED":
                    return {"status": "BLOCKED", "verification_status": "BLOCKED", "actions": actions, "observation": observation}
            contents.append(types.Content(role="user", parts=responses))
        return {"status": "UNVERIFIED", "verification_status": "UNVERIFIED", "detail": "Computer Use step budget exhausted.", "actions": actions, "observation": observation}


__all__ = ["GeminiComputerUseBrowserAgent"]
