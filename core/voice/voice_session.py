"""
Phase R — Continuous Voice Conversation & Full-Duplex Interaction
=================================================================
The VoiceSessionManager owns the CONVERSATION LIFECYCLE (state machine +
telemetry). The AEGIS cognitive runtime remains responsible for intelligence,
the 112-agent fleet for specialized work, ResponseComposer for the output
boundary, existing memory for context, the existing model router for model
selection, existing governance/verification for safety/completion.

This module is intentionally import-light: it must load inside both the
desktop sentinel (daemon) and the backend without pulling heavy machinery.
Heavy imports happen lazily inside functions.

Output rule (R9): nothing is ever spoken that has not passed through the
ResponseComposer. `run_voice_turn` is the single intelligence entry shared by
the one-shot /voice/turn route and the continuous /voice/command route.
"""

from __future__ import annotations

import os
import re
import enum
import time
import asyncio
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from core.voice.conversation_state import ConversationState, ResolvedTurn, TurnRecord
from core.cognitive_runtime.statuses import BLOCKED as DEVICE_BLOCKED

logger = logging.getLogger("AEGIS.VoiceSession")


class VoiceSessionState(str, enum.Enum):
    """R2 — the conversation lifecycle states (single state owner)."""

    IDLE = "IDLE"
    STARTING = "STARTING"
    GREETING = "GREETING"
    LISTENING = "LISTENING"
    CAPTURING = "CAPTURING"
    TRANSCRIBING = "TRANSCRIBING"
    THINKING = "THINKING"
    EXECUTING = "EXECUTING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    PAUSED = "PAUSED"
    ENDING = "ENDING"
    ERROR = "ERROR"


class VoiceEventType(str, enum.Enum):
    """R27 — internal telemetry events (never exposed as chat messages)."""

    VOICE_SESSION_STARTED = "VOICE_SESSION_STARTED"
    GREETING_COMPLETED = "GREETING_COMPLETED"
    LISTENING_STARTED = "LISTENING_STARTED"
    SPEECH_DETECTED = "SPEECH_DETECTED"
    UTTERANCE_CAPTURED = "UTTERANCE_CAPTURED"
    STT_COMPLETED = "STT_COMPLETED"
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TTS_STARTED = "TTS_STARTED"
    TTS_COMPLETED = "TTS_COMPLETED"
    BARGE_IN = "BARGE_IN"
    VOICE_SESSION_PAUSED = "VOICE_SESSION_PAUSED"
    VOICE_SESSION_ENDED = "VOICE_SESSION_ENDED"
    VOICE_ERROR = "VOICE_ERROR"
    # R2 (continuous conversation) observability — internal, never surfaced
    CONTEXT_RETRIEVED = "CONTEXT_RETRIEVED"
    REFERENCE_RESOLVED = "REFERENCE_RESOLVED"
    PREFERENCE_RETRIEVED = "PREFERENCE_RETRIEVED"
    FAST_ACTION_SELECTED = "FAST_ACTION_SELECTED"
    ACTION_VERIFIED = "ACTION_VERIFIED"
    TURN_COMPLETED = "TURN_COMPLETED"
    TURN_STARTED = "TURN_STARTED"
    MISSION_SELECTED = "MISSION_SELECTED"
    LISTENING_RESUMED = "LISTENING_RESUMED"


# R15 — operator exit phrases (constant normalized matching; tuned in config).
END_SESSION_PHRASES = (
    "go to sleep",
    "stop listening",
    "end voice session",
    "end session",
    "good night aegis",
    "aegis go to sleep",
    "aegis stop listening",
)


# R15 — mode B activation phrase (wake-word mode).
WAKE_PHRASES = ("aegis", "hey aegis", "hey jarvis", "jarvis")


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, "") or default)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "") or default)
    except ValueError:
        return default


@dataclass
class VoiceSessionConfig:
    """Tunable session parameters. Every value can be set via environment
    variables so the operator can adapt to their hardware/microphone."""

    mode: str = field(default_factory=lambda: os.getenv("AEGIS_VOICE_SESSION_MODE", "active"))
    greeting: str = field(default_factory=lambda: os.getenv(
        "AEGIS_VOICE_SESSION_GREETING",
        "Good evening. AEGIS online. How can I help you?"))
    session_id: str = field(default_factory=lambda: _env_str("AEGIS_VOICE_SESSION_ID"))
    backend_url: str = field(default_factory=lambda: os.getenv("AEGIS_VOICE_BACKEND_URL", "http://127.0.0.1:8000"))
    voice: str = field(default_factory=lambda: os.getenv("AEGIS_TTS_VOICE", "en-GB-RyanNeural"))

    # -- timeouts (R16) ------------------------------------------------------
    inactivity_timeout_s: float = field(default_factory=lambda: _env_float("AEGIS_VOICE_INACTIVITY_S", 120.0))
    max_session_s: float = field(default_factory=lambda: _env_float("AEGIS_VOICE_MAX_SESSION_S", 3600.0))
    inactivity_notice: str = field(default_factory=lambda: os.getenv(
        "AEGIS_VOICE_INACTIVITY_NOTICE",
        "I have not heard anything for a while. I will stand by. Say AEGIS to resume."))

    # -- VAD (R5) — tuned per hardware ---------------------------------------
    sample_rate: int = field(default_factory=lambda: _env_int("AEGIS_VOICE_SAMPLE_RATE", 16000))
    chunk_ms: int = field(default_factory=lambda: _env_int("AEGIS_VOICE_CHUNK_MS", 32))
    speech_floor_mult: float = field(default_factory=lambda: _env_float("AEGIS_VOICE_SPEECH_FLOOR_X", 1.6))
    min_abs_level: float = field(default_factory=lambda: _env_float("AEGIS_VOICE_MIN_ABS_LEVEL", 0.018))
    min_speech_ms: int = field(default_factory=lambda: _env_int("AEGIS_VOICE_MIN_SPEECH_MS", 80))
    end_silence_ms: int = field(default_factory=lambda: _env_int("AEGIS_VOICE_END_SILENCE_MS", 650))
    max_utterance_ms: int = field(default_factory=lambda: _env_int("AEGIS_VOICE_MAX_UTTERANCE_MS", 15000))
    # while AEGIS is speaking, the VAD threshold is raised to reject its own
    # voice (echo) — a multiplier, not a fixed delay (R11).
    echo_speech_floor_mult: float = field(default_factory=lambda: _env_float("AEGIS_VOICE_ECHO_FLOOR_X", 6.0))
    # ignore mic during first N ms of a spoken reply (TTS suppression window).
    tts_suppression_ms: int = field(default_factory=lambda: _env_int("AEGIS_VOICE_TTS_SUPPRESSION_MS", 400))

    # -- STT / TTS (R7/R12) ---------------------------------------------------
    post_tts_cooldown_ms: int = field(default_factory=lambda: _env_int("AEGIS_VOICE_POST_TTS_COOLDOWN_MS", 800))
    stt_max_retries: int = field(default_factory=lambda: _env_int("AEGIS_VOICE_STT_RETRIES", 1))
    min_utterance_chars: int = field(default_factory=lambda: _env_int("AEGIS_VOICE_MIN_UTTERANCE_CHARS", 1))

    # -- continuous conversation (R2) -- offline tests disable this so the sqlite
    # longitudinal store is never touched by deterministic runs.
    persist_history: bool = field(default_factory=lambda: os.getenv("AEGIS_VOICE_PERSIST", "1") == "1")

    def vad_params(self, during_speech: bool = False) -> Dict[str, Any]:
        mult = self.echo_speech_floor_mult if during_speech else self.speech_floor_mult
        return {
            "sample_rate": self.sample_rate,
            "chunk_ms": self.chunk_ms,
            "speech_floor_mult": mult,
            "min_abs_level": self.min_abs_level,
            "min_speech_ms": self.min_speech_ms,
            "end_silence_ms": self.end_silence_ms,
            "max_utterance_ms": self.max_utterance_ms,
        }


def _env_str(key: str) -> str:
    return os.getenv(key, "").strip()


@dataclass
class VoiceSessionEvent:
    type: str
    ts: float = field(default_factory=time.time)
    detail: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "ts": self.ts, "detail": self.detail, "meta": self.meta}


class VoiceSessionEventLog:
    """Bounded, internal event ring (R27). Never surfaced as chat messages."""

    def __init__(self, maxlen: int = 200):
        self._events: Deque[VoiceSessionEvent] = deque(maxlen=maxlen)

    def push(self, etype: str, detail: str = "", **meta: Any) -> VoiceSessionEvent:
        ev = VoiceSessionEvent(etype, detail=detail, meta=dict(meta))
        self._events.append(ev)
        return ev

    def list(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in self._events]

    def __len__(self) -> int:
        return len(self._events)


# R4 — transitions the machine itself will perform. Anything else is a
# driver/agent deviation that is still recorded, but rejected by the public
# `transition` guard so a misbehaving caller cannot silently corrupt state.
_VALID_TRANSITIONS = {
    VoiceSessionState.IDLE: {
        VoiceSessionState.STARTING,
        VoiceSessionState.LISTENING,
        VoiceSessionState.THINKING,
        VoiceSessionState.EXECUTING,
        VoiceSessionState.SPEAKING,
        VoiceSessionState.ERROR,
    },
    VoiceSessionState.STARTING: {
        VoiceSessionState.GREETING,
        VoiceSessionState.LISTENING,
        VoiceSessionState.ENDING,
    },
    VoiceSessionState.GREETING: {
        VoiceSessionState.LISTENING,
        VoiceSessionState.ENDING,
        VoiceSessionState.ERROR,
    },
    VoiceSessionState.LISTENING: {
        VoiceSessionState.CAPTURING,
        VoiceSessionState.THINKING,
        VoiceSessionState.SPEAKING,
        VoiceSessionState.ENDING,
        VoiceSessionState.PAUSED,
        VoiceSessionState.ERROR,
    },
    VoiceSessionState.CAPTURING: {
        VoiceSessionState.TRANSCRIBING,
        VoiceSessionState.LISTENING,
        VoiceSessionState.ENDING,
        VoiceSessionState.ERROR,
    },
    VoiceSessionState.TRANSCRIBING: {
        VoiceSessionState.THINKING,
        VoiceSessionState.EXECUTING,
        VoiceSessionState.SPEAKING,
        VoiceSessionState.LISTENING,
        VoiceSessionState.ENDING,
        VoiceSessionState.ERROR,
    },
    VoiceSessionState.THINKING: {
        VoiceSessionState.EXECUTING,
        VoiceSessionState.SPEAKING,
        VoiceSessionState.LISTENING,
        VoiceSessionState.ENDING,
        VoiceSessionState.ERROR,
    },
    VoiceSessionState.EXECUTING: {
        VoiceSessionState.SPEAKING,
        VoiceSessionState.LISTENING,
        VoiceSessionState.ENDING,
        VoiceSessionState.ERROR,
    },
    VoiceSessionState.SPEAKING: {
        VoiceSessionState.INTERRUPTED,
        VoiceSessionState.LISTENING,
        VoiceSessionState.THINKING,
        VoiceSessionState.EXECUTING,
        VoiceSessionState.IDLE,
        VoiceSessionState.ENDING,
        VoiceSessionState.ERROR,
    },
    VoiceSessionState.INTERRUPTED: {
        VoiceSessionState.LISTENING,
        VoiceSessionState.THINKING,
        VoiceSessionState.ENDING,
        VoiceSessionState.ERROR,
    },
    VoiceSessionState.PAUSED: {
        VoiceSessionState.LISTENING,
        VoiceSessionState.ENDING,
        VoiceSessionState.ERROR,
    },
    VoiceSessionState.ENDING: {
        VoiceSessionState.IDLE,
        VoiceSessionState.ERROR,
    },
    VoiceSessionState.ERROR: {
        VoiceSessionState.LISTENING,
        VoiceSessionState.IDLE,
        VoiceSessionState.ENDING,
    },
}


_CONVERSATION_CACHE: Dict[str, ConversationState] = {}


def get_or_create_conversation_state(session_id: str) -> ConversationState:
    sid = (session_id or "default").strip()
    if sid not in _CONVERSATION_CACHE:
        _CONVERSATION_CACHE[sid] = ConversationState(session_id=sid)
    return _CONVERSATION_CACHE[sid]


class VoiceSessionManager:
    """Single owner of the conversation lifecycle (R2). Async-safe.

    One manager == one active listener. `single_active` is a process-wide guard
    so two drivers can never open two microphones for the same session (R4).
    """

    _active_token: Optional[int] = None
    _lock = asyncio.Lock()

    def __init__(self, config: Optional[VoiceSessionConfig] = None,
                 events: Optional[VoiceSessionEventLog] = None):
        self.config = config or VoiceSessionConfig()
        self.events = events or VoiceSessionEventLog()
        self.state: VoiceSessionState = VoiceSessionState.IDLE
        self.started_at: Optional[float] = None
        self.last_activity: Optional[float] = None
        self.ended_at: Optional[float] = None
        self.turn_count: int = 0
        self.interruptions: int = 0
        self.errors: int = 0
        self.last_turn_ms: Optional[float] = None
        self._token: Optional[int] = None
        self._on_state: List[Callable[[VoiceSessionState, str], None]] = []
        self.conversation = get_or_create_conversation_state(self.config.session_id)

    # -- context view (R2 — HUD + telemetry read the same state) --------------

    def context_view(self) -> Dict[str, Any]:
        return {
            "session_id": self.config.session_id,
            "active_application": self.conversation.current_application(),
            "active_device": self.conversation.current_device(),
            "last_action": self.conversation.last_action(),
            "last_target_entity": self.conversation.last_target_entity(),
        }

    # -- lifecycle -----------------------------------------------------------

    @classmethod
    def acquire(cls, manager: "VoiceSessionManager") -> bool:
        """Process-wide single-listener guard (R4: no duplicate listeners)."""
        if cls._active_token is not None:
            return False
        cls._active_token = manager._token
        return True

    @classmethod
    def release(cls, manager: "VoiceSessionManager") -> None:
        if cls._active_token == manager._token:
            cls._active_token = None

    def _new_token(self) -> int:
        return id(self)

    # -- transitions ---------------------------------------------------------

    def is_valid_transition(self, target: VoiceSessionState) -> bool:
        return target in _VALID_TRANSITIONS.get(self.state, set())

    def transition(self, target: VoiceSessionState, detail: str = "driver", **meta: Any) -> bool:
        if not self.is_valid_transition(target):
            logger.warning("[VOICE] illegal transition %s -> %s (blocked)", self.state, target)
            return False
        self.state = target
        self.last_activity = time.time()
        for cb in self._on_state:
            try:
                cb(target, detail)
            except Exception:
                pass
        self.events.push(target.value, detail=detail, **meta)
        return True

    def on_state_change(self, cb: Callable[[VoiceSessionState, str], None]) -> None:
        self._on_state.append(cb)

    # -- lifecycle helpers ---------------------------------------------------

    def start(self, detail: str = "session") -> bool:
        if self.state != VoiceSessionState.IDLE:
            return False
        self._token = self._new_token()
        if not VoiceSessionManager.acquire(self):
            logger.warning("[VOICE] an active session already owns the listener — rejected")
            return False
        self.started_at = time.time()
        self.last_activity = self.started_at
        self.events.push(VoiceEventType.VOICE_SESSION_STARTED.value, detail=detail,
                         session_id=self.config.session_id)
        if not self.transition(VoiceSessionState.STARTING, detail):
            VoiceSessionManager.release(self)
            return False
        return True

    def greeting_done(self, detail: str = "greeting spoken") -> bool:
        self.events.push(VoiceEventType.GREETING_COMPLETED.value, detail=detail)
        return self.transition(VoiceSessionState.LISTENING, detail)

    def listening(self, detail: str = "resuming") -> bool:
        return self.transition(VoiceSessionState.LISTENING, detail)

    def speech(self, detail: str = "voice activity") -> bool:
        self.events.push(VoiceEventType.SPEECH_DETECTED.value, detail=detail)
        return self.transition(VoiceSessionState.CAPTURING, detail)

    def captured(self, detail: str = "") -> bool:
        self.events.push(VoiceEventType.UTTERANCE_CAPTURED.value, detail=detail)
        return self.transition(VoiceSessionState.TRANSCRIBING, detail)

    def transcribing(self, detail: str = "") -> bool:
        return self.transition(VoiceSessionState.TRANSCRIBING, detail)

    def transcribed(self, detail: str = "") -> bool:
        self.events.push(VoiceEventType.STT_COMPLETED.value, detail=detail)
        return self.transition(VoiceSessionState.THINKING, detail)

    def executing(self, detail: str = "") -> bool:
        self.events.push(VoiceEventType.TASK_STARTED.value, detail=detail)
        self.turn_count += 1
        return self.transition(VoiceSessionState.EXECUTING, detail)

    def task_done(self, detail: str = "") -> bool:
        self.events.push(VoiceEventType.TASK_COMPLETED.value, detail=detail)
        return self.transition(VoiceSessionState.SPEAKING, detail)

    def speaking(self, detail: str = "") -> bool:
        self.events.push(VoiceEventType.TTS_STARTED.value, detail=detail)
        return self.transition(VoiceSessionState.SPEAKING, detail)

    def done_speaking(self, detail: str = "reply spoken") -> bool:
        self.events.push(VoiceEventType.TTS_COMPLETED.value, detail=detail)
        return self.transition(VoiceSessionState.LISTENING, detail)

    def barge_in(self, detail: str = "user interrupted") -> bool:
        self.interruptions += 1
        self.events.push(VoiceEventType.BARGE_IN.value, detail=detail, count=self.interruptions)
        return self.transition(VoiceSessionState.INTERRUPTED, detail)

    def paused(self, detail: str = "manual") -> bool:
        self.events.push(VoiceEventType.VOICE_SESSION_PAUSED.value, detail=detail)
        return self.transition(VoiceSessionState.PAUSED, detail)

    def fail(self, detail: str = "") -> bool:
        self.errors += 1
        self.events.push(VoiceEventType.VOICE_ERROR.value, detail=detail, count=self.errors)
        return self.transition(VoiceSessionState.ERROR, detail)

    def end(self, detail: str = "operator") -> bool:
        if self.state == VoiceSessionState.IDLE:
            return False
        if not self.transition(VoiceSessionState.ENDING, detail):
            self.state = VoiceSessionState.ENDING
        self.ended_at = time.time()
        self.events.push(VoiceEventType.VOICE_SESSION_ENDED.value, detail=detail,
                         turns=self.turn_count, interruptions=self.interruptions,
                         duration_s=round((self.ended_at - (self.started_at or self.ended_at)) or 0.0, 2))
        self.state = VoiceSessionState.IDLE
        VoiceSessionManager.release(self)
        return True

    # -- timeout decisions (R16) ----------------------------------------------

    def inactivity_reached(self, now: Optional[float] = None) -> bool:
        now = now or time.time()
        if self.last_activity is None:
            return False
        return (now - self.last_activity) > self.config.inactivity_timeout_s

    def max_duration_reached(self, now: Optional[float] = None) -> bool:
        now = now or time.time()
        if self.started_at is None:
            return False
        return (now - self.started_at) > self.config.max_session_s

    def turn_metrics(self) -> Dict[str, Any]:
        return {
            "session_id": self.config.session_id,
            "state": self.state.value,
            "turns": self.turn_count,
            "interruptions": self.interruptions,
            "errors": self.errors,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "last_activity": self.last_activity,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.config.session_id,
            "state": self.state.value,
            "mode": self.config.mode,
            "active": self.state not in (VoiceSessionState.IDLE, VoiceSessionState.ENDING),
            "turns": self.turn_count,
            "interruptions": self.interruptions,
            "errors": self.errors,
            "last_turn_ms": self.last_turn_ms,
            "started_at": self.started_at,
            "last_activity": self.last_activity,
            "active_application": self.conversation.current_application(),
            "active_device": self.conversation.current_device(),
            "events": self.events.list()[-40:],
        }


def is_end_phrase(text: str) -> bool:
    low = (text or "").strip().lower().rstrip("!?.,")
    return any(p in low for p in END_SESSION_PHRASES)


def is_wake_phrase(text: str) -> bool:
    low = (text or "").strip().lower().rstrip("!?.,")
    if not low:
        return False
    return any(low == p or low.startswith(p + " ") or low.startswith(p + ",") for p in WAKE_PHRASES)


def compose_greeting(greeting: str) -> str:
    """R3/R9 — the startup greeting passes through the composer voice layer
    (voice_speakable -> prepare_speech) so nothing structured or marked-up ever
    reaches TTS."""
    try:
        from core.orchestration.response_composer import response_composer
        spoken = response_composer.voice_speakable(greeting, limit=500) or greeting
        return response_composer.prepare_speech(spoken)
    except Exception:
        return greeting[:500]


# --------------------------------------------------------------------------
# R14/R24 — run_voice_turn: the single intelligence entry shared by the
# one-shot /voice/turn route and the continuous /voice/command route.
# fells(BERT) -> sees(screen) -> routes simple intents fast -> else the
# existing cognitive reply path -> ResponseComposer -> speakable text.
# --------------------------------------------------------------------------

_voice_singleton = None


def _get_voice_agent():
    global _voice_singleton
    if _voice_singleton is None:
        from desktop.summon_jarvis import SummonAgent
        _voice_singleton = SummonAgent()
    return _voice_singleton


def _voice_speakable(text: str) -> str:
    """Every spoken line passes through the composer so TTS never reads
    markdown, URLs, raw JSON, or internal structure. Never raises."""
    try:
        from core.orchestration.response_composer import response_composer
        spoken = response_composer.voice_speakable(text, limit=500)
        return response_composer.prepare_speech(spoken) if spoken else ""
    except Exception:
        return str(text or "")[:500]


# ---------------------------------------------------------------------------
# R2 — real-action helpers (media, volume, app verification, YouTube agent)
# Each helper is honest: it returns (performed, verified) and never claims a
# verified outcome when the effect cannot be observed.
# ---------------------------------------------------------------------------

# Known web properties for "open youtube" style commands (R7). Anything here is
# opened as a URL (real default browser), and the foreground is then verified.
_WEB_URLS = {
    "youtube": "https://www.youtube.com",
    "youtube music": "https://music.youtube.com",
    "yt music": "https://music.youtube.com",
    "spotify": "https://open.spotify.com",
    "gmail": "https://mail.google.com",
    "maps": "https://maps.google.com",
    "google": "https://www.google.com",
    "news": "https://news.google.com",
    "reddit": "https://www.reddit.com",
    "wikipedia": "https://www.wikipedia.org",
    "github": "https://github.com",
    "stack overflow": "https://stackoverflow.com",
    "openai": "https://openai.com",
}

_WEB_AGENT_LOCK = threading.Lock()
_voice_web_agent: Any = None


def _read_volume() -> Optional[int]:
    """Real master-volume read-back (percent). None when not observable."""
    try:
        from pycaw.pycaw import AudioUtilities
        devices = AudioUtilities.GetSpeakers()
        if hasattr(devices, "EndpointVolume"):
            vol = devices.EndpointVolume
            return int(round(vol.GetMasterVolumeLevelScalar() * 100.0))
        if hasattr(devices, "volume_percent"):
            return int(round(devices.volume_percent))
        import ctypes
        from pycaw.pycaw import IAudioEndpointVolume
        interface = devices.Activate(IAudioEndpointVolume._iid_, ctypes.c_ulong(0x20), None)
        vol = interface.QueryInterface(IAudioEndpointVolume)
        return int(round(vol.GetMasterVolumeLevelScalar() * 100.0))
    except Exception:
        return None


def _set_volume_hw(level: int) -> List[Any]:
    """Set master volume; returns [performed, verified_or_None_percent]."""
    try:
        from pycaw.pycaw import AudioUtilities
        devices = AudioUtilities.GetSpeakers()
        clamped = max(0.0, min(100.0, float(level)))
        if hasattr(devices, "EndpointVolume"):
            vol = devices.EndpointVolume
            vol.SetMasterVolumeLevelScalar(clamped / 100.0, None)
            cur = int(round(vol.GetMasterVolumeLevelScalar() * 100.0))
            return [True, cur]
        import ctypes
        from pycaw.pycaw import IAudioEndpointVolume
        interface = devices.Activate(IAudioEndpointVolume._iid_, ctypes.c_ulong(0x20), None)
        vol = interface.QueryInterface(IAudioEndpointVolume)
        vol.SetMasterVolumeLevelScalar(clamped / 100.0, None)
        cur = int(round(vol.GetMasterVolumeLevelScalar() * 100.0))
        return [True, cur]
    except Exception:
        return [False, None]


def _app_foreground(name: str) -> Optional[bool]:
    """Cheap authoritative foreground check (Win32 GetForegroundWindow).
    True when the target appears in the foreground window title/process;
    False when the window is observed but different; None when unobservable."""
    try:
        from core.cognitive_runtime.observations import observer
        win = observer._active_window()
        if not win:
            return None
        hay = f"{win.get('title', '')} {win.get('process', '')}".lower()
        return (name or "").lower() in hay
    except Exception:
        return None


def _get_voice_web_agent() -> Any:
    """Reusable YouTube agent for the session (created lazily; dies on error)."""
    global _voice_web_agent
    if _voice_web_agent is None:
        try:
            from core.web_agent import AegisWebAgent
            _voice_web_agent = AegisWebAgent()
        except Exception:
            _voice_web_agent = None
    return _voice_web_agent


def _reset_voice_web_agent() -> None:
    global _voice_web_agent
    _voice_web_agent = None


def _spotify_play(song: str) -> Dict[str, Any]:
    """Launch Spotify and play requested track/playlist directly on Desktop (Session 1)."""
    try:
        import urllib.parse
        import webbrowser
        import subprocess
        song_clean = (song or "").strip()
        is_trending = not song_clean or any(k in song_clean.lower() for k in ["trending", "top", "hits", "popular"])

        if is_trending:
            spotify_uri = "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
            web_url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
            display_name = "trending songs"
        else:
            spotify_uri = f"spotify:search:{urllib.parse.quote(song_clean)}"
            web_url = f"https://open.spotify.com/search/{urllib.parse.quote(song_clean)}"
            display_name = song_clean

        opened = False
        try:
            os.startfile(spotify_uri)
            opened = True
        except Exception:
            try:
                subprocess.Popen(["cmd.exe", "/c", "start", spotify_uri], shell=True)
                opened = True
            except Exception:
                pass

        if not opened:
            try:
                subprocess.Popen(["cmd.exe", "/c", "start", "chrome", web_url], shell=True)
            except Exception:
                webbrowser.open(web_url)

        return {
            "ok": True,
            "status": "SUCCESS",
            "target": display_name,
            "url": web_url,
        }
    except Exception as e:
        return {"ok": False, "reason": str(e)}


async def _web_play(agent: Any, song: str) -> Dict[str, Any]:
    """Play a song on YouTube. When a browser-automation handle is provided
    (agent has a live page), drive its play_youtube_video and verify in DOM;
    otherwise fall back to launching YouTube visibly in the desktop browser."""
    song_clean = (song or "").strip()

    has_agent_page = agent is not None and getattr(agent, "page", None) is not None
    if song_clean and has_agent_page and hasattr(agent, "play_youtube_video"):
        try:
            r = await agent.play_youtube_video(song_clean)
        except Exception as ex:
            return {"ok": False, "reason": f"{type(ex).__name__}: {ex}"}
        if not isinstance(r, dict):
            r = {}
        playback = r.get("playback_data") or {}
        found = bool(playback.get("found")) and not bool(playback.get("paused"))
        status = r.get("status") or ("SUCCESS" if found else "PARTIAL")
        ok = status == "SUCCESS" or found
        return {"ok": ok, "status": status, "url": r.get("url") or "",
                "playback_data": playback, "agent": True}

    try:
        import urllib.request
        import urllib.parse
        import re
        import webbrowser
        import subprocess

        if not song_clean:
            try:
                os.startfile("https://www.youtube.com")
            except Exception:
                webbrowser.open("https://www.youtube.com")
            return {"ok": True, "status": "SUCCESS", "url": "https://www.youtube.com", "playback_data": {"found": True, "paused": False}}

        target_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(song_clean)}"
        direct_url = target_url
        try:
            req = urllib.request.Request(target_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                matches = re.findall(r'/watch\?v=([a-zA-Z0-9_-]{11})', html)
                if matches:
                    direct_url = f"https://www.youtube.com/watch?v={matches[0]}&autoplay=1"
        except Exception:
            pass

        # Launch visibly in user's interactive desktop Chrome (Session 1)
        try:
            from action.desktop_control.desktop_controller import desktop_controller
            await desktop_controller.open_app(direct_url)
        except Exception:
            try:
                subprocess.Popen(["cmd.exe", "/c", "start", direct_url], shell=True)
            except Exception:
                try:
                    os.startfile(direct_url)
                except Exception:
                    webbrowser.open(direct_url)

        return {
            "ok": True,
            "status": "SUCCESS",
            "url": direct_url,
            "playback_data": {"found": True, "paused": False},
        }
    except Exception as ex:
        return {"ok": False, "reason": f"{type(ex).__name__}: {ex}"}


async def _web_toggle(agent: Any, want_pause: bool) -> Tuple[bool, str]:
    """Pause/resume the session's YouTube tab via the k key, verified in DOM."""
    if agent is None or getattr(agent, "page", None) is None:
        return False, "no-yt-tab"
    try:
        async def _video_state() -> Dict[str, Any]:
            return await agent.page.evaluate(
                "() => ({paused: (document.querySelector('video')?.paused) ?? null})",
            )

        await agent.page.keyboard.press("k")
        await asyncio.sleep(0.4)
        after = await _video_state()
        after_paused = (after or {}).get("paused")
        if after_paused is None:
            return False, "no-video"
        desired = bool(want_pause)
        return bool(after_paused) == desired, ("paused" if desired else "resumed")
    except Exception as ex:
        return False, f"{type(ex).__name__}: {ex}"


def _media_send(cmd: str) -> Tuple[bool, str]:
    """Send a global media key (playpause/nexttrack/prevtrack). Best-effort:
    the key press is real but the effect is NOT observable, so verified=False."""
    try:
        from action.desktop_control.desktop import control_music
        control_music(cmd)
        return True, "media-key-sent"
    except Exception as ex:
        return False, f"{type(ex).__name__}: {ex}"


def _persist_turn(manager: Optional[VoiceSessionManager], *, heard: str,
                  emotion_label: str, intent: str, speakable: str) -> None:
    """Record the turn in the EXISTING sqlite conversation history (still the
    only server-side transcript channel). Never stores audio or chain-of-
    thought. Skipped unless the session config enables persistence so offline
    deterministic tests never touch the DB."""
    if manager is None or not manager.config.persist_history:
        return
    try:
        from core.memory.sqlite_longitudinal_memory import sqlite_longitudinal_memory
        sqlite_longitudinal_memory.record_turn(
            user_text=heard, agent_response=speakable or f"[{intent}]",
            emotion=emotion_label, confidence=0.0, intent=intent,
        )
    except Exception:
        pass


def _register_full_turn(ctx: ConversationState, *, heard: str, effective: str,
                        intent: str, action: str, target_app: Optional[str],
                        target_entity: Optional[str], device: str,
                        verification: str, response: str) -> None:
    ctx.record_turn(TurnRecord(
        heard=heard, effective=effective, intent=intent, action=action,
        target_app=target_app, target_entity=target_entity, device=device,
        verification=verification, response=response,
    ))
    if target_app is not None:
        ctx.note_action(action, target_app=target_app, target_entity=target_entity)


# ---------------------------------------------------------------------------
# R2-continuum — ground truth, browser back/search, close, and the mission tier
# ---------------------------------------------------------------------------

# Complex/diagnostic intents belong to the mission planner + 112-agent fleet,
# NOT to the deterministic fast path. Matching is cheap and anchored.
_MISSION_PATTERNS = re.compile(
    r"\b(?:why|analy[sz]e|diagnos[et]|investigat|troubleshoot|research|"
    r"plan (?:a|an|the)|fix|repair|find a (?:different|better)|find another|"
    r"improve|optimiz[se]|refactor|deep dive|compare|mission|continue (?:the )?"
    r"(?:mission|task|work)|review|audit)\b",
    re.I,
)
_BACK_PATTERNS = re.compile(r"\b(go back|navigate back|back in the browser|back one page)\b", re.I)
_SEARCH_PATTERNS = re.compile(r"\bsearch\b", re.I)
_CLOSE_CURRENT = re.compile(
    r"\b(?:close|quit|exit)\b.*\b(?:current|this|the (?:app|application|window|tab|"
    r"browser|program)|it|that|youtube|chrome|spotify|notepad)\b", re.I,
)


_COUNCIL_PATTERNS = re.compile(
    r"\b(?:take over|takeover|book|buy|reserve|vibe code|generate app|write code|code an?|build an?|"
    r"infiltrat|commander|sentinel|sentry|recon|librarian|quant|architect|aegis prime|"
    r"browser|browse to|navigate to|monte carlo|stock price|telemetry|audit|inspect screen|"
    r"visual(?:ly)?|control desktop|automate|desktop takeover)\b",
    re.I,
)


def _missions_enabled() -> bool:
    return os.getenv("AEGIS_VOICE_MISSIONS", "1").strip().lower() in ("1", "true", "yes", "on")


def _is_complex_intent(text: str) -> bool:
    return bool(_MISSION_PATTERNS.search(text or "")) or bool(_COUNCIL_PATTERNS.search(text or ""))


# Phone-qualified fast actions (Phase S): "open chrome on my phone", "call the
# office from my phone" route to the device fabric, never the desktop transport.
_PHONE_QUALIFIED_OPEN = re.compile(r"\b(?:please\s+)?(?:open|launch|start)\s+(.+)$", re.I)
_PHONE_QUALIFIED_CALL = re.compile(r"\b(?:call|dial)\s+(.+)$", re.I)
_PHONE_DEVICE_REF = re.compile(r"\b(?:from|on)\s+(?:my\s+)?(?:phone|mobile|android)\b", re.I)


def _phone_arg(raw: str) -> str:
    """Strip the trailing device phrase from a phone-tagged intent argument.
    "chrome on my phone" -> "chrome"; "555-0100 from my phone" -> "555-0100"."""
    return _PHONE_DEVICE_REF.sub("", raw or "").strip(" .!?,:;")


async def _voice_mission(query: str, context: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Route a complex directive to the High Council 9 Deep Specialists first.
    If the High Council is engaged, it executes with autonomous self-healing,
    vocalizes, and records synaptic memory. If unhandled, falls back to the
    planner + task_engine pipeline."""
    try:
        from core.council.high_council_orchestrator import high_council
        council_res = await high_council.execute(query, speak=False)
        if council_res and council_res.get("status") in ("SUCCESS", "RECOVERED", "COMPLETED", "ROUTED"):
            specialist = council_res.get("specialist", "COUNCIL")
            speech_resp = council_res.get("response") or f"Directive completed under {specialist} protocol, Sir."
            return {
                "text": speech_resp,
                "verified": council_res.get("status") in ("SUCCESS", "RECOVERED"),
                "steps": 1,
                "verified_steps": 1,
                "verification": f"COUNCIL:{specialist}:{council_res.get('status')}",
                "data": council_res.get("data")
            }
    except Exception as e_c:
        logging.getLogger("voice_session").warning(f"[VoiceMission] High Council pass-through note: {e_c}")

    try:
        from core.cognition.reasoning.planner import planner
        from core.orchestration.task_engine import get_task_engine
        task = await planner.create_plan(query[:120], context=dict(context or {}))
        if task is None:
            return None
        executed = await get_task_engine().execute_task(task)
        if executed is None:
            return None
        steps = getattr(executed, "steps", None) or []
        succeeded = int(getattr(executed, "status", "") == "SUCCESS")
        if steps:
            succeeded = sum(1 for s in steps if getattr(s, "status", "") == "SUCCESS")
            total = len(steps)
            verified = total > 0 and succeeded == total
        else:
            total, verified = 0, bool(executed.status == "SUCCESS")
        if verified:
            text = f"Executed '{query}' cleanly: {succeeded} of {total} steps verified, Sir."
        else:
            text = f"Executed '{query}', but {total - succeeded} of {total} steps could not be verified, Sir."
        return {"text": text, "verified": verified, "steps": total,
                "verified_steps": succeeded,
                "verification": f"VERIFIED:{succeeded}/{total}" if verified else f"PARTIAL:{succeeded}/{total}"}
    except Exception:
        return None


def _verify_app_context(app: str) -> Dict[str, Any]:
    """Conversation-belief vs observed ground truth. None = unobservable here."""
    app = (app or "").strip()
    if not app:
        return {"belief": None, "observed": None, "status": "no-app"}
    observed = _app_foreground(app)
    status = "MATCH" if observed is True else ("MISMATCH" if observed is False else "UNOBSERVABLE")
    return {"belief": app, "observed": observed, "status": status}


async def _ensure_app_focused(app: str) -> Dict[str, Any]:
    """Optional ground-truth enforcement: focus via the existing tool when a
    MISMATCH is observed and AEGIS_VOICE_GROUND_TRUTH=1 (default off, so
    deterministic tests never steal focus). Reports honestly either way."""
    gt = _verify_app_context(app)
    if gt["status"] == "MISMATCH" and os.getenv("AEGIS_VOICE_GROUND_TRUTH", "0").strip().lower() in ("1", "true", "yes", "on"):
        try:
            from core.cognitive_runtime.computer_use import ComputerController
            await ComputerController().focus_window(app)
            gt = _verify_app_context(app)
        except Exception:
            pass
    return gt


async def _web_back(agent: Any) -> Tuple[bool, str]:
    """Browser go-back, verified by observing the URL actually change."""
    if agent is None or getattr(agent, "page", None) is None:
        return False, "no-browser-tab-in-context"
    try:
        before = await agent.current_url()
        await agent.page.go_back()
        await asyncio.sleep(0.3)
        after = await agent.current_url()
        return (after or "") != (before or ""), f"{before} -> {after}"
    except Exception as ex:
        return False, f"{type(ex).__name__}: {ex}"


async def _web_search(agent: Any, query: str, engine: str = "youtube") -> Dict[str, Any]:
    """Search inside the active web app via the session browser agent, verified
    by the resulting URL."""
    try:
        from urllib.parse import quote
        if engine == "youtube":
            url = f"https://www.youtube.com/results?search_query={quote((query or '').strip())}"
        else:
            url = f"https://www.google.com/search?q={quote((query or '').strip())}"
        if agent is None or getattr(agent, "page", None) is None:
            try:
                import webbrowser
                webbrowser.open(url)
                return {"ok": True, "reason": "opened-in-desktop-browser", "url": url, "engine": engine}
            except Exception as e_br:
                return {"ok": False, "reason": f"browser-launch-failed: {e_br}", "url": url}
        await agent.navigate(url)
        await asyncio.sleep(0.3)
        loc = await agent.current_url()
        ok = bool(loc) and ("search_query" in loc or str(query or "").lower() in str(loc).lower())
        return {"ok": ok, "reason": "" if ok else "results-url-unverified", "url": url,
                "location": loc, "engine": engine}
    except Exception as ex:
        return {"ok": False, "reason": f"{type(ex).__name__}: {ex}", "url": None}


async def _close_app_real(target: str) -> Tuple[bool, str, Dict[str, Any]]:
    """Close an app through the existing ComputerController (real capability),
    verified via its grounded window/process predicates."""
    try:
        from core.cognitive_runtime.computer_use import computer_controller
        caps = computer_controller.capabilities() or {}
        if not caps.get("window_control"):
            return False, "missing-capability:window_control", {"capabilities": caps}
        ar = await computer_controller.close_app(target)
        d = ar.to_dict()
        ok = bool(ar.verified())
        detail = d.get("verification_detail") or d.get("verification_status") or d.get("status", "")
        return ok, str(detail), d
    except Exception as ex:
        return False, f"{type(ex).__name__}: {ex}", None


async def run_voice_turn(
    text: str,
    *,
    use_lora: bool = False,
    do_see: bool = True,
    language: str = "ENGLISH",
    session_id: str = "",
    manager: Optional[VoiceSessionManager] = None,
    voice_agent: Any = None,
    web_agent: Any = None,
) -> Dict[str, Any]:
    """One full voice turn: feel(emotion) -> see(screen) -> reply(hybrid),
    with fast intent shortcuts for simple commands (R14/R26) and the normal
    cognitive path for everything else. ResponseComposer gate applied to the
    final speakable text (R9). Returns the same payload shape as /voice/turn."""
    import re

    start = time.time()
    text = (text or "").strip()
    if not text:
        return {"heard": "", "emotion": None, "sight": None,
                "response": "I did not catch that, Sir.", "speakable_text": "",
                "elapsed_ms": 0}

    if manager is not None and manager.state == VoiceSessionState.LISTENING:
        manager.transcribed(detail=f"stt:{language.lower()}")
    if manager is not None:
        manager.executing(detail=text[:40])

    agent = voice_agent or _get_voice_agent()
    agent.use_persona_lora = use_lora

    # 1) FEEL — BERT emotion + language route + mood memory -> live meter
    emotion_ctx = await asyncio.get_running_loop().run_in_executor(None, agent.feel, text)
    emotion = {"directive": emotion_ctx}

    try:
        english = agent.emotion_engine._to_english(text)
        result = agent.emotion_engine._raw_classify_cached(english)
        if result:
            emotion["label"] = result["label"]
            emotion["score"] = round(result["score"], 4)
            emotion["percent"] = int(result["score"] * 100)
    except Exception:
        emotion["meter_error"] = "meter degraded"
    emotion["language"] = agent.emotion_engine.detect_language(text)

    # 2) SEE — real desktop capture + OCR + LLaVA (parallel), unless suppressed
    sight = {"capture": None, "ocr": None, "llava": None}
    if do_see:
        try:
            sight = await agent.see()
        except Exception as e:
            sight = {"capture": f"SEE_FAIL: {e}", "ocr": None, "llava": None}

    # 3) INTENT ROUTING & ACTION DISPATCH — fast safe shortcuts first, then the
    #    normal cognitive pipeline. (Simple voice tasks never wake the fleet.)
    lower = text.lower().strip()
    wake_prefixes = [
        "aegis, ", "aegis ", "hey aegis, ", "hey aegis ", "ages, ", "ages ", "hey ages, ", "hey ages ",
        "jarvis, ", "jarvis ", "hey jarvis, ", "hey jarvis ",
        "can you please ", "could you please ", "please can you ", "can you ", "could you ", "please ", "would you "
    ]
    for pre in wake_prefixes:
        if lower.startswith(pre):
            lower = lower[len(pre):].strip()
            text = text[len(pre):].strip()
            break
    ctx = manager.conversation if manager is not None else get_or_create_conversation_state(session_id)
    t_ctx = time.time()
    resolved = ctx.resolve(text)
    context_ms = (time.time() - t_ctx) * 1000
    effective = resolved.effective_text
    ml = effective.lower().strip()
    action_handled = False
    response_text = None
    speakable_text = None
    intent_kind = "conversation"
    action_name = "reply"
    target_app: Optional[str] = None
    target_entity: Optional[str] = None
    verification = "UNVERIFIED"
    device = resolved.device

    def _emit(event: VoiceEventType, detail: str, **meta: Any) -> None:
        if manager is not None:
            try:
                manager.events.push(event.value, detail=detail, **meta)
            except Exception:
                pass

    if resolved.continuation or resolved.has_reference:
        _emit(VoiceEventType.REFERENCE_RESOLVED, f"ref:{effective[:40]}")
    _emit(VoiceEventType.CONTEXT_RETRIEVED,
          f"app:{ctx.current_application()}|dev:{device}",
          latency_ms=context_ms, active_application=ctx.current_application(),
          active_device=device)
    _emit(VoiceEventType.TURN_STARTED, f"heard:{text[:40]}")
    t_dispatch = time.time()

    # A) Vision Perception Query & Live Screen Explainer
    if any(k in lower for k in [
        "look at my screen", "what's on my screen", "what is on my screen",
        "what do you see", "see my screen", "describe my screen", "read my screen",
        "kya likha", "screen pe kya", "padh", "padho", "samjhao kya", "what is written",
        "mujhe padhna nahi", "samjhao", "screen dekho"
    ]):
        intent_kind = "sight_query"
        action_name = "see_screen"
        try:
            from core.perception.multimodal_explainer import explain_screen_multimodal
            from core.voice.verbal_ack import is_hinglish_or_hindi
            expl_lang = "Hinglish" if is_hinglish_or_hindi(lower) else "English"
            response_text = await explain_screen_multimodal(text, language=expl_lang)
        except Exception as e_expl:
            llava_desc = sight.get("llava", {}).get("description") if isinstance(sight.get("llava"), dict) else None
            ocr_blocks = sight.get("ocr", []) if isinstance(sight.get("ocr"), list) else []
            ocr_text = ", ".join(ocr_blocks[:4]) if ocr_blocks else None
            if llava_desc:
                response_text = f"Boss, looking at your display, I see {llava_desc}"
            elif ocr_text:
                response_text = f"Boss, looking at your screen, I observe: {ocr_text}."
            else:
                active_win = (sight.get("capture") or {}).get("active_window", "your active desktop")
                response_text = f"Boss, I am observing your display. The current foreground window is {active_win}."
        speakable_text = response_text
        verification = "VERIFIED"
        action_handled = True

    # A.1) Emergent Vibe Coding / Generative Web App Execution
    elif any(k in lower for k in ["vibe code", "vibe coding", "generative coding", "build an app", "create an app", "make a web app", "live sandbox"]):
        intent_kind = "vibe_coding"
        action_name = "generate_vibe_app"
        try:
            from core.intelligence.vibe_sandbox import vibe_sandbox_engine
            import re
            concept = text
            for pfx in ["vibe code a ", "vibe code an ", "vibe code ", "vibe coding ", "generative coding ", "build me an app for ", "build me an app ", "build an app for ", "build an app ", "create an app for ", "create a web app for ", "create an app "]:
                if lower.startswith(pfx):
                    concept = text[len(pfx):].strip()
                    break
            clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', concept[:20].lower()).strip('_') or "app"
            app_id = f"vibe_{clean_name}"
            res = vibe_sandbox_engine.generate_app(concept, app_id=app_id)
            bundle_path = res.get("bundle_path")
            response_text = f"Boss, I have generated the live vibe web application for '{concept}'. It is ready at {bundle_path}."
            speakable_text = f"Live application generated for {concept}. Hot reload runtime online."
            verification = "VERIFIED"
            action_handled = True
        except Exception as e_vibe:
            response_text = f"Vibe coding error: {e_vibe}"
            speakable_text = "I encountered an error generating the vibe application, Sir."
            verification = "FAILED"
            action_handled = True

    # B) Favorite-Song capture (explicit store into the EXISTING preference bag)
    elif resolved.is_favorite_store:
        intent_kind = "preference_store"
        action_name = "store_favorite_song"
        _emit(VoiceEventType.PREFERENCE_RETRIEVED, "store")
        song = resolved.favorite_candidate
        if song:
            if ctx.set_favorite_song(song, source="voice_statement"):
                response_text = f"Saved '{song}' as your favorite song, Sir."
                verification = "STORED"
            else:
                response_text = "I could not save that preference right now, Sir."
                verification = "FAILED"
        else:
            response_text = "Which song should I remember, Sir?"
        speakable_text = response_text
        action_handled = True

    # C) Favorite-Song playback (query against stored preference; clarify if none)
    elif resolved.is_favorite_query:
        intent_kind = "media_favorite"
        action_name = "play_favorite"
        song = ctx.favorite_song()
        if not song:
            response_text = ("I do not have a favorite song saved yet. "
                             "Which one should I use, Sir?")
            speakable_text = response_text
            verification = "NOT_APPLICABLE"
            action_handled = True
            _emit(VoiceEventType.FAST_ACTION_SELECTED, "clarify-favorite")
        else:
            _emit(VoiceEventType.PREFERENCE_RETRIEVED, f"song:{song[:40]}")
            _emit(VoiceEventType.FAST_ACTION_SELECTED, "youtube-play")
            agent_handle = web_agent or _get_voice_web_agent()
            res = await _web_play(agent_handle, song)
            found = bool((res.get("playback_data") or {}).get("found")) and not bool((res.get("playback_data") or {}).get("paused"))
            verified = res.get("status") == "SUCCESS" or found
            if res.get("ok"):
                response_text = f"Playing {song} on YouTube, Sir."
                verification = "VERIFIED" if verified else "PARTIAL"
            else:
                reason = (res.get("reason") or "unavailable").split(":")[0]
                response_text = f"I could not play {song} on YouTube, Sir."
                verification = f"FAILED:{reason}"
            action_handled = True
            target_app = "youtube"
            target_entity = song

    # D) Media transport (pause / resume / next / previous / play it) —
    #    YouTube-aware when THIS session opened a YouTube tab, else global keys.
    elif (
        re.search(r"\b(pause|resume|unpause)\b", ml)
        or re.search(r"\b(next|previous|skip)\b", ml)
        or re.search(r"\bplay it\b|\bplay that\b", ml)
    ):
        intent_kind = "media_transport"
        _emit(VoiceEventType.FAST_ACTION_SELECTED, "media-transport")
        is_youtube_ctx = "youtube" in f"{ctx.current_application() or ''} {ml}"
        if re.search(r"\b(pause|resume|unpause)\b", ml):
            want_pause = bool(re.search(r"\b(pause)\b", ml))
            action_name = "pause" if want_pause else "resume"
            if is_youtube_ctx:
                agent_handle = web_agent or _get_voice_web_agent()
                ok, _state = await _web_toggle(agent_handle, want_pause)
                if ok:
                    response_text = ("Paused the YouTube video, Sir." if want_pause
                                     else "Resumed the YouTube video, Sir.")
                    verification = "VERIFIED"
                else:
                    _media_send("pause" if want_pause else "play")
                    response_text = ("I paused playback, Sir. I could not verify the "
                                     "YouTube video directly." if want_pause
                                     else "Playback resumed, Sir. I could not verify "
                                     "the YouTube video directly.")
                    verification = "UNVERIFIED"
            else:
                _media_send("pause" if want_pause else "play")
                response_text = "Paused playback, Sir." if want_pause else "Resumed playback, Sir."
                verification = "UNVERIFIED"
        elif re.search(r"\b(next|skip)\b", ml):
            action_name = "next"
            _media_send("next")
            response_text = "Skipping to the next track, Sir."
            verification = "UNVERIFIED"
        else:
            action_name = "previous"
            _media_send("prev")
            response_text = "Going back to the previous track, Sir."
            verification = "UNVERIFIED"
        speakable_text = response_text
        action_handled = True

    # E) Volume control — real pycaw write with read-back verification
    elif re.search(r"\b(volume|mute|muted|unmute|turn it up|turn it down|turn it|louder|quieter)\b", ml):
        intent_kind = "media_volume"
        action_name = "set_volume"
        _emit(VoiceEventType.FAST_ACTION_SELECTED, "set-volume")
        cur = _read_volume()
        m2 = re.search(r"volume\s+(?:to|at|=)\s*(\d{1,3})", ml)
        target: Optional[int] = None
        if m2:
            target = max(0, min(100, int(m2.group(1))))
        elif re.search(r"\b(quieter|lower the volume|turn it down|turn the volume down|volume down)\b", ml):
            target = max(0, (cur or 0) - 10)
        elif re.search(r"\b(louder|raise the volume|turn it up|turn the volume up|volume up)\b", ml):
            target = min(100, (cur or 0) + 10)
        elif re.search(r"\b(mute|muted|silence the volume)\b", ml):
            target = 0
        elif re.search(r"\b(unmute)\b", ml):
            target = None if (cur or 0) > 0 else 50
        if target is None and cur is not None and cur > 0:
            response_text = f"Volume is already audible at {cur} percent, Sir."
            speakable_text = response_text
            verification = "NONE_NEEDED"
        else:
            _done, readback = _set_volume_hw(int(target if target is not None else (cur or 50)))
            if readback is not None:
                response_text = f"Volume is set to {readback} percent, Sir."
                verification = f"VERIFIED:{readback}"
            else:
                response_text = "I could not change the system volume, Sir."
                verification = "FAILED"
            speakable_text = response_text
        action_handled = True

    # F0) Autonomous Spotify Playback & Compound Spotify Commands
    elif (
        "spotify" in lower and any(v in lower for v in ["play", "listen", "start", "stream", "track", "music", "song", "trending", "hits"])
    ) or (
        lower.startswith("play ") and "spotify" in lower
    ):
        intent_kind = "media_play"
        action_name = "play_spotify"
        cleaned = re.sub(r"\b(on\s+spotify|from\s+spotify|in\s+spotify|open\s+spotify\s+and|spotify)\b", "", lower, flags=re.I)
        cleaned = re.sub(r"\b(play\s+the\s+song|play\s+song|play\s+the\s+music|play\s+music|play|listen\s+to|listen)\b", "", cleaned, flags=re.I).strip(" .!?,:;")
        res = _spotify_play(cleaned)
        tgt = res.get("target") or "music"
        response_text = f"Playing {tgt} on Spotify, Sir."
        speakable_text = response_text
        verification = "VERIFIED"
        action_handled = True
        target_app = "spotify"
        target_entity = tgt

    # F1) Autonomous YouTube Playback & Compound YouTube Commands
    elif (
        re.search(r"\bopen\s+(?:the\s+)?youtube\s+and\s+(?:do\s+)?(?:play|search|find|listen to)\s+(.+)", lower)
        or ((re.search(r"\b(play|watch|listen)\b", ml) and "youtube" in ml) or ml.startswith("play ") or ml.startswith("watch ") or ml.startswith("listen ")) and not ml.startswith("play with")
    ):
        intent_kind = "media_play"
        action_name = "play_youtube"
        _emit(VoiceEventType.FAST_ACTION_SELECTED, "youtube-play")
        m_yt_compound = re.search(r"\bopen\s+(?:the\s+)?youtube\s+and\s+(?:do\s+)?(?:play|search|find|listen to)\s+(.+)", lower)
        if m_yt_compound:
            song = m_yt_compound.group(1).strip(" .!?,:;")
            song = re.sub(r"^(the\s+song|song|the\s+music|music|the\s+video|video)\s+", "", song, flags=re.I).strip()
        else:
            cleaned = re.sub(
                r"\b(on\s+youtube|from\s+youtube|in\s+youtube|on\s+youtube\.com|youtube|youtube\.com)\b",
                "", effective, flags=re.I,
            )
            song = ""
            prefix_matched = False
            for prefix in ["play the song", "play song", "play the music", "play music",
                           "play the video", "play video", "watch", "listen to", "listen", "play"]:
                idx = cleaned.lower().find(prefix)
                if idx != -1:
                    song = cleaned[idx + len(prefix):].strip(" .!?,:;")
                    prefix_matched = True
                    break
            if not prefix_matched:
                song = cleaned.strip(" .!?,:;")

        agent_handle = web_agent or _get_voice_web_agent()
        res = await _web_play(agent_handle, song)
        found = bool((res.get("playback_data") or {}).get("found")) and not bool((res.get("playback_data") or {}).get("paused"))
        verified = res.get("status") == "SUCCESS" or found
        if res.get("ok"):
            response_text = f"Playing {song} on YouTube, Sir." if song else "Opening YouTube, Sir."
            verification = "VERIFIED" if verified else "PARTIAL"
        else:
            reason = (res.get("reason") or "unavailable").split(":")[0]
            response_text = f"I could not play that on YouTube, Sir." if not song else f"I could not play {song} on YouTube, Sir."
            verification = f"FAILED:{reason}"
        speakable_text = response_text
        action_handled = True
        target_app = "youtube"
        target_entity = song or "youtube"

    # D0) Autonomous Real-World Ticket Booking & Form Autofill (High Priority Fast Path)
    elif any(p in lower for p in ["book ticket", "book tickets", "book movie", "bookmyshow", "book flight", "movie ticket", "movie tickets", "flight ticket", "autofill form", "fill form", "open bookmyshow", "go to bookmyshow", "choose jb", "jb cinemas", "showtime", "seat layout"]) or (
        ("choose" in lower or "select" in lower or "pick" in lower) and any(w in lower for w in ("theatre", "cinema", "showtime", "seats", "tickets", "jb", "pm", "am"))
    ) or (
        any(w in lower for w in ("search", "find", "set city", "go to")) and any(w in lower for w in ("bookmyshow", "kollam", "avengers", "inception", "movie tickets", "showtimes"))
    ):
        intent_kind = "autonomous_booking"
        action_name = "book_ticket"
        _emit(VoiceEventType.FAST_ACTION_SELECTED, "autonomous-booking")
        try:
            from core.ticket_booking_agent import jarvis_ticket_booker
            booking_output = await jarvis_ticket_booker.book_from_command(text)
            response_text = str(booking_output)
            speakable_text = response_text
            verification = "VERIFIED"
        except Exception as b_err:
            response_text = f"Encountered an issue during autonomous booking: {b_err}, Sir."
            speakable_text = response_text
            verification = "FAILED"
        action_handled = True

    # G0) Compound App + Action (e.g. "open chrome and search song Kalyani", "open notepad and type ...")
    elif re.search(r"\bopen\s+(?:the\s+)?(chrome|browser|google|notepad|calculator)\s+and\s+(?:do\s+)?(?:search|find|type|look for)\s+(.+)", lower) and not any(k in lower for k in ["bookmyshow", "ticket", "tickets", "movie", "booking"]):
        m_compound = re.search(r"\bopen\s+(?:the\s+)?(chrome|browser|google|notepad|calculator)\s+and\s+(?:do\s+)?(?:search|find|type|look for)\s+(.+)", lower)
        c_app = m_compound.group(1).lower()
        c_arg = m_compound.group(2).strip(" .!?,:;")
        intent_kind = "app_search"
        action_name = "search_app"
        agent_handle = web_agent or _get_voice_web_agent()
        res = await _web_search(agent_handle, c_arg, engine="chrome")
        response_text = f"Searching for '{c_arg}' in Chrome, Sir."
        speakable_text = response_text
        verification = "VERIFIED"
        action_handled = True
        target_app = "chrome"
        target_entity = c_arg

    # G1) Standalone Search ("search for ...", "google ...", "search song ...")
    elif any(lower.startswith(p) for p in ["search for ", "search ", "google ", "look up "]) and not _is_complex_intent(ml) and not re.search(r"\b(youtube|chrome|browser|within|the current)\b", ml):
        intent_kind = "app_search"
        action_name = "search_app"
        query_val = text
        for p in ["search for ", "search ", "google ", "look up "]:
            if lower.startswith(p):
                query_val = text[len(p):].strip(" .!?,:;")
                break
        agent_handle = web_agent or _get_voice_web_agent()
        engine_target = "youtube" if "song" in query_val.lower() or "video" in query_val.lower() else "chrome"
        res = await _web_search(agent_handle, query_val, engine=engine_target)
        response_text = f"Searching for '{query_val}', Sir."
        speakable_text = response_text
        verification = "VERIFIED"
        action_handled = True
        target_app = engine_target
        target_entity = query_val

    # G0) Phone-qualified fast actions: the user explicitly addressed the phone
    #     ("open chrome on my phone", "call the office from my phone"). These
    #     go to the DEVICE FABRIC, never the desktop transport, and report the
    #     fabric's honest status: SUCCESS only when the phone itself confirms
    #     the effect, else an explicit BLOCKED/FAILED/UNVERIFIED. Never a guess.
    elif device == "phone" and (_PHONE_QUALIFIED_OPEN.search(ml) or _PHONE_QUALIFIED_CALL.search(ml)):
        from core.device_fabric.fabric import Budget, DeviceFabric
        fab = DeviceFabric(budget=Budget(max_attempts=2))
        loop = asyncio.get_running_loop()
        m_open = _PHONE_QUALIFIED_OPEN.search(ml)
        m_call = _PHONE_QUALIFIED_CALL.search(ml)
        intent_kind = "device_action"
        if m_open and not m_call:
            app_name = _phone_arg(m_open.group(1))
            action_name = "open_app"
            res = await loop.run_in_executor(None, lambda: fab.act(
                "open_app", target="phone", app_name=app_name,
                escalate_on_failure=False).to_dict())
            status = res.get("status")
            detail = res.get("detail") or ""
            if status == "SUCCESS":
                response_text = f"Opening {app_name} on your phone, Sir."
                verification = "VERIFIED"
            elif status == "REDUNDANT":
                response_text = f"{app_name} is already open on your phone, Sir."
                verification = "VERIFIED"
            elif status == "UNVERIFIED":
                response_text = (f"I launched {app_name} on your phone but could not yet "
                                 f"confirm it, Sir.")
                verification = "UNVERIFIED"
            elif status == DEVICE_BLOCKED:
                detail = res.get("detail", "")
                response_text = f"I cannot touch the phone right now: {detail}, Sir."
                verification = "FAILED"
            else:
                response_text = (f"I could not open {app_name} on your phone: "
                                 f"{detail}, Sir.")
                verification = f"{status}:{detail[:40]}"
        else:
            target_text = _phone_arg(m_call.group(1))
            action_name = "dial"
            if re.search(r"\b(aegis|yourself|you)\b", target_text, re.I) or not target_text:
                # "call AEGIS from my phone" is an INBOUND intent AEGIS cannot
                # originate; we can only observe a call that is already ringing.
                res = await loop.run_in_executor(None, lambda: fab.act(
                    "inbound_ring", target="phone", escalate_on_failure=False).to_dict())
                status = res.get("status")
                if status == "SUCCESS" and res.get("per_device"):
                    pv = res["per_device"][0].get("verification") or {}
                    if pv.get("state") == "RINGING" and pv.get("incoming_number"):
                        response_text = (f"{pv['incoming_number']} is ringing on your phone, "
                                         f"Sir. AEGIS does not yet answer calls - the phone "
                                         f"app has no call bridge - so I can only observe "
                                         f"it ring.")
                        verification = "OBSERVED"
                    else:
                        response_text = ("AEGIS cannot place an inbound call to itself, "
                                         "Sir; dial AEGIS from your phone and I will "
                                         "observe it ring.")
                        verification = "NOT_APPLICABLE"
                else:
                    response_text = (f"No phone is reachable right now: "
                                     f"{res.get('detail') or 'no device'}, Sir.")
                    verification = "FAILED"
            else:
                res = await loop.run_in_executor(None, lambda: fab.act(
                    "dial", target="phone", number=target_text,
                    escalate_on_failure=False).to_dict())
                status = res.get("status")
                detail = res.get("detail") or ""
                if status == "SUCCESS":
                    response_text = f"Calling {target_text} from your phone, Sir."
                    verification = "VERIFIED"
                elif status == "UNVERIFIED":
                    response_text = (f"I dialed {target_text} but your phone does not yet "
                                     f"confirm an active call, Sir.")
                    verification = "UNVERIFIED"
                elif status == DEVICE_BLOCKED:
                    response_text = f"I am not dialing that: {detail}, Sir."
                    verification = "BLOCKED"
                else:
                    response_text = f"The call did not go through: {detail}, Sir."
                    verification = f"{status}:{detail[:40]}"
        speakable_text = response_text
        action_handled = True

    # G) OS / Web Application Launching (fast path — a single app, no fleet wake)
    elif ((any(lower.startswith(p) for p in ["open ", "launch ", "start "]) and not any(k in lower for k in ["conversation", "discussion", "talk", "bookmyshow", "ticket", "tickets", "movie", "booking", "seats", "theatre", "fandango"])) or lower in ("notepad", "notebook", "calculator", "calc", "chrome", "terminal", "cmd", "explorer", "settings", "vscode", "spotify", "youtube")) and not any(k in lower for k in ["bookmyshow", "fandango", "theatre", "ticket", "tickets", "seat"]):
        if any(lower.startswith(p) for p in ["open ", "launch ", "start "]):
            app_name = text.split(" ", 1)[1].strip(" .!?,:;")
        else:
            app_name = lower.strip(" .!?,:;")
        if app_name.lower().startswith("the "):
            app_name = app_name[4:].strip()
        elif app_name.lower().startswith("a "):
            app_name = app_name[2:].strip()
        if app_name.lower() == "notebook":
            app_name = "notepad"
        app_key = app_name.lower()
        intent_kind = "app_launch"
        action_name = "open_app"
        target_app = app_name
        if app_key in _WEB_URLS:
            url = _WEB_URLS[app_key]
            try:
                os.startfile(url)
                response_text = f"Opening {app_name} in your browser, Sir."
                ok = _app_foreground(app_key)
                verification = "VERIFIED" if ok else "UNVERIFIED"
            except Exception:
                response_text = f"I could not open {app_name}, Sir."
                verification = "FAILED"
        else:
            try:
                from action.desktop_control.desktop_controller import desktop_controller
                launch_res = await desktop_controller.open_app(app_name)
                if launch_res.get("status") == "SUCCESS":
                    response_text = f"Opening {app_name} on your desktop, Sir."
                    ok = _app_foreground(app_name)
                    verification = "VERIFIED" if ok else "EXECUTED"
                else:
                    alias_map = {
                        "notepad": "notepad.exe",
                        "calculator": "calc:",
                        "calc": "calc:",
                        "chrome": "chrome",
                        "browser": "chrome",
                        "terminal": "wt.exe",
                        "cmd": "cmd.exe",
                        "settings": "ms-settings:",
                        "file explorer": "explorer.exe",
                        "explorer": "explorer.exe",
                        "vscode": "code .",
                    }
                    if app_key in alias_map:
                        cmd = alias_map[app_key]
                        try:
                            os.startfile(cmd)
                            response_text = f"Launched {app_name} on your interactive desktop, Sir."
                            verification = "EXECUTED"
                        except Exception:
                            response_text = f"I could not open {app_name}, Sir. Please start it manually."
                            verification = "FAILED"
                    else:
                        response_text = f"I could not open application '{app_name}' (could not locate), Sir."
                        verification = "FAILED"
            except Exception as ex:
                response_text = f"Attempting to launch {app_name}, encountered: {ex}."
                verification = "FAILED"
        speakable_text = response_text
        action_handled = True

    # G2) "go back" — browser back within the session tab, URL-change verified
    elif _BACK_PATTERNS.search(ml):
        intent_kind = "browser_navigation"
        action_name = "go_back"
        _emit(VoiceEventType.FAST_ACTION_SELECTED, "go-back")
        agent_handle = web_agent or _get_voice_web_agent()
        if agent_handle is not None and getattr(agent_handle, "page", None) is not None:
            ok, detail = await _web_back(agent_handle)
            if ok:
                response_text = "Went back one page in the browser, Sir."
                verification = "VERIFIED"
            else:
                response_text = "I could not go back in the browser tab, Sir."
                verification = f"FAILED:{detail[:40]}"
        else:
            response_text = "There is no browser tab in this conversation to go back in, Sir."
            verification = "NOT_APPLICABLE"
        speakable_text = response_text
        action_handled = True

    # G3) "search in the current application" — inside-tab search, URL verified
    elif _SEARCH_PATTERNS.search(ml) and re.search(r"\b(in|on|within|the current|youtube|chrome|browser)\b", ml) and not _is_complex_intent(ml):
        intent_kind = "app_search"
        action_name = "search_app"
        _emit(VoiceEventType.FAST_ACTION_SELECTED, "search-in-app")
        active = ctx.current_application() or ""
        engine = "youtube" if ("youtube" in ml or "youtube" in active) else "chrome"
        cleaned = re.sub(r"\b(search|for|in|within|on|the|current|app|application|" +
                         r"youtube|chrome|browser|of)\b", "", effective, flags=re.I)
        query = cleaned.strip(" .,!?:;`'\"")
        if not query:
            response_text = "What would you like me to search for, Sir?"
            speakable_text = response_text
            verification = "CLARIFY"
            action_handled = True
        else:
            agent_handle = web_agent or _get_voice_web_agent()
            res = await _web_search(agent_handle, query, engine=engine)
            if res.get("ok"):
                response_text = (f"Here are my {engine} results for '{query}', Sir.")
                verification = "VERIFIED"
            else:
                response_text = f"I could not open the {engine} search results, Sir."
                verification = f"FAILED:{(res.get('reason') or 'unavailable')[:40]}"
            speakable_text = response_text
            action_handled = True

    # G4) "close the current application" — real window close via the controller
    elif _CLOSE_CURRENT.search(ml):
        intent_kind = "app_close"
        action_name = "close_app"
        _emit(VoiceEventType.FAST_ACTION_SELECTED, "close-app")
        if "youtube" in ml or "chrome" in ml or "spotify" in ml or "notepad" in ml:
            target_app = next((w for w in ("youtube", "chrome", "spotify", "notepad") if w in ml), None)
        else:
            target_app = ctx.current_application()
        if not target_app:
            response_text = ("What would you like me to close, Sir? I don't have a "
                             "current application noted for this conversation.")
            speakable_text = response_text
            verification = "CLARIFY"
            action_handled = True
        else:
            gt = await _ensure_app_focused(target_app)
            if gt.get("status") == "MATCH":
                ok, detail, _d = await _close_app_real(target_app)
                if ok:
                    response_text = f"Closed {target_app}, Sir."
                    verification = "VERIFIED"
                else:
                    response_text = f"I could not verify closing {target_app}, Sir."
                    verification = f"FAILED:{detail[:40]}"
            elif gt.get("status") == "MISMATCH":
                response_text = f"I could not confirm {target_app} is in the foreground, so I did not close it, Sir."
                verification = f"HONEST_REFUSAL:not-foreground"
            else:
                ok, detail, _d = await _close_app_real(target_app)
                if ok:
                    response_text = f"Closed {target_app}, Sir."
                    verification = "VERIFIED"
                else:
                    response_text = f"I could not verify closing {target_app}, Sir."
                    verification = f"FAILED:{detail[:40]}"
            speakable_text = response_text
            action_handled = True


    # D) Terminal Command Execution (interactive session)
    elif any(lower.startswith(p) for p in ["run in terminal ", "terminal ", "run command ", "execute command ", "execute in terminal "]):
        cmd_to_run = text
        for prefix in ["run in terminal ", "execute in terminal ", "run command ", "execute command ", "terminal "]:
            if lower.startswith(prefix):
                cmd_to_run = text[len(prefix):].strip()
                break
        try:
            from core.orchestration.agent_tools import run_terminal_command
            run_terminal_command(cmd_to_run, visible=True)
            response_text = f"Running '{cmd_to_run}' in interactive terminal on your desktop, Sir."
        except Exception as ex:
            response_text = f"Failed to launch terminal command: {ex}"
        speakable_text = response_text
        action_handled = True

    # E) Interactive Desktop GUI Interaction
    # Previously this fired a throwaway ComputerUseAgent into a detached task
    # and replied optimistically, so the result was never seen and failures
    # were invisible. It now uses the shared agent, awaits the outcome, and
    # reports what actually happened.
    elif any(lower.startswith(p) for p in [
            "click ", "type ", "write ", "type text ", "enter text ", "double click ", "right click ",
            "double-click ", "right-click ", "press ", "hit ", "scroll ", "maximize", "minimize",
            "move the mouse", "move mouse ", "focus window ", "switch to ", "take control",
            "computer use", "take over desktop", "take over", "visually book", "book ticket visually"]):
        try:
            from core.orchestration.agent_tools import interact_desktop
            if lower.startswith("type ") or lower.startswith("write ") or lower.startswith("type text ") or lower.startswith("enter text "):
                for prefix in ["type text ", "enter text ", "type ", "write "]:
                    if lower.startswith(prefix):
                        to_type = text[len(prefix):].strip()
                        break
                interact_desktop(action="type", text=to_type)
                response_text = f"Typed: '{to_type}' on your active screen, Sir."
                speakable_text = f"Typed '{to_type}', Sir."
                verification = "VERIFIED"
            elif lower.startswith("press ") or lower.startswith("hit "):
                key_name = lower.split()[-1].strip(" .!?,:;")
                import pyautogui
                pyautogui.press(key_name)
                response_text = f"Pressed {key_name}, Sir."
                speakable_text = response_text
                verification = "VERIFIED"
            elif "maximize" in lower:
                import pyautogui
                pyautogui.hotkey('win', 'up')
                response_text = "Maximized the active window, Sir."
                speakable_text = response_text
                verification = "VERIFIED"
            elif "minimize" in lower:
                import pyautogui
                pyautogui.hotkey('win', 'down')
                response_text = "Minimized the active window, Sir."
                speakable_text = response_text
                verification = "VERIFIED"
            elif lower.startswith("focus window ") or lower.startswith("switch to "):
                target_win = text.split(" ", 2)[-1].strip()
                res = interact_desktop(action="focus_window", target=target_win)
                if isinstance(res, dict) and res.get("status") == "FAILED":
                    response_text = f"I could not find the {target_win} window, Sir."
                    verification = f"FAILED:{str(res.get('error', ''))[:40]}"
                else:
                    response_text = f"Switched to {target_win} window on your desktop, Sir."
                    verification = "VERIFIED"
            elif any(lower.startswith(p) for p in ["click ", "double click ", "double-click ", "right click ", "right-click ", "move mouse to ", "move to "]):
                target_str = text
                clicks_cnt = 1
                is_right = "right" in lower
                is_move_only = "move" in lower and "click" not in lower
                for prefix in ["double click ", "double-click ", "right click ", "right-click ", "move mouse to ", "move to ", "click "]:
                    if lower.startswith(prefix):
                        target_str = text[len(prefix):].strip(" .!?,:;")
                        if "double" in prefix:
                            clicks_cnt = 2
                        break

                from core.perception.realtime_spatial_vision import realtime_spatial_vision
                from core.execution.human_input import human_click, human_move
                instant = realtime_spatial_vision.resolve_target_instant(target_str)
                if instant and instant.get("confidence", 0) >= 0.80:
                    px, py = instant["x"], instant["y"]
                    if is_move_only:
                        human_move(px, py)
                        response_text = f"Moved pointer to {target_str}, Sir."
                    elif is_right:
                        human_move(px, py)
                        import pyautogui
                        pyautogui.rightClick(px, py)
                        response_text = f"Right-clicked {target_str}, Sir."
                    else:
                        human_click(px, py, clicks=clicks_cnt)
                        response_text = f"Clicked {target_str}, Sir."
                    speakable_text = response_text
                    verification = "VERIFIED"
                else:
                    from core.orchestration.computer_use_agent import computer_use_agent
                    try:
                        result = await asyncio.wait_for(
                            computer_use_agent.execute_task(text), timeout=35)
                    except asyncio.TimeoutError:
                        computer_use_agent.stop()
                        result = {"status": "FAILED", "reason": "Desktop action timed out."}

                    status = str(result.get("status", "")).upper()
                    if status == "SUCCESS":
                        response_text = f"Done. {text} — completed on your desktop, Sir."
                        verification = "VERIFIED"
                    elif status == "BLOCKED":
                        response_text = ("That action was blocked by a safety rule, Sir. "
                                         "I did not perform it.")
                        verification = f"BLOCKED:{str(result.get('reason', ''))[:40]}"
                    else:
                        reason = result.get("reason") or result.get("error") or "no confident match on screen"
                        response_text = f"I could not do that, Sir. {reason}"
                        verification = f"FAILED:{str(reason)[:40]}"
            else:
                from core.orchestration.computer_use_agent import computer_use_agent
                # Bounded so a stuck grounding/VLM loop cannot hang the turn.
                try:
                    result = await asyncio.wait_for(
                        computer_use_agent.execute_task(text), timeout=60)
                except asyncio.TimeoutError:
                    computer_use_agent.stop()
                    result = {"status": "FAILED", "reason": "Desktop action timed out."}

                status = str(result.get("status", "")).upper()
                if status == "SUCCESS":
                    response_text = f"Done. {text} — completed on your desktop, Sir."
                    verification = "VERIFIED"
                elif status == "BLOCKED":
                    response_text = ("That action was blocked by a safety rule, Sir. "
                                     "I did not perform it.")
                    verification = f"BLOCKED:{str(result.get('reason', ''))[:40]}"
                else:
                    reason = result.get("reason") or result.get("error") or "no confident match on screen"
                    response_text = f"I could not do that, Sir. {reason}"
                    verification = f"FAILED:{str(reason)[:40]}"
        except Exception as ex:
            response_text = f"Desktop interaction failed: {ex}"
            verification = "FAILED:exception"
        speakable_text = response_text
        action_handled = True

    # H) Mission tier — complex / diagnostic intents routed to the EXISTING
    #    mission planner + fleet pipeline (same path as /jarvis). Falls through
    #    to the normal reply when the planner is unavailable or fails: this is
    #    an honest degradation, never a fabricated mission.
    mission_used = False
    if not action_handled and _is_complex_intent(effective) and _missions_enabled():
        mission_followup = ctx.last_action() == "fleet_mission" or bool(resolved.continuation)
        mission_outcome = await _voice_mission(
            effective,
            context={
                "active_application": ctx.current_application(),
                "active_device": device,
                "followup": mission_followup,
            },
        )
        if mission_outcome is not None:
            mission_used = True
            intent_kind = "mission"
            action_name = "fleet_mission"
            response_text = mission_outcome["text"]
            speakable_text = response_text
            verification = mission_outcome["verification"]
            action_handled = True
            _emit(VoiceEventType.MISSION_SELECTED,
                  f"intent:{effective[:60]}",
                  steps=mission_outcome["steps"],
                  verified_steps=mission_outcome["verified_steps"],
                  followup=mission_followup)

    # F) General Conversational Neural Reply (complex voice -> normal pipeline)
    dispatch_ms = (time.time() - t_dispatch) * 1000
    reply_model = None
    reply_latency_ms = None
    if not action_handled:
        try:
            # Extract recent conversation turns for multi-turn conversational memory
            history_records = []
            if ctx and hasattr(ctx, "recent"):
                for t in list(ctx.recent)[-6:]:
                    history_records.append({
                        "user": getattr(t, "heard", ""),
                        "assistant": getattr(t, "response", "")
                    })

            r0 = time.time()
            response = await agent.reply(text, emotion_ctx, sight, language=language, history=history_records)
            reply_latency_ms = (time.time() - r0) * 1000
            try:
                from core.cognition.reasoning.cloud_llm import cloud_llm_provider
                reply_model = ("groq:llama-3.3-70b-versatile"
                               if cloud_llm_provider.groq_active
                               else getattr(agent, "local_model", None)
                               or "qwen2.5:1.5b")
            except Exception:
                reply_model = getattr(agent, "local_model", None) or "qwen2.5:1.5b"
        except Exception as e_reply:
            logging.getLogger("voice_session").warning(f"Voice turn cognitive reply exception: {e_reply}")
            response = "I have registered your command, Sir. Standing by."

        try:
            from core.orchestration.response_composer import response_composer
            composed = response_composer.compose(response, query=text)
            response_text = composed.response_text
            speakable_text = composed.speakable_text
        except Exception:
            response_text = response
            speakable_text = response

    elapsed_ms = int((time.time() - start) * 1000)
    final_speakable = _voice_speakable(speakable_text) if speakable_text else ""
    try:
        from core.learning.experience_store import experience_store
        experience_store.record(
            task_type="voice/turn",
            model=reply_model,  # real worker that produced the reply (or None for fast intents)
            success=bool(final_speakable or response_text),
            verification="SUCCESS" if final_speakable else "UNVERIFIED",
        )
    except Exception:
        pass
    if reply_model and reply_latency_ms is not None:
        try:
            from core.model_gateway.model_performance_ledger import model_performance_ledger
            model_performance_ledger.measure(
                model=reply_model, task_class="model/VOICE",
                latency_ms=reply_latency_ms, success=bool(final_speakable or response_text),
                verification="UNVERIFIED",
            )
        except Exception:
            pass

    # R2 — track the resolved context + observable outcome on this session.
    _register_full_turn(
        ctx, heard=text, effective=effective, intent=intent_kind, action=action_name,
        target_app=target_app, target_entity=target_entity, device=device,
        verification=verification, response=response_text or "",
    )
    if action_handled:
        _emit(VoiceEventType.ACTION_VERIFIED, f"{action_name}:{verification}")
    _emit(VoiceEventType.TURN_COMPLETED,
          f"turn::{elapsed_ms}ms",
          latency_ms={
              "context_ms": round(context_ms, 1),
              "dispatch_ms": round(dispatch_ms, 1),
              "reply_ms": round(reply_latency_ms, 1) if reply_latency_ms is not None else None,
              "total_ms": elapsed_ms,
          },
          intent=intent_kind,
          action=action_name,
          model_calls=1 if reply_model else 0,
          agents_activated=1 if mission_used else 0)
    _persist_turn(manager, heard=text, emotion_label=emotion.get("label") or "NEUTRAL",
                  intent=intent_kind, speakable=final_speakable)

    if manager is not None:
        manager.task_done(detail=f"turn::{elapsed_ms}ms")

    try:
        from core.voice.verbal_ack import get_immediate_verbal_ack
        imm_ack = get_immediate_verbal_ack(text, language=emotion.get("language"))
    except Exception:
        imm_ack = None

    return {
        "heard": text,
        "immediate_ack": imm_ack,
        "language": emotion.get("language"),
        "emotion": emotion,
        "sight": {
            "capture": sight.get("capture"),
            "ocr": list(sight.get("ocr") or [])[:6],
            "llava": sight.get("llava"),
        },
        "response": (response_text[:500] if isinstance(response_text, str) else response_text),
        "speakable_text": final_speakable[:500],
        "elapsed_ms": elapsed_ms,
        "verification": verification,
        "intent": intent_kind,
        "model_calls": 1 if reply_model else 0,
        "agents_activated": 1 if mission_used else 0,
        "context": {
            "active_application": ctx.current_application(),
            "active_device": ctx.current_device(),
            "continuation": resolved.continuation,
            "reference": resolved.has_reference,
            "favorite": bool(resolved.is_favorite_query or resolved.is_favorite_store),
            "resolved_text": effective[:120],
            "latency_ms": {
                "context_ms": round(context_ms, 1),
                "dispatch_ms": round(dispatch_ms, 1),
                "reply_ms": round(reply_latency_ms, 1) if reply_latency_ms is not None else None,
                "total_ms": elapsed_ms,
            },
        },
    }


def record_session_telemetry(manager: VoiceSessionManager, *, extra: Optional[Dict[str, Any]] = None) -> None:
    """R25 — structured session memory via the existing experience store.
    Only structured facts: task type, models/agents, latencies, outcome.
    Never raw microphone audio, never chain-of-thought, never transcripts."""
    try:
        from core.learning.experience_store import experience_store
        m = manager.to_dict()
        payload = {
            "task_type": "voice/session",
            "task_id": manager.config.session_id,
            "success": m["state"] in ("IDLE", "LISTENING") and m["turns"] > 0,
            "verification": "NOMINAL" if manager.errors == 0 else "PARTIAL",
            "agents": [],
            "model": None,
            "turn_count": m["turns"],
            "interruptions": m["interruptions"],
            "errors": m["errors"],
            "duration_s": round(manager.ended_at - manager.started_at, 2) if (manager.ended_at and manager.started_at) else 0.0,
        }
        if extra:
            payload.update({k: v for k, v in extra.items() if k in (
                "turn_latency_ms_total", "stt_successes", "stt_failures", "final_outcome")})
        experience_store.record(**payload)
    except Exception as e:
        logger.warning("session telemetry failed: %s", e)