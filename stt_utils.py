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
import shutil
import logging
import zipfile
import tempfile
import urllib.request
from typing import Optional, List, Tuple, Callable

from pydub import AudioSegment
import pydub.utils

HERE = os.path.dirname(os.path.abspath(__file__))

# Known Vosk English models keyed by a friendly "size". The small model is the
# default (fast, ~40 MB); the large model is far more accurate on clean audio
# (~1.8 GB) and is what you want to push DVD-rip confidence higher.
VOSK_MODELS = {
    "small": {
        "dir": "vosk-model-small-en-us-0.15",
        "url": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
        "approx_mb": 40,
    },
    "large": {
        "dir": "vosk-model-en-us-0.22",
        "url": "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22.zip",
        "approx_mb": 1800,
    },
}


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


def get_model_path(model_size: str = "small", models_dir: Optional[str] = None
                   ) -> Optional[str]:
    """Return the local path to the Vosk model for ``model_size`` if present.

    ``model_size`` is "small" or "large". Looks under ``models_dir`` (default
    ``<project>/models``). Returns the directory path if the model exists on
    disk, else ``None`` (call :func:`download_vosk_model` to fetch it).
    """
    size = (model_size or "small").lower()
    spec = VOSK_MODELS.get(size, VOSK_MODELS["small"])
    base = models_dir or os.path.join(HERE, "models")
    path = os.path.join(base, spec["dir"])
    return path if os.path.isdir(path) else None


def download_vosk_model(model_size: str = "large",
                        models_dir: Optional[str] = None,
                        progress: Optional[Callable[[int, int], None]] = None
                        ) -> str:
    """Download and unzip a Vosk model, returning the local model directory.

    ``model_size`` is "small" or "large" (the large ~1.8 GB model gives the best
    accuracy on clean DVD audio). If the model already exists on disk it is
    returned immediately without re-downloading. ``progress`` is an optional
    callback ``(downloaded_bytes, total_bytes)`` for UI progress reporting
    (``total_bytes`` may be 0 if the server does not send a length).
    """
    size = (model_size or "large").lower()
    spec = VOSK_MODELS.get(size)
    if spec is None:
        raise ValueError(f"Unknown Vosk model size: {model_size!r} "
                         f"(known: {', '.join(VOSK_MODELS)})")
    base = models_dir or os.path.join(HERE, "models")
    os.makedirs(base, exist_ok=True)
    dest = os.path.join(base, spec["dir"])
    if os.path.isdir(dest):
        logging.info("Vosk %s model already present at %s", size, dest)
        return dest

    url = spec["url"]
    logging.info("Downloading Vosk %s model (~%d MB) from %s",
                 size, spec["approx_mb"], url)
    tmp_zip = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp_zip.close()
    try:
        with urllib.request.urlopen(url) as resp:  # nosec - fixed known host
            total = int(resp.headers.get("Content-Length", 0) or 0)
            done = 0
            chunk = 1024 * 256
            with open(tmp_zip.name, "wb") as fh:
                while True:
                    block = resp.read(chunk)
                    if not block:
                        break
                    fh.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total)

        # Extract into the models dir. The archive's top-level folder already
        # matches spec["dir"], so extracting into ``base`` lands it correctly.
        with zipfile.ZipFile(tmp_zip.name) as zf:
            top = zf.namelist()[0].split("/")[0] if zf.namelist() else spec["dir"]
            zf.extractall(base)
        extracted = os.path.join(base, top)
        if extracted != dest and os.path.isdir(extracted):
            shutil.move(extracted, dest)
        logging.info("Vosk %s model ready at %s", size, dest)
        return dest
    finally:
        try:
            os.remove(tmp_zip.name)
        except OSError:
            pass


def get_transcriber(cfg: dict):
    """Factory that returns a transcriber based on config['stt']['engine'].

    For Vosk the model is selected in priority order:
      1. an explicit ``stt.vosk_model_path`` (backward compatible), else
      2. ``stt.model_size`` ("small" / "large") resolved under ``models/``,
         falling back to the small model directory.
    """
    stt_cfg = cfg.get("stt", {})
    engine = stt_cfg.get("engine", "vosk").lower()
    sample_rate = cfg.get("audio", {}).get("sample_rate", 16000)
    if engine == "vosk":
        model_size = str(stt_cfg.get("model_size", "small")).lower()
        model_path = stt_cfg.get("vosk_model_path")
        if not model_path:
            resolved = get_model_path(model_size)
            model_path = resolved or os.path.join(
                "models", VOSK_MODELS.get(model_size, VOSK_MODELS["small"])["dir"])
        elif model_size == "large":
            # An explicit small path but a large size selected: prefer the large
            # model if it is actually downloaded, else keep the explicit path.
            resolved = get_model_path("large")
            if resolved:
                model_path = resolved
        return VoskTranscriber(model_path, sample_rate)
    if engine == "google":
        return GoogleTranscriber(stt_cfg.get("google_language", "en-US"))
    raise ValueError(f"Unknown STT engine: {engine}")
