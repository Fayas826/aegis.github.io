"""Regression tests for actual local TTS style controls."""

from core.voice.streaming_speech_synthesizer import StreamingSpeechSynthesizer


def test_local_kokoro_style_changes_speed_and_pitch_controls():
    synthesizer = StreamingSpeechSynthesizer()
    neutral = {
        "emotion": "NEUTRAL",
        "score": 0.9,
        "speed": 1.0,
        "pitch_semitones": 0.0,
        "style": "steady",
    }

    calm = synthesizer.apply_voice_style(neutral, "CALM")
    energetic = synthesizer.apply_voice_style(neutral, "ENERGETIC")

    assert calm["requested_style"] == "CALM"
    assert energetic["requested_style"] == "ENERGETIC"
    assert calm["speed"] < 1.0 < energetic["speed"]
    assert calm["pitch_semitones"] < 0 < energetic["pitch_semitones"]


def test_unknown_local_kokoro_style_is_safe_and_deterministic():
    synthesizer = StreamingSpeechSynthesizer()
    result = synthesizer.apply_voice_style({"speed": 1.0}, "not-a-style")
    assert result["requested_style"] == "CALM"
    assert result["speed"] == 0.88
