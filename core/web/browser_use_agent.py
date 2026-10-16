"""
A.E.G.I.S. Next-Gen Agentic Browser Engine (Powered by Browser-Use + CDP AXTree).
Replaces legacy Playwright DOM scraping with autonomous VLM-driven web execution:
1. Browser-Use Autonomous Agent (drives browser with VLM vision + AXTree).
2. Chrome DevTools Protocol (CDP) direct session for structured observation.
3. Multi-step workflows with explicit human handoff for authentication and
   security challenges. This engine never bypasses CAPTCHA, MFA, or browser
   security controls.
"""

import os
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("browser_use_agent")

class AegisBrowserUseEngine:
    """
    Unified Browser-Use autonomous agent engine for Aegis.
    Combines Browser-Use with local Ollama / Cloud LLM backends and direct CDP control.
    """

    def __init__(self):
        self._browser = None

    def _get_llm(self):
        """Resolves active LLM — sovereign local first, cloud only when explicitly enabled."""
        sovereign = os.getenv("AEGIS_LOCAL_FIRST", "true").lower() == "true"
        model_name = os.getenv("OLLAMA_MODEL", "aegis-sovereign")

        # SOVEREIGN PATH: always use local Ollama — never reads cloud keys
        if sovereign:
            try:
                from langchain_ollama import ChatOllama
                logger.info(f"[BROWSER-USE] Using sovereign model: {model_name}")
                return ChatOllama(model=model_name)
            except Exception as e:
                logger.warning(f"[BROWSER-USE] langchain_ollama unavailable: {e}")
            return None

        # CLOUD PATH: only when AEGIS_LOCAL_FIRST=false
        try:
            groq_key = os.getenv("GROQ_API_KEY")
            if groq_key:
                from langchain_groq import ChatGroq
                return ChatGroq(model_name="llama-3.3-70b-versatile", api_key=groq_key)
        except Exception:
            pass
        try:
            gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if gemini_key:
                from langchain_google_genai import ChatGoogleGenerativeAI
                return ChatGoogleGenerativeAI(model="gemini-1.5-flash", google_api_key=gemini_key)
        except Exception:
            pass
        try:
            from langchain_ollama import ChatOllama
            return ChatOllama(model=model_name)
        except Exception:
            pass
        return None

    async def execute_task(self, task: str, max_steps: int = 15, headless: bool = False) -> Dict[str, Any]:
        """
        Executes a complex web task autonomously using Browser-Use.
        """
        logger.info(f"[BROWSER-USE] Initiating autonomous web execution for task: '{task}' (headless={headless})")
        
        try:
            from browser_use import Agent, Browser
            from browser_use.browser.browser import BrowserConfig

            browser_config = BrowserConfig(
                headless=headless,
                # Never disable browser security. Login, MFA, CAPTCHA, and
                # anti-bot challenges must remain visible for the operator.
                disable_security=False,
                extra_chromium_args=[
                    "--start-maximized",
                    "--autoplay-policy=no-user-gesture-required"
                ]
            )
            browser = Browser(config=browser_config)

            llm = self._get_llm()
            if not llm:
                logger.warning("[BROWSER-USE] LangChain LLM wrapper not found, falling back to CDP AXTree direct execution.")
                from core.web_agent import aegis_web_agent
                await aegis_web_agent.initialize(headless=headless)
                return await aegis_web_agent.execute_with_axtree(task)

            agent = Agent(
                task=task,
                llm=llm,
                browser=browser,
                max_actions_per_step=3
            )

            history = await agent.run(max_steps=max_steps)
            result_text = history.final_result() if hasattr(history, "final_result") else str(history)

            logger.info(f"[BROWSER-USE] Execution completed successfully: {result_text[:200]}")
            return {
                "status": "SUCCESS",
                "task": task,
                "result": result_text,
                "engine": "browser-use"
            }

        except ImportError as e:
            logger.warning(f"[BROWSER-USE] Browser-Use dependency unavailable: {e}. Falling back to AXTree engine.")
            from core.web_agent import aegis_web_agent
            await aegis_web_agent.initialize(headless=headless)
            return await aegis_web_agent.execute_with_axtree(task)
        except Exception as e:
            logger.error(f"[BROWSER-USE] Execution failed without fallback: {e}")
            return {
                "status": "FAILED",
                "task": task,
                "error": str(e),
                "error_type": type(e).__name__,
                "engine": "browser-use",
            }

browser_use_engine = AegisBrowserUseEngine()
