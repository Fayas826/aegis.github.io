"""Optional Gemini Live voice + screen transport for AEGIS.

The transport is intentionally a thin shell around
``MultimodalRuntimeAdapter``. It owns hardware streams only when explicitly
started, keeps imports lazy, and stops with an honest ``UNAVAILABLE`` result
when a microphone, speaker, screen, or Gemini session is missing.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from core.runtime.multimodal_runtime import MultimodalRuntimeAdapter


@dataclass(frozen=True)
class LiveAudioConfig:
    input_rate: int = 16000
    output_rate: int = 24000
    channels: int = 1
    frames_per_buffer: int = 1024
    screen_fps: float = 1.0


class GeminiLiveVoiceSession:
    """Run bidirectional audio and screen streaming around the live adapter."""

    def __init__(self, adapter: Optional[MultimodalRuntimeAdapter] = None, config: Optional[LiveAudioConfig] = None):
        self.adapter = adapter or MultimodalRuntimeAdapter()
        self.config = config or LiveAudioConfig()
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()
        self.adapter.cancel("operator_stop")

    async def _hardware(self) -> Any:
        try:
            return importlib.import_module("pyaudio")
        except Exception:
            return None

    async def stream_microphone(self, pyaudio_module: Any) -> None:
        pa = pyaudio_module.PyAudio()
        stream = pa.open(
            format=pyaudio_module.paInt16,
            channels=self.config.channels,
            rate=self.config.input_rate,
            input=True,
            frames_per_buffer=self.config.frames_per_buffer,
        )
        try:
            while not self._stop.is_set():
                data = await asyncio.to_thread(
                    stream.read, self.config.frames_per_buffer, exception_on_overflow=False
                )
                await self.adapter.send_audio_chunk(data)
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()

    async def stream_screen(self) -> None:
        try:
            from core.perception.screen_capture import screen_capturer
        except Exception:
            screen_capturer = None
        delay = 1.0 / max(0.1, self.config.screen_fps)
        while not self._stop.is_set():
            if screen_capturer is None:
                return
            try:
                frame = await asyncio.to_thread(screen_capturer.capture_frame)
                if not frame or not getattr(frame, "image_b64", None):
                    return
                import base64

                await self.adapter.send_screen_frame(base64.b64decode(frame.image_b64))
            except Exception:
                return
            await asyncio.sleep(delay)

    async def speaker(self, pyaudio_module: Any) -> None:
        pa = pyaudio_module.PyAudio()
        stream = pa.open(
            format=pyaudio_module.paInt16,
            channels=self.config.channels,
            rate=self.config.output_rate,
            output=True,
        )
        try:
            while not self._stop.is_set():
                if self.adapter.audio_out:
                    chunk = self.adapter.audio_out.popleft()
                    await asyncio.to_thread(stream.write, chunk)
                else:
                    await asyncio.sleep(0.01)
        finally:
            stream.stop_stream()
            stream.close()
            pa.terminate()

    async def run(self, *, client: Any = None, session: Any = None, model: Optional[str] = None) -> dict[str, Any]:
        hardware = await self._hardware()
        if hardware is None:
            return {"status": "UNAVAILABLE", "detail": "PyAudio is not installed."}
        started = await self.adapter.start_live(client=client, session=session, model=model)
        if started.get("status") != "READY":
            return started
        try:
            await asyncio.gather(
                self.stream_microphone(hardware),
                self.stream_screen(),
                self.speaker(hardware),
                self._receive(),
            )
        except asyncio.CancelledError:
            self.stop()
            return {"status": "CANCELLED", "live": False}
        finally:
            await self.adapter.stop_live()
        return {"status": "STOPPED", "live": False}

    async def _receive(self) -> None:
        async for _event in self.adapter.iter_live_events():
            if self._stop.is_set():
                return


__all__ = ["GeminiLiveVoiceSession", "LiveAudioConfig"]
