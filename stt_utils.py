"""
stt_utils.py
============
Speech-to-text helpers used by identify_dvd_episodes.py.

Two engines are supported:
  * "vosk"   -> fully offline, no API key. Recommended. Requires a downloaded
               Vosk model (see README). Works on raw 16-bit mono PCM.
  * "google" -> SpeechRecognition's free Google Web Speech endpoint (needs
               internet; rate limited; good for quick tests).

Audio is normalised to 16 kHz mono 16-bit PCM via pydub/ffmpeg before STT.
"""

import os
import json
import wave
import logging
from typing import Optional, List, Tuple

from pydub import AudioSegment
import pydub.utils


# ---------------------------------------------------------------------------
# Suppress ffmpeg/ffprobe console windows on Windows
# ---------------------------------------------------------------------------
# pydub shells out to ffmpeg/ffprobe via subprocess.Popen (imported into
# pydub.utils as ``Popen``). On Windows each of those spawns a flashing console
# window. Wrap pydub.utils.Popen so it always passes CREATE_NO_WINDOW. This is a
# no-op on non-Windows platforms and is applied only once.
if os.name == "nt" and not getattr(pydub.utils, "_no_window_patched", False):
    _CREATE_NO_WINDOW = 0x08000000
    _orig_popen = pydub.utils.Popen

    def _no_window_popen(*args, **kwargs):
        if "creationflags" not in kwargs:
            kwargs["creationflags"] = _CREATE_NO_WINDOW
        return _orig_popen(*args, **kwargs)

    pydub.utils.Popen = _no_window_popen
    pydub.utils._no_window_patched = True


def load_audio_mono16k(path: str, sample_rate: int = 16000) -> AudioSegment:
    """Load any audio file and convert to mono / target sample-rate / 16-bit."""
    seg = AudioSegment.from_file(path)
    seg = seg.set_channels(1).set_frame_rate(sample_rate).set_sample_width(2)
    return seg


def segment_to_wav_bytes(seg: AudioSegment) -> bytes:
    import io
    buf = io.BytesIO()
    seg.export(buf, format="wav")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Vosk (offline)
# ---------------------------------------------------------------------------

class VoskTranscriber:
    def __init__(self, model_path: str, sample_rate: int = 16000):
        from vosk import Model, KaldiRecognizer, SetLogLevel
        SetLogLevel(-1)
        if not os.path.isdir(model_path):
            raise FileNotFoundError(
                f"Vosk model not found at '{model_path}'. "
                "Download one from https://alphacephei.com/vosk/models "
                "(e.g. vosk-model-small-en-us-0.15) and unzip it there."
            )
        self.sample_rate = sample_rate
        self.model = Model(model_path)
        self.KaldiRecognizer = KaldiRecognizer

    def transcribe_segment(self, seg: AudioSegment) -> str:
        """Transcribe a full pydub AudioSegment to text."""
        seg = seg.set_channels(1).set_frame_rate(self.sample_rate).set_sample_width(2)
        rec = self.KaldiRecognizer(self.model, self.sample_rate)
        rec.SetWords(False)
        raw = seg.raw_data
        chunk = 4000
        texts: List[str] = []
        for i in range(0, len(raw), chunk):
            if rec.AcceptWaveform(raw[i:i + chunk]):
                texts.append(json.loads(rec.Result()).get("text", ""))
        texts.append(json.loads(rec.FinalResult()).get("text", ""))
        return " ".join(t for t in texts if t).strip()

    def transcribe_pcm(self, pcm_bytes: bytes) -> str:
        rec = self.KaldiRecognizer(self.model, self.sample_rate)
        rec.AcceptWaveform(pcm_bytes)
        return json.loads(rec.FinalResult()).get("text", "").strip()


# ---------------------------------------------------------------------------
# Google Web Speech (online, via SpeechRecognition)
# ---------------------------------------------------------------------------

class GoogleTranscriber:
    def __init__(self, language: str = "en-US"):
        import speech_recognition as sr
        self.sr = sr
        self.recognizer = sr.Recognizer()
        self.language = language

    def transcribe_segment(self, seg: AudioSegment) -> str:
        import io
        seg = seg.set_channels(1).set_frame_rate(16000).set_sample_width(2)
        wav_bytes = segment_to_wav_bytes(seg)
        with self.sr.AudioFile(io.BytesIO(wav_bytes)) as source:
            audio = self.recognizer.record(source)
        try:
            return self.recognizer.recognize_google(audio, language=self.language)
        except self.sr.UnknownValueError:
            return ""
        except self.sr.RequestError as exc:
            logging.error("Google STT request error: %s", exc)
            return ""


def get_transcriber(cfg: dict):
    """Factory that returns a transcriber based on config['stt']['engine']."""
    stt_cfg = cfg.get("stt", {})
    engine = stt_cfg.get("engine", "vosk").lower()
    sample_rate = cfg.get("audio", {}).get("sample_rate", 16000)
    if engine == "vosk":
        return VoskTranscriber(stt_cfg.get("vosk_model_path",
                                           "models/vosk-model-small-en-us-0.15"),
                               sample_rate)
    if engine == "google":
        return GoogleTranscriber(stt_cfg.get("google_language", "en-US"))
    raise ValueError(f"Unknown STT engine: {engine}")
