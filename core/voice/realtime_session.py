"""
Phase S - S7 / R27: OpenAI Realtime voice transport.

SOVEREIGN MODE (AEGIS_LOCAL_FIRST=true):
  This entire module is DISABLED. Zero OpenAI calls are made.
  Voice pipeline uses: Whisper (local STT) → Ollama → Kokoro (local TTS)
  To re-enable cloud realtime: set AEGIS_LOCAL_FIRST=false in .env AND
  provide a valid OPENAI_API_KEY.

When AEGIS_LOCAL_FIRST=false:
  - Opt-in via AEGIS_VOICE_TRANSPORT=realtime
  - Default transport stays: mic → VAD → STT → /voice/command → composer → TTS
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("realtime_session")

REALTIME_MODEL = os.getenv("AEGIS_REALTIME_MODEL", "gpt-realtime")
REALTIME_VOICE = os.getenv("AEGIS_REALTIME_VOICE", "alloy")
REALTIME_URL = os.getenv(
    "AEGIS_REALTIME_URL", "wss://api.openai.com/v1/realtime")

#: The GA endpoint. POST /v1/realtime/sessions is deprecated; ephemeral browser
#: credentials now come from /realtime/client_secrets.
REALTIME_SECRETS_URL = os.getenv(
    "AEGIS_REALTIME_SECRETS_URL",
    "https://api.openai.com/v1/realtime/client_secrets")

#: Ephemeral secrets are single use and short lived; cache only briefly so a
#: reconnect does not mint a new secret on every retry storm.
_TOKEN_TTL_S = float(os.getenv("AEGIS_REALTIME_TOKEN_TTL", "45"))

#: Shared by the mint request and the transport, so the two can never drift
#: into disagreeing about what the assistant is allowed to claim.
DEFAULT_INSTRUCTIONS = (
    "You are AEGIS, a concise voice assistant. One short sentence at a time. "
    "Never invent the result of an action you did not perform.")


def _redact(text: str) -> str:
    """Strip anything that looks like an API key or client secret."""
    if not text:
        return text
    out = text
    for env in ("OPENAI_API_KEY", "OPENAI_REALTIME_API_KEY"):
        secret = os.getenv(env)
        if secret and len(secret) > 8 and secret in out:
            out = out.replace(secret, "[REDACTED]")
    return out


@dataclass
class RealtimeSession:
    """One realtime conversation session."""

    session_id: str
    client_secret: str
    expires_at: float
    model: str = REALTIME_MODEL
    voice: str = REALTIME_VOICE

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    def ttl_s(self) -> float:
        return max(0.0, round(self.expires_at - time.time(), 1))

    def to_client(self) -> Dict[str, Any]:
        """The ONLY payload that may cross to a browser.

        Contains the ephemeral secret and the endpoint. It never contains the
        server-side API key, and it is the reason the minting step has to
        happen on the server.
        """
        return {
            "session_id": self.session_id,
            "client_secret": self.client_secret,
            "expires_in_s": self.ttl_s(),
            "model": self.model,
            "voice": self.voice,
            "url": REALTIME_URL,
        }


def server_key() -> Optional[str]:
    # SOVEREIGN GATE: When AEGIS_LOCAL_FIRST=true, OpenAI Realtime is disabled.
    # Voice runs through: Local Whisper STT → Ollama → Kokoro TTS (zero cloud).
    if os.getenv("AEGIS_LOCAL_FIRST", "true").lower() == "true":
        return None
    key = os.getenv("AEGIS_REALTIME_API_KEY") or os.getenv("OPENAI_API_KEY")
    return key.strip() if key and key.strip() else None


def _mint(
    *,
    instructions: str = "",
    http_post: Optional[Callable[..., Any]] = None,
    now: Optional[float] = None,
) -> tuple:
    """Mint an ephemeral client secret. Never raises.

    Returns a ``(status, detail, session|None)`` triple so the public wrappers
    can stay JSON-safe: a route must be able to serialise the result directly.
    ``http_post`` is injectable so this is testable with no network and no key.
    """
    now = time.time() if now is None else now
    if not transport_enabled():
        if os.getenv("AEGIS_LOCAL_FIRST", "true").lower() == "true":
            return ("unavailable", "realtime disabled by local-first mode", None)
        return ("unavailable", "realtime transport is not explicitly enabled", None)

    key = server_key()
    if not key:
        return ("unavailable", "no server-side realtime key configured", None)

    if http_post is None:
        try:
            import httpx  # imported lazily: it is optional
        except Exception:
            return ("unavailable", "httpx is not installed; cannot mint a session", None)
        http_post = httpx.post

    body = {
        "session": {
            "type": "realtime",
            "model": REALTIME_MODEL,
            "instructions": instructions or DEFAULT_INSTRUCTIONS,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 500,
                    },
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "voice": REALTIME_VOICE,
                },
            },
        },
    }
    headers = {"Authorization": f"Bearer {key}",
               "Content-Type": "application/json"}
    safety_id = os.getenv("AEGIS_SAFETY_IDENTIFIER")
    if safety_id:
        # The docs ask for this server-side: the safety id is bound to the
        # ephemeral token so the browser does not have to send it.
        headers["OpenAI-Safety-Identifier"] = safety_id
    try:
        resp = http_post(REALTIME_SECRETS_URL,
                         headers=headers, json=body, timeout=10.0)
        data = resp.json()
    except Exception as e:
        return ("unavailable", _redact(f"session mint failed: {e}"), None)

    if (data or {}).get("error"):
        return ("unavailable", _redact(f"realtime rejected the request: {data}"),
                None)

    data = data or {}
    # GA shape: { value, expires_at, session: { id, ... } }. The deprecated
    # /realtime/sessions shape nested the secret under client_secret.
    secret = data.get("value")
    expires_at = data.get("expires_at")
    if not secret:
        nested = data.get("client_secret") or {}
        secret = nested.get("value") if isinstance(nested, dict) else nested
        expires_in = float((nested or {}).get("expires_in", 60)
                           if isinstance(nested, dict) else 60) if secret else 60
    if not secret:
        return ("unavailable", _redact(f"no client_secret in response: {data}"), None)

    session_cfg = data.get("session") or {}
    if not expires_at:
        expires_at = now + float(expires_in)
    session = RealtimeSession(
        session_id=str(session_cfg.get("id") or data.get("id")
                       or f"realtime_{int(now)}"),
        client_secret=secret,
        expires_at=float(expires_at),
    )
    return ("ok", "ephemeral session minted", session)


class RealtimeSessionCache:
    """Short-lived cache so a reconnect storm cannot hammer the mint endpoint."""

    def __init__(self, ttl_s: float = _TOKEN_TTL_S):
        self.ttl_s = ttl_s
        self._sessions: Dict[str, RealtimeSession] = {}

    def get(self, key: str) -> Optional[RealtimeSession]:
        s = self._sessions.get(key)
        if s is None:
            return None
        if s.expired or s.ttl_s() < 5:
            self._sessions.pop(key, None)
            return None
        return s

    def put(self, key: str, session: RealtimeSession) -> RealtimeSession:
        self._sessions[key] = session
        return session

    def clear(self) -> None:
        self._sessions.clear()


_cache = RealtimeSessionCache()


def _envelope(status: str, detail: str,
              session: Optional[RealtimeSession]) -> Dict[str, Any]:
    """A fully JSON-safe result, so a route can return it verbatim."""
    return {"status": status, "detail": detail,
            "client": session.to_client() if session is not None else None}


def mint_session(*, instructions: str = "",
                 http_post: Optional[Callable[..., Any]] = None,
                 now: Optional[float] = None) -> Dict[str, Any]:
    """Mint one ephemeral session and return a JSON-safe envelope."""
    status, detail, session = _mint(instructions=instructions,
                                    http_post=http_post, now=now)
    return _envelope(status, detail, session)


def get_client_session(session_key: str = "default", *,
                        instructions: str = "",
                        http_post: Optional[Callable[..., Any]] = None
                        ) -> Dict[str, Any]:
    """Cache-first mint, for the HTTP route that serves the browser."""
    hit = _cache.get(session_key)
    if hit is not None:
        return _envelope("ok", "cached", hit)
    status, detail, session = _mint(instructions=instructions,
                                    http_post=http_post)
    if session is not None:
        _cache.put(session_key, session)
    return _envelope(status, detail, session)


def transport_enabled() -> bool:
    transport = os.getenv("AEGIS_VOICE_TRANSPORT", "local").strip().lower()
    if transport not in ("realtime", "openai", "openai-realtime"):
        return False
    # Realtime is a cloud transport. It must be explicitly opted out of
    # sovereign/local-first mode as well as explicitly selected as a
    # transport. Missing settings therefore remain private-by-default.
    return os.getenv("AEGIS_LOCAL_FIRST", "true").lower() != "true"


# -- websocket session --------------------------------------------------------

def build_session_update(instructions: str = "", voice: str = REALTIME_VOICE,
                         modalities: Optional[list] = None) -> Dict[str, Any]:
    """The `session.update` payload, kept pure so it is unit-testable."""
    return {
        "type": "session.update",
        "session": {
            "modalities": modalities or [{"type": "audio", "output": "audio"}],
            "voice": voice,
            "instructions": instructions or DEFAULT_INSTRUCTIONS,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.5,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 500,
            },
        },
    }


def serialize(event: Dict[str, Any]) -> str:
    return json.dumps(event, separators=(",", ":"))


@dataclass
class RealtimeTurn:
    transcript: str = ""
    audio: bytes = b""
    usage: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"transcript": self.transcript,
                "audio_bytes": len(self.audio),
                "usage": self.usage}


class RealtimeTransport:
    """Minimal realtime websocket client.

    Microphone capture and VAD stay where they already are (the desktop
    engine); this only owns the transport, so switching to Realtime does not
    duplicate the audio front end.
    """

    def __init__(self, session: RealtimeSession, *,
                 instructions: str = "", connect=None,
                 on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
                 api_key: Optional[str] = None):
        self.session = session
        self.instructions = instructions
        self.on_event = on_event
        #: The realtime websocket authenticates with the EPHEMERAL secret.
        #: The long-lived key is never used here and never passed in.
        self._api_key = api_key
        self._connect = connect
        self._ws = None
        self.turns: list[RealtimeTurn] = []
        self._text = []
        self._audio = bytearray()
        self._cancelled = False

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.session.client_secret}",
            "OpenAI-Beta": "realtime=v1",
        }

    async def __aenter__(self) -> "RealtimeTransport":
        if self._connect is None:
            try:
                import websockets  # optional dependency
            except Exception as e:
                raise RuntimeError(
                    "websockets is not installed; realtime transport "
                    f"unavailable ({e})") from e
            self._connect = websockets.connect
        url = f"{REALTIME_URL}?model={self.session.model}"
        self._ws = await self._connect(url, extra_headers=self.headers)
        await self._send(build_session_update(self.instructions,
                                              voice=self.session.voice))
        return self

    async def __aexit__(self, *exc) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def _send(self, event: Dict[str, Any]) -> None:
        await self._ws.send(serialize(event))

    async def send_audio(self, pcm16: bytes) -> None:
        import base64
        await self._send({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(pcm16).decode("ascii"),
        })

    async def commit_and_respond(self) -> None:
        await self._send({"type": "input_audio_buffer.commit"})
        await self._send({"type": "response.create"})

    async def cancel(self) -> None:
        """Cancel the current server response for barge-in or operator stop."""
        self._cancelled = True
        if self._ws is not None:
            await self._send({"type": "response.cancel"})

    async def pump(self, timeout_s: float = 30.0) -> RealtimeTurn:
        """Drain server events until the response completes."""
        self._text, self._audio = [], bytearray()
        import base64
        while True:
            raw = await asyncio_wait(self._ws, timeout_s)
            event = json.loads(raw)
            t = event.get("type", "")
            if t.endswith("transcript.delta") or t == "response.audio_transcript.delta":
                self._text.append(event.get("delta", ""))
            elif t == "response.audio.delta":
                self._audio.extend(base64.b64decode(event.get("delta", "")))
            elif t == "response.done":
                break
            if self.on_event:
                self.on_event(event)
        turn = RealtimeTurn(transcript="".join(self._text),
                            audio=bytes(self._audio),
                            usage=(event.get("response") or {}).get("usage") or {})
        self.turns.append(turn)
        return turn


async def asyncio_wait(ws, timeout_s: float):
    import asyncio
    return await asyncio.wait_for(ws.recv(), timeout=timeout_s)
