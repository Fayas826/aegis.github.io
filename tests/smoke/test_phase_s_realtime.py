"""
Phase S - S7 / R27: realtime transport, with no network and no API key.

The properties that matter are security properties, so they are tested
directly: the long-lived key must never reach the client payload, an absent
key must be reported as unavailable rather than faked, and the transport must
authenticate with the ephemeral secret.
"""

from __future__ import annotations

import base64
import json

import pytest

from core.voice import realtime_session as rt


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


MINT_OK = {
    "value": "ephemeral_sk-xyz",
    "expires_at": 9999999999,
    "session": {"id": "sess_abc", "type": "realtime"},
}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("AEGIS_REALTIME_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-server-side-SECRET")
    # Realtime tests model the explicit production opt-in. The application
    # remains local-first when these settings are absent.
    monkeypatch.setenv("AEGIS_LOCAL_FIRST", "false")
    monkeypatch.setenv("AEGIS_VOICE_TRANSPORT", "realtime")
    rt._cache.clear()
    yield
    rt._cache.clear()


# -- key handling -------------------------------------------------------------

def test_no_key_is_reported_unavailable_not_faked(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    res = rt.mint_session()
    assert res["status"] == "unavailable"
    assert res["client"] is None
    assert "key" in res["detail"].lower()


def test_realtime_requires_explicit_transport_opt_in(monkeypatch):
    monkeypatch.delenv("AEGIS_VOICE_TRANSPORT", raising=False)
    res = rt.mint_session(http_post=lambda *a, **k: FakeResponse(MINT_OK))
    assert res["status"] == "unavailable"
    assert "explicitly enabled" in res["detail"]


def test_local_first_blocks_realtime_even_with_transport_and_key(monkeypatch):
    monkeypatch.setenv("AEGIS_LOCAL_FIRST", "true")
    res = rt.mint_session(http_post=lambda *a, **k: FakeResponse(MINT_OK))
    assert res["status"] == "unavailable"
    assert "local-first" in res["detail"]


def test_server_key_never_appears_in_the_client_payload(monkeypatch):
    res = rt.mint_session(http_post=lambda *a, **k: FakeResponse(MINT_OK))
    assert res["status"] == "ok"
    blob = json.dumps(res["client"])
    assert "sk-server-side-SECRET" not in blob
    assert res["client"]["client_secret"] == "ephemeral_sk-xyz"


def test_transport_authenticates_with_the_ephemeral_secret(monkeypatch):
    res = rt.mint_session(http_post=lambda *a, **k: FakeResponse(MINT_OK))
    tr = rt.RealtimeTransport(rt.RealtimeSession(
        res["client"]["session_id"], res["client"]["client_secret"], 9e9))
    assert tr.headers["Authorization"] == "Bearer ephemeral_sk-xyz"
    assert "sk-server-side-SECRET" not in json.dumps(tr.headers)


def test_realtime_http_call_uses_the_server_key_and_never_returns_it(monkeypatch):
    seen = {}

    def post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["auth"] = headers["Authorization"]
        return FakeResponse(MINT_OK)

    res = rt.mint_session(http_post=post)
    assert seen["url"].endswith("/v1/realtime/client_secrets"), "the deprecated /realtime/sessions endpoint is gone"
    assert seen["auth"] == "Bearer sk-server-side-SECRET"
    assert "sk-server-side-SECRET" not in json.dumps(res), "the whole mint result must be JSON-safe for the route"


def test_error_detail_is_redacted(monkeypatch):
    class Boom:
        def json(self):
            raise RuntimeError("401 for Bearer sk-server-side-SECRET")

    res = rt.mint_session(http_post=lambda *a, **k: Boom())
    assert res["status"] == "unavailable"
    assert "sk-server-side-SECRET" not in res["detail"]
    assert "[REDACTED]" in res["detail"]


def test_response_without_a_secret_is_unavailable():
    res = rt.mint_session(http_post=lambda *a, **k: FakeResponse({"id": "x"}))
    assert res["status"] == "unavailable"
    assert res["client"] is None


# -- caching ------------------------------------------------------------------

def test_second_call_reuses_the_cached_secret():
    calls = []

    def post(*a, **k):
        calls.append(1)
        return FakeResponse(MINT_OK)

    a = rt.get_client_session("u1", http_post=post)
    b = rt.get_client_session("u1", http_post=post)
    assert len(calls) == 1, "a reconnect storm re-minted a secret"
    assert a["client"]["client_secret"] == b["client"]["client_secret"]


def test_different_sessions_do_not_share_a_secret():
    def post(*a, **k):
        return FakeResponse(MINT_OK)

    rt.get_client_session("u1", http_post=post)
    rt.get_client_session("u2", http_post=post)
    assert len(rt._cache._sessions) == 2, "sessions must not share a cache slot"


def test_expired_secret_is_not_reused(monkeypatch):
    monkeypatch.setattr(rt.time, "time", lambda: 1000.0)
    res = rt.mint_session(http_post=lambda *a, **k: FakeResponse({"value": "v", "expires_at": 1001, "session": {"id": "s"}}))
    monkeypatch.setattr(rt.time, "time", lambda: 1005.0)
    calls = []

    def post(*a, **k):
        calls.append(1)
        return FakeResponse(MINT_OK)

    rt.get_client_session("u1", http_post=post)
    assert calls == [1], "an expired secret was handed out"


# -- opt-in -------------------------------------------------------------------

def test_realtime_is_off_by_default(monkeypatch):
    monkeypatch.delenv("AEGIS_VOICE_TRANSPORT", raising=False)
    assert rt.transport_enabled() is False


def test_realtime_can_be_enabled(monkeypatch):
    monkeypatch.setenv("AEGIS_VOICE_TRANSPORT", "realtime")
    assert rt.transport_enabled() is True


# -- protocol payloads --------------------------------------------------------

def test_session_update_requests_server_vad_and_audio():
    ev = rt.build_session_update("be brief")
    assert ev["type"] == "session.update"
    s = ev["session"]
    assert s["turn_detection"]["type"] == "server_vad"
    assert s["input_audio_format"] == "pcm16"
    assert s["voice"]
    assert "be brief" in s["instructions"]


def test_default_instructions_forbid_inventing_results():
    text = rt.build_session_update()["session"]["instructions"].lower()
    assert "invent" in text


def test_audio_is_sent_as_base64():
    pcm = b"\x01\x02\x03\x04"
    captured = {}

    class FakeWS:
        async def send(self, s):
            captured["s"] = json.loads(s)

    import asyncio

    async def go():
        tr = rt.RealtimeTransport(rt.RealtimeSession("s", "k", 9e9))
        tr._ws = FakeWS()
        await tr.send_audio(pcm)

    asyncio.run(go())
    ev = captured["s"]
    assert ev["type"] == "input_audio_buffer.append"
    assert base64.b64decode(ev["audio"]) == pcm


def test_pump_collects_transcript_and_audio():
    events = [
        {"type": "response.audio_transcript.delta", "delta": "hello "},
        {"type": "response.audio_transcript.delta", "delta": "there"},
        {"type": "response.audio.delta",
         "delta": base64.b64encode(b"\xaa\xbb").decode()},
        {"type": "response.done", "response": {"usage": {"total_tokens": 12}}},
    ]

    class FakeWS:
        def __init__(self):
            self.i = 0

        async def recv(self):
            ev = events[self.i]
            self.i += 1
            return json.dumps(ev)

    import asyncio

    async def go():
        tr = rt.RealtimeTransport(rt.RealtimeSession("s", "k", 9e9))
        tr._ws = FakeWS()
        return await tr.pump(timeout_s=5)

    turn = asyncio.run(go())
    assert turn.transcript == "hello there"
    assert turn.audio == b"\xaa\xbb"
    assert turn.usage["total_tokens"] == 12
    assert turn.to_dict()["audio_bytes"] == 2


def test_transport_uses_only_the_ephemeral_secret_in_its_url_and_headers():
    tr = rt.RealtimeTransport(rt.RealtimeSession("s", "eph-only", 9e9))
    assert "eph-only" in tr.headers["Authorization"]
    assert "Bearer eph-only" == tr.headers["Authorization"]


def test_cancel_sends_response_cancel_event():
    captured = []

    class FakeWS:
        async def send(self, payload):
            captured.append(json.loads(payload))

    import asyncio

    async def go():
        tr = rt.RealtimeTransport(rt.RealtimeSession("s", "eph-only", 9e9))
        tr._ws = FakeWS()
        await tr.cancel()
        return tr

    transport = asyncio.run(go())
    assert transport._cancelled is True
    assert captured == [{"type": "response.cancel"}]
