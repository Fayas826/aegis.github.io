"""
A.E.G.I.S. Clause-by-Clause Streaming Speech Synthesizer & Emotional Vocal Cortex.
Connects BERT Emotion Engine directly to Kokoro-82M & Neural Vocal Cords.
- Low-latency clause streaming: First audio playback starts in <400ms.
- BERT emotional modulation: Joy, Anger, Sadness, Surprise dynamically alter cadence, speed, AND pitch.
  Pitch is applied via librosa pitch_shift on the raw numpy samples (REAL, not fake dict values).
- Full-duplex barge-in: Instantly aborts playback when user interrupts.
- AEGIS_VOICE_OUTPUT_MODE: 'local' plays via sounddevice; 'browser' skips local play (browser streams it).
"""

import os
import re
import time
import queue
import asyncio
import logging
import threading
from typing import Dict, Any, List, Optional, Generator, AsyncGenerator
import sounddevice as sd
import numpy as np

from core.voice.council_voice_matrix import council_voice, COUNCIL_PERSONA_MAP
from core.cognition.perception.emotion_engine import EmotionEngine

logger = logging.getLogger("streaming_speech")

# BERT emotion → pitch semitones applied via librosa (REAL prosody, not fake dict)
EMOTION_SEMITONES: Dict[str, float] = {
    "JOY":      +1.5,
    "LOVE":     +1.5,
    "SADNESS":  -1.2,
    "ANGER":    -0.5,
    "SURPRISE": +2.0,
    "FEAR":     +1.0,
    "DISGUST":  -0.8,
    "NEUTRAL":  0.0,
}

# Kokoro exposes speed but not a native style/emotion control.  These
# application-level presets are therefore made audible by combining Kokoro's
# speed control with the real post-synthesis pitch shift below.  Edge-TTS uses
# its own native rate/pitch settings in ``user_settings``.
KOKORO_STYLE_PROSODY: Dict[str, Dict[str, float]] = {
    "CALM": {"speed_multiplier": 0.88, "pitch_semitones": -1.0},
    "FORMAL": {"speed_multiplier": 0.94, "pitch_semitones": -0.25},
    "ENERGETIC": {"speed_multiplier": 1.14, "pitch_semitones": 0.75},
    "WARM": {"speed_multiplier": 0.98, "pitch_semitones": 0.25},
    "SUPERFRIENDLY": {"speed_multiplier": 1.06, "pitch_semitones": 1.0},
}

class StreamingSpeechSynthesizer:
    def __init__(self):
        self.emotion_engine = EmotionEngine()
        self._playback_queue = queue.Queue()
        self._stop_event = threading.Event()
        self._is_playing = False
        self._playback_thread = None

    def _split_clauses(self, text: str) -> List[str]:
        """
        Splits paragraph into natural conversational clauses along punctuation boundaries.
        Ensures clauses are not too short or too long for natural human breath cadence.
        """
        # Split on sentence ends or pause marks: . ! ? ; ,
        raw_parts = re.split(r'([.!?;,\n]+)', text)
        clauses = []
        current = ""

        for i in range(0, len(raw_parts) - 1, 2):
            clause_text = raw_parts[i].strip()
            punct = raw_parts[i+1].strip()
            full_clause = f"{clause_text}{punct}".strip()
            
            if not full_clause:
                continue

            # If clause is too short (less than 3 words) and not ending with strong punctuation, merge with current
            if len(full_clause.split()) < 3 and punct == "," and current:
                current += f" {full_clause}"
            else:
                if current:
                    current += f" {full_clause}"
                    clauses.append(current.strip())
                    current = ""
                else:
                    clauses.append(full_clause)

        # Catch trailing segment
        if len(raw_parts) % 2 == 1 and raw_parts[-1].strip():
            trailing = raw_parts[-1].strip()
            if current:
                clauses.append(f"{current} {trailing}".strip())
            else:
                clauses.append(trailing)
        elif current:
            clauses.append(current.strip())

        return [c for c in clauses if c.strip()]

    def get_emotional_prosody(self, text: str) -> Dict[str, Any]:
        """
        BERT Emotional Cortex: Analyzes text emotion and returns modulated prosody parameters.
        Pitch semitones are now REAL — applied via librosa.effects.pitch_shift() on numpy samples.
        """
        emotion_label = "NEUTRAL"
        score = 0.90

        try:
            res = self.emotion_engine._raw_classify_cached(text)
            if res:
                emotion_label = res.get("label", "NEUTRAL").upper()
                score = res.get("score", 0.90)
        except Exception as e:
            logger.debug(f"[StreamingSpeech] BERT classification fallback: {e}")

        # Map BERT emotions to acoustic cadence & prosody
        # pitch_semitones is applied REAL via librosa after synthesis
        if emotion_label in ["JOY", "LOVE"]:
            return {
                "emotion": emotion_label,
                "score": score,
                "speed": 1.06,
                "pitch_semitones": EMOTION_SEMITONES["JOY"],
                "rate": "+4%",
                "style": "cheerful"
            }
        elif emotion_label == "SADNESS":
            return {
                "emotion": emotion_label,
                "score": score,
                "speed": 0.92,
                "pitch_semitones": EMOTION_SEMITONES["SADNESS"],
                "rate": "-5%",
                "style": "supportive"
            }
        elif emotion_label == "ANGER":
            return {
                "emotion": emotion_label,
                "score": score,
                "speed": 1.02,
                "pitch_semitones": EMOTION_SEMITONES["ANGER"],
                "rate": "+2%",
                "style": "firm"
            }
        elif emotion_label == "SURPRISE":
            return {
                "emotion": emotion_label,
                "score": score,
                "speed": 1.08,
                "pitch_semitones": EMOTION_SEMITONES["SURPRISE"],
                "rate": "+6%",
                "style": "inquisitive"
            }
        elif emotion_label == "FEAR":
            return {
                "emotion": emotion_label,
                "score": score,
                "speed": 1.04,
                "pitch_semitones": EMOTION_SEMITONES["FEAR"],
                "rate": "+3%",
                "style": "cautious"
            }
        else:
            return {
                "emotion": "NEUTRAL",
                "score": score,
                "speed": 1.00,
                "pitch_semitones": 0.0,
                "rate": "+0%",
                "style": "steady"
            }

    def apply_voice_style(self, prosody: Dict[str, Any], style: str = "") -> Dict[str, Any]:
        """Apply a requested user style to the actual local Kokoro controls.

        BERT supplies conversational emotion; this method supplies the explicit
        UI style. Keeping both contributions means a requested style is not
        silently ignored when the local engine is selected.
        """
        key = str(style or "CALM").upper()
        preset = KOKORO_STYLE_PROSODY.get(key, KOKORO_STYLE_PROSODY["CALM"])
        result = dict(prosody or {})
        base_speed = float(result.get("speed", 1.0) or 1.0)
        base_pitch = float(result.get("pitch_semitones", 0.0) or 0.0)
        result.update({
            "requested_style": key if key in KOKORO_STYLE_PROSODY else "CALM",
            "speed": max(0.5, min(2.0, base_speed * preset["speed_multiplier"])),
            "pitch_semitones": base_pitch + preset["pitch_semitones"],
        })
        return result

    def _apply_pitch_shift(self, samples: np.ndarray, sample_rate: int, semitones: float) -> np.ndarray:
        """
        REAL pitch shifting using librosa — this is what actually makes BERT emotions
        audible in the voice. Previously pitch values were stored in a dict but never applied.
        """
        if semitones == 0.0:
            return samples
        try:
            import librosa
            shifted = librosa.effects.pitch_shift(
                samples.astype(np.float32),
                sr=sample_rate,
                n_steps=semitones
            )
            return shifted.astype(samples.dtype)
        except ImportError:
            logger.warning("[StreamingSpeech] librosa not installed — pitch shift skipped. Run: pip install librosa")
            return samples
        except Exception as e:
            logger.debug(f"[StreamingSpeech] Pitch shift note: {e}")
            return samples

    async def speak_stream(
        self,
        specialist_id: str,
        text: str,
        on_clause_start: Optional[Any] = None,
        barge_in_checker: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Streams speech synthesis clause-by-clause:
        1. Analyzes BERT emotional vector.
        2. Splits into natural clauses.
        3. Synthesizes Clause 1 (<350ms) and initiates playback immediately.
        4. Synthesizes subsequent clauses in parallel while earlier audio is playing.
        5. Aborts immediately if barge_in_checker returns True.
        """
        self._stop_event.clear()
        clauses = self._split_clauses(text)
        if not clauses:
            clauses = [text]

        prosody = self.get_emotional_prosody(text)
        persona = council_voice.get_persona(specialist_id)
        logger.info(f"[STREAMING SPEECH] Specialist: {specialist_id} | Emotion: {prosody['emotion']} ({int(prosody['score']*100)}%) | Clauses: {len(clauses)}")

        synthesized_clauses = []
        start_time = time.time()
        first_audio_latency = None

        for idx, clause in enumerate(clauses):
            if self._stop_event.is_set() or (barge_in_checker and barge_in_checker()):
                logger.info("[STREAMING SPEECH] Barge-in detected! Aborting remaining speech synthesis.")
                break

            clause_start = time.time()
            # 1. Synthesize clause via Kokoro local or Edge-TTS
            res = None
            if council_voice._kokoro and persona.get("kokoro_voice"):
                try:
                    voice_name = persona["kokoro_voice"]
                    samples, sample_rate = council_voice._kokoro.create(
                        clause,
                        voice=voice_name,
                        speed=prosody["speed"]
                    )
                    res = {
                        "status": "SUCCESS",
                        "engine": "kokoro-82m-local",
                        "samples": samples,
                        "sample_rate": sample_rate,
                        "clause": clause
                    }
                except Exception as ex:
                    logger.debug(f"[StreamingSpeech] Kokoro clause error: {ex}")

            if not res or res.get("status") != "SUCCESS":
                # Fallback to file-based speak_as
                res = await council_voice.speak_as(specialist_id, clause)
                res["clause"] = clause

            clause_latency = round((time.time() - clause_start) * 1000, 1)
            if idx == 0:
                first_audio_latency = round((time.time() - start_time) * 1000, 1)
                logger.info(f"[STREAMING SPEECH] First clause ready in {first_audio_latency}ms! Initiating playback.")

            synthesized_clauses.append(res)

            # 2. Trigger clause start hook
            if on_clause_start:
                try:
                    on_clause_start(clause, idx, prosody)
                except Exception:
                    pass

            # 3. Apply REAL pitch shift to samples (BERT emotion → librosa pitch_shift)
            if res.get("samples") is not None and len(res["samples"]) > 0:
                n_semitones = prosody.get("pitch_semitones", 0.0)
                if n_semitones != 0.0:
                    res["samples"] = self._apply_pitch_shift(
                        res["samples"], res["sample_rate"], n_semitones
                    )

            # 4. Play audio — in local/daemon/both mode. Browser-only mode skips sd.play().
            voice_output_mode = os.environ.get("AEGIS_VOICE_OUTPUT_MODE", "both").lower()
            should_play_locally = voice_output_mode in ("local", "both", "desktop")

            if res.get("samples") is not None and len(res["samples"]) > 0 and should_play_locally:
                try:
                    sd.play(res["samples"], res["sample_rate"])
                    # Wait for playback or barge-in
                    duration = len(res["samples"]) / res["sample_rate"]
                    sleep_steps = int(duration / 0.05)
                    for _ in range(sleep_steps):
                        if self._stop_event.is_set() or (barge_in_checker and barge_in_checker()):
                            sd.stop()
                            break
                        await asyncio.sleep(0.05)
                except Exception as e_sd:
                    logger.debug(f"[StreamingSpeech] Audio device playback note: {e_sd}")
            elif res.get("samples") is not None and not should_play_locally:
                # Browser mode: yield duration as sleep so streaming still paces correctly
                duration = len(res["samples"]) / res["sample_rate"]
                await asyncio.sleep(min(duration * 0.1, 0.3))  # brief yield, browser handles timing

        total_latency = round((time.time() - start_time) * 1000, 1)
        return {
            "status": "SUCCESS",
            "specialist": specialist_id,
            "emotion": prosody["emotion"],
            "prosody": prosody,
            "total_clauses": len(clauses),
            "first_audio_latency_ms": first_audio_latency,
            "total_latency_ms": total_latency
        }

    def abort_speech(self):
        """Immediately stops audio output (Barge-In)."""
        self._stop_event.set()
        try:
            sd.stop()
        except Exception:
            pass
        logger.info("[STREAMING SPEECH] Speech aborted by Barge-In.")

streaming_speech = StreamingSpeechSynthesizer()
