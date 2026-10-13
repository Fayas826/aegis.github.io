"""
A.E.G.I.S. Universal Desktop Command Router
=============================================
Professional-grade intent classifier that routes ANY user command to the
correct execution pipeline: Desktop Actions, Browser Actions, Computer Use
(vision-based autonomous), or plain Chat.

This is the critical missing bridge between voice/chat input and the
computer_use_agent pipeline. Without this, desktop commands fall through
to the chat LLM and get conversational responses instead of actions.
"""

import re
import os
import logging
from typing import Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger("AEGIS.DesktopCommandRouter")


@dataclass
class CommandClassification:
    """Result of classifying a user command."""
    category: str  # DESKTOP_ACTION, BROWSER_ACTION, COMPUTER_USE, CHAT
    confidence: float  # 0.0 - 1.0
    action_type: str  # e.g. OPEN_APP, NAVIGATE_URL, MULTI_STEP_TASK, etc.
    target: str  # The primary target (app name, URL, task description)
    parameters: Dict[str, Any] = field(default_factory=dict)
    requires_vision: bool = False
    is_multi_step: bool = False
    continuation_of_session: bool = False


# ─── Pattern Definitions ────────────────────────────────────────────────────

# Apps that should be opened natively (not in browser)
_NATIVE_APPS = {
    "notepad", "calculator", "calc", "paint", "terminal", "cmd", "powershell",
    "explorer", "file explorer", "files", "task manager", "settings",
    "vscode", "vs code", "code", "cursor", "word", "excel", "powerpoint",
    "camera", "photos", "store", "control panel", "snipping tool",
    "spotify", "discord", "slack", "teams", "zoom", "obs", "vlc",
    "steam", "epic games", "blender", "photoshop", "figma",
}

# Web destinations that should open in browser
_WEB_DESTINATIONS = {
    "youtube", "google", "gmail", "reddit", "github", "chatgpt",
    "whatsapp", "twitter", "x", "netflix", "amazon", "wikipedia",
    "twitch", "linkedin", "instagram", "facebook", "huggingface",
    "stackoverflow", "maps", "google maps", "spotify web",
    "bookmyshow", "flipkart", "swiggy", "zomato", "makemytrip",
    "uber", "ola", "paytm", "phonepe", "gpay",
}

# Keywords indicating multi-step autonomous tasks (need vision agent)
_AUTONOMOUS_TASK_KEYWORDS = [
    "book ticket", "book tickets", "book movie", "book flight",
    "order food", "order from", "buy", "purchase", "checkout",
    "fill form", "fill out", "submit form", "sign up", "sign in", "log in",
    "send message", "send email", "compose email", "write email",
    "download", "upload", "install", "uninstall",
    "search and click", "find and select", "navigate to and",
    "set city", "select city", "choose", "select seats", "pick seats",
]

_AUTONOMOUS_TASK_PATTERNS = [
    r"\bbook\s+(?:\d+\s+)?(?:movie|film\s+)?(?:ticket|tickets|seat|seats|flight|hotel|table|room|show)",
    r"\b(?:order|buy|purchase|checkout)\s+(?:from\s+)?(.+)",
    r"\b(?:fill\s+(?:out\s+)?|submit\s+)(?:the\s+)?form",
    r"\b(?:sign|log)\s*(?:in|up)\b",
    r"\b(?:select|pick|choose)\s+(?:the\s+)?(?:seats?|city|theatre|cinema|showtime)",
    r"\bsearch\s+(?:for\s+)?(.+?)\s+and\s+(?:click|select|choose|book|buy|open)",
]

# Patterns for browser navigation
_BROWSER_NAV_PATTERNS = [
    r"(?:open|go to|visit)\s+(?:youtube|spotify)\s+(?:and\s+)?(?:play|watch|listen to)\s+(.+)",
    r"(?:play|watch|listen to)\s+(.+?)(?:\s+on\s+(?:youtube|spotify))?$",
    r"(?:open|go to|navigate to|visit|load)\s+(?:the\s+)?(?:website|site|page|url)?\s*(?:for\s+)?(.+\.(?:com|org|net|in|io|co|ai|dev|edu|gov))",
    r"(?:open|go to|navigate to|visit)\s+(?:chrome|browser|edge)\s+(?:and\s+)?(?:go to|open|navigate to|visit)\s+(.+)",
    r"(?:open|go to|visit)\s+(https?://\S+)",
    r"(?:search|google|look up|find)\s+(?:for\s+)?(.+?)(?:\s+on\s+(?:google|youtube|amazon|reddit))?$",
]

# Desktop action patterns
_DESKTOP_ACTION_PATTERNS = [
    (r"^(?:open|launch|start|run)\s+(.+)$", "OPEN_APP"),
    (r"^(?:close|exit|quit|kill)\s+(.+)$", "CLOSE_APP"),
    (r"^(?:minimize|maximise|maximize|restore)\s+(.+)$", "WINDOW_CONTROL"),
    (r"^(?:switch to|alt tab|go to window)\s*(.*)$", "SWITCH_WINDOW"),
    (r"^(?:take|capture)\s+(?:a\s+)?screenshot", "SCREENSHOT"),
    (r"^(?:type|write|input|enter)\s+(.+)$", "TYPE_TEXT"),
    (r"^(?:click|press|tap|hit)\s+(.+)$", "CLICK_TARGET"),
    (r"^(?:scroll)\s+(?:down|up)(?:\s+(.*))?$", "SCROLL"),
    (r"^(?:copy|paste|cut|undo|redo|select all)$", "KEYBOARD_SHORTCUT"),
]


class DesktopCommandRouter:
    """
    Classifies user commands into execution categories with high accuracy.
    Uses a tiered approach: exact patterns → keyword matching → context analysis.
    """

    def __init__(self):
        self._compiled_browser = [re.compile(p, re.IGNORECASE) for p in _BROWSER_NAV_PATTERNS]
        self._compiled_desktop = [(re.compile(p, re.IGNORECASE), action) for p, action in _DESKTOP_ACTION_PATTERNS]
        self._compiled_autonomous = [re.compile(p, re.IGNORECASE) for p in _AUTONOMOUS_TASK_PATTERNS]

    def classify(self, message: str, session_context: Optional[Dict] = None) -> CommandClassification:
        """
        Classify a user message into the appropriate execution pipeline.
        
        Priority:
        1. Continuation of active computer-use session
        2. Multi-step autonomous task (needs vision agent)
        3. Browser navigation
        4. Desktop app control
        5. Default to chat
        """
        clean = message.strip()
        lower = clean.lower()

        # ── 0. Session Continuation ──
        if session_context and session_context.get("active_session"):
            return CommandClassification(
                category="COMPUTER_USE",
                confidence=0.95,
                action_type="SESSION_CONTINUE",
                target=clean,
                parameters={"session_id": session_context.get("session_id")},
                requires_vision=True,
                is_multi_step=True,
                continuation_of_session=True,
            )

        # ── 1. Multi-step Autonomous Task Detection (Keywords & Regex) ──
        for keyword in _AUTONOMOUS_TASK_KEYWORDS:
            if keyword in lower:
                return CommandClassification(
                    category="COMPUTER_USE",
                    confidence=0.92,
                    action_type="MULTI_STEP_TASK",
                    target=clean,
                    requires_vision=True,
                    is_multi_step=True,
                )

        for pat in self._compiled_autonomous:
            if pat.search(lower):
                return CommandClassification(
                    category="COMPUTER_USE",
                    confidence=0.95,
                    action_type="MULTI_STEP_TASK",
                    target=clean,
                    requires_vision=True,
                    is_multi_step=True,
                )

        # ── 2. Compound "open X and do Y" commands ──
        compound_match = re.search(
            r"(?:open|go to|launch)\s+(?:chrome|browser|edge)\s+(?:and\s+)?(?:go to|search(?:\s+for)?|open|navigate to|visit)\s+(.+)",
            lower
        )
        if compound_match:
            dest = compound_match.group(1).strip()
            if dest.startswith("for "):
                dest = dest[4:].strip()
            # Check if the destination triggers a booking/complex flow
            if any(kw in dest for kw in ["bookmyshow", "ticket", "book", "order"]):
                return CommandClassification(
                    category="COMPUTER_USE",
                    confidence=0.95,
                    action_type="BROWSER_AUTONOMOUS",
                    target=dest,
                    requires_vision=True,
                    is_multi_step=True,
                )
            return CommandClassification(
                category="BROWSER_ACTION",
                confidence=0.9,
                action_type="NAVIGATE_URL",
                target=dest,
            )

        # ── 3. Browser Navigation Patterns ──
        for pattern in self._compiled_browser:
            m = pattern.search(lower)
            if m:
                target = m.group(1).strip() if m.lastindex else clean
                # Check if destination is a known web URL
                is_web = any(
                    w in target for w in [".com", ".org", ".net", ".in", ".io", "http"]
                ) or target in _WEB_DESTINATIONS
                is_media = any(kw in lower for kw in ["play", "watch", "listen to", "music", "song", "video"])
                if is_media:
                    return CommandClassification(
                        category="BROWSER_ACTION",
                        confidence=0.92,
                        action_type="MEDIA_PLAY",
                        target=target,
                    )
                if is_web or "search" in lower or "google" in lower:
                    return CommandClassification(
                        category="BROWSER_ACTION",
                        confidence=0.88,
                        action_type="NAVIGATE_URL" if is_web else "WEB_SEARCH",
                        target=target,
                    )

        # ── 4. Known web destination shorthand ──
        # "open youtube", "go to gmail", etc.
        for pfx in ("open ", "go to ", "navigate to ", "visit ", "launch "):
            if lower.startswith(pfx):
                target_name = lower[len(pfx):].strip()
                # Remove "the" prefix
                if target_name.startswith("the "):
                    target_name = target_name[4:]
                
                if target_name in _WEB_DESTINATIONS:
                    return CommandClassification(
                        category="BROWSER_ACTION",
                        confidence=0.95,
                        action_type="NAVIGATE_URL",
                        target=target_name,
                    )
                if target_name in _NATIVE_APPS:
                    return CommandClassification(
                        category="DESKTOP_ACTION",
                        confidence=0.95,
                        action_type="OPEN_APP",
                        target=target_name,
                    )

        # ── 4.5 Compound Multi-Step Desktop Tasks (Phase 4.3) ──
        # Commands like "Open Notepad, write '...', and save as ...", "Open Calculator and calculate 23 * 17"
        compound_actions = [
            r"\b(?:write|type|input|enter)\b",
            r"\b(?:save|save as)\b",
            r"\b(?:calculate|compute)\b",
            r"\b(?:click|select|choose)\b",
            r"\b(?:and then|then)\b",
        ]
        has_compound_actions = any(re.search(pat, lower) for pat in compound_actions)
        is_compound_syntax = any(sep in lower for sep in [", write", ", and write", ", type", ", and type", ", save", ", and save", ";"])
        if (lower.startswith(("open ", "launch ", "start ", "run ")) and (has_compound_actions or is_compound_syntax)) or is_compound_syntax:
            return CommandClassification(
                category="COMPUTER_USE",
                confidence=0.95,
                action_type="MULTI_STEP_TASK",
                target=clean,
                requires_vision=True,
                is_multi_step=True,
            )

        # ── 5. Desktop Action Patterns ──
        for pattern, action_type in self._compiled_desktop:
            m = pattern.match(lower)
            if m:
                target = m.group(1).strip() if m.lastindex else ""
                return CommandClassification(
                    category="DESKTOP_ACTION",
                    confidence=0.85,
                    action_type=action_type,
                    target=target or clean,
                )

        # ── 6. Play/search commands ──
        if lower.startswith(("play ", "watch ", "listen to ")):
            return CommandClassification(
                category="BROWSER_ACTION",
                confidence=0.8,
                action_type="MEDIA_PLAY",
                target=clean,
            )
        if lower.startswith(("search ", "google ", "look up ", "find ")):
            return CommandClassification(
                category="BROWSER_ACTION",
                confidence=0.8,
                action_type="WEB_SEARCH",
                target=clean,
            )

        # ── 7. Implicit desktop/browser context from keywords ──
        desktop_signal_words = [
            "take over", "desktop", "screen", "mouse", "cursor", "window",
            "tab", "browser", "chrome", "screenshot", "clipboard",
        ]
        if any(w in lower for w in desktop_signal_words):
            return CommandClassification(
                category="COMPUTER_USE",
                confidence=0.7,
                action_type="CONTEXTUAL_DESKTOP",
                target=clean,
                requires_vision=True,
            )

        # ── 8. Default: Chat ──
        return CommandClassification(
            category="CHAT",
            confidence=0.6,
            action_type="CONVERSATIONAL",
            target=clean,
        )

    def is_actionable(self, classification: CommandClassification) -> bool:
        """Returns True if the command should be executed (not just chatted about)."""
        return classification.category != "CHAT" and classification.confidence >= 0.7


# Singleton
desktop_command_router = DesktopCommandRouter()
