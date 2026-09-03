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
import sys
import json
import wave
import shutil
import logging
import zipfile
import tempfile
import urllib.request
from typing import Optional, List, Tuple, Callable

# pydub is only needed for AUDIO TRANSCRIPTION (it normalises audio via
# ffmpeg and depends on the C ``audioop`` module). It is deliberately imported
# defensively so the rest of this module - crucially download_vosk_model(),
# which only needs urllib + zipfile - keeps working even when pydub cannot be
# imported. On Python 3.13 ``audioop`` was removed from the stdlib (PEP 594)
# and is provided by the ``audioop-lts`` wheel; if that native extension is
# missing (e.g. not bundled into a frozen build) pydub raises at import time.
# Guarding it here means the Vosk model download and the GUI still function,
# and only actual transcription surfaces a clear, actionable error.
try:
    from pydub import AudioSegment
    import pydub.utils
    _PYDUB_IMPORT_ERROR = None
except Exception as _exc:  # pragma: no cover - depends on runtime environment
    AudioSegment = None
    pydub = None
    _PYDUB_IMPORT_ERROR = _exc

HERE = os.path.dirname(os.path.abspath(__file__))


def _require_pydub():
    """Raise a clear error if pydub (audio transcription support) is missing."""
    if AudioSegment is None:
        raise RuntimeError(
            "Audio transcription requires the 'pydub' package and its "
            "'audioop' dependency, which failed to load "
            f"({_PYDUB_IMPORT_ERROR}). On Python 3.13+ install 'audioop-lts', "
            "and ensure ffmpeg is on PATH. Note: downloading the Vosk model "
            "does not require pydub and works without it."
        )

# Known Vosk English models keyed by a friendly "size" (see constants.py). The
# small model is the default (fast, ~40 MB); the large model is far more
# accurate on clean audio (~1.8 GB) and pushes DVD-rip confidence higher.
from constants import VOSK_MODELS, DEFAULT_MODELS_DIR  # noqa: E402


# ---------------------------------------------------------------------------
# Suppress ffmpeg/ffprobe console windows on Windows
# ---------------------------------------------------------------------------
# pydub shells out to ffmpeg/ffprobe via subprocess.Popen (imported into
# pydub.utils as ``Popen``). On Windows each of those spawns a flashing console
# window. Wrap pydub.utils.Popen so it always passes CREATE_NO_WINDOW. This is a
# no-op on non-Windows platforms and is applied only once.
if (pydub is not None and os.name == "nt"
        and not getattr(pydub.utils, "_no_window_patched", False)):
    _CREATE_NO_WINDOW = 0x08000000
    _orig_popen = pydub.utils.Popen

    def _no_window_popen(*args, **kwargs):
        if "creationflags" not in kwargs:
            kwargs["creationflags"] = _CREATE_NO_WINDOW
        return _orig_popen(*args, **kwargs)

    pydub.utils.Popen = _no_window_popen
    pydub.utils._no_window_patched = True


def load_audio_mono16k(path: str, sample_rate: int = 16000) -> "AudioSegment":
    """Load any audio file and convert to mono / target sample-rate / 16-bit."""
    _require_pydub()
    seg = AudioSegment.from_file(path)
    seg = seg.set_channels(1).set_frame_rate(sample_rate).set_sample_width(2)
    return seg


def segment_to_wav_bytes(seg: "AudioSegment") -> bytes:
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

    def transcribe_segment(self, seg: "AudioSegment") -> str:
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

    def transcribe_segment(self, seg: "AudioSegment") -> str:
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


# ---------------------------------------------------------------------------
# Where Vosk models live (must be a WRITABLE, PERSISTENT location)
# ---------------------------------------------------------------------------
# CRITICAL for frozen (PyInstaller) builds: never store models in a path
# derived from ``__file__``. In a --onefile build, ``__file__`` resolves inside
# ``sys._MEIPASS`` - a temporary directory that PyInstaller extracts on launch
# and DELETES on exit - so any downloaded model vanishes when the app closes
# (symptom: "download succeeds" but the model is "Not downloaded" on restart,
# and no models/ folder is ever visible). In a --onedir build ``__file__`` sits
# in the install directory, which is typically read-only (e.g. Program Files).
#
# So: when frozen, download/read models from a per-user application-data folder
# that persists across restarts and is always writable. When running from
# source we keep the project-local ``models/`` directory for backward
# compatibility with existing installs.

def _user_data_dir() -> str:
    """Per-user, per-OS application data directory for EpisodeSleuth."""
    app = "EpisodeSleuth"
    if sys.platform == "win32":
        root = os.environ.get("LOCALAPPDATA") or os.path.expanduser(
            r"~\AppData\Local")
    elif sys.platform == "darwin":
        root = os.path.expanduser("~/Library/Application Support")
    else:
        root = os.environ.get("XDG_DATA_HOME") or os.path.expanduser(
            "~/.local/share")
    return os.path.join(root, app)


def default_models_base() -> str:
    """Return the writable base directory where Vosk models are stored.

    Frozen build -> per-user data dir (persistent + writable).
    Source run   -> ``<project>/models`` (unchanged, backward compatible).
    """
    if getattr(sys, "frozen", False):
        return os.path.join(_user_data_dir(), DEFAULT_MODELS_DIR)
    return os.path.join(HERE, DEFAULT_MODELS_DIR)


def _model_search_bases() -> List[str]:
    """All base dirs to search when checking whether a model is present.

    Includes the writable default plus any read-only locations a model might
    have been shipped in (next to the exe, or bundled into the onefile temp
    dir), so a pre-installed model is still found.
    """
    bases: List[str] = [default_models_base()]
    try:
        if getattr(sys, "frozen", False):
            exe_dir = os.path.dirname(os.path.abspath(sys.executable))
            bases.append(os.path.join(exe_dir, DEFAULT_MODELS_DIR))
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                bases.append(os.path.join(meipass, DEFAULT_MODELS_DIR))
        # Always also consider the source-relative dir (harmless if absent).
        bases.append(os.path.join(HERE, DEFAULT_MODELS_DIR))
    except Exception:
        pass
    seen, out = set(), []
    for b in bases:
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def get_model_path(model_size: str = "small", models_dir: Optional[str] = None
                   ) -> Optional[str]:
    """Return the local path to the Vosk model for ``model_size`` if present.

    ``model_size`` is "small" or "large". When ``models_dir`` is given, only
    that directory is checked; otherwise every persistent/bundled model
    location is searched (see :func:`_model_search_bases`). Returns the
    directory path if the model exists on disk, else ``None`` (call
    :func:`download_vosk_model` to fetch it).
    """
    size = (model_size or "small").lower()
    spec = VOSK_MODELS.get(size, VOSK_MODELS["small"])
    bases = [models_dir] if models_dir else _model_search_bases()
    for base in bases:
        path = os.path.join(base, spec["dir"])
        if os.path.isdir(path):
            return path
    return None


class ModelDownloadError(Exception):
    """Raised when a Vosk model cannot be downloaded or unpacked."""


def download_vosk_model(model_size: str = "large",
                        models_dir: Optional[str] = None,
                        progress: Optional[Callable[[int, int], None]] = None,
                        force: bool = False,
                        cancel_check: Optional[Callable[[], bool]] = None
                        ) -> str:
    """Download and unzip a Vosk model, returning the local model directory.

    ``model_size`` is "small" or "large" (the large ~1.8 GB model gives the best
    accuracy on clean DVD audio). ``progress`` is an optional callback
    ``(downloaded_bytes, total_bytes)`` for UI progress reporting (``total_bytes``
    may be 0 if the server does not send a length).

    ``force`` re-downloads even if the model already exists (used by the
    "Download / Update model" button to refresh to the latest published build).
    ``cancel_check`` is an optional callable returning True to abort mid-download.

    The download is written to a temporary file, its length and zip integrity are
    verified before anything touches the models directory, and it is extracted to
    a staging folder that is only swapped into place once complete. That way a
    truncated or corrupt download can never leave a half-written model behind
    (the cause of the "zip failed to initialise" error on the large model).
    """
    size = (model_size or "large").lower()
    spec = VOSK_MODELS.get(size)
    if spec is None:
        raise ValueError(f"Unknown Vosk model size: {model_size!r} "
                         f"(known: {', '.join(VOSK_MODELS)})")
    base = models_dir or default_models_base()
    os.makedirs(base, exist_ok=True)
    dest = os.path.join(base, spec["dir"])
    if os.path.isdir(dest) and not force:
        logging.info("Vosk %s model already present at %s", size, dest)
        return dest

    url = spec["url"]
    logging.info("Downloading Vosk %s model (~%d MB) from %s",
                 size, spec["approx_mb"], url)

    tmp_zip = tempfile.NamedTemporaryFile(suffix=".zip", delete=False,
                                          dir=base)
    tmp_zip.close()
    staging = dest + ".partial"
    total = 0
    done = 0
    try:
        # ---- download to a temp file ----
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "dvd-identifier/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:  # nosec
                total = int(resp.headers.get("Content-Length", 0) or 0)
                chunk = 1024 * 256
                with open(tmp_zip.name, "wb") as fh:
                    while True:
                        if cancel_check and cancel_check():
                            raise ModelDownloadError("Download cancelled.")
                        block = resp.read(chunk)
                        if not block:
                            break
                        fh.write(block)
                        done += len(block)
                        if progress:
                            progress(done, total)
        except ModelDownloadError:
            raise
        except Exception as exc:
            raise ModelDownloadError(
                f"Could not download the model from {url} ({exc}). "
                "Check your internet connection and try again.") from exc

        # ---- verify the download is complete and valid ----
        if total and done < total:
            raise ModelDownloadError(
                f"Download incomplete: got {done} of {total} bytes. "
                "The connection was likely interrupted - please try again.")

        try:
            with zipfile.ZipFile(tmp_zip.name) as zf:
                bad = zf.testzip()  # returns first corrupt member, or None
                if bad is not None:
                    raise ModelDownloadError(
                        f"The downloaded archive is corrupt (bad entry: {bad}). "
                        "Please try downloading again.")
                names = zf.namelist()
                if not names:
                    raise ModelDownloadError("The downloaded archive is empty.")
                # top-level folder of the archive (skip any leading "./")
                tops = {n.replace("\\", "/").lstrip("./").split("/")[0]
                        for n in names if n.strip("/")}
                top = spec["dir"] if spec["dir"] in tops else sorted(tops)[0]

                # ---- extract to a clean staging dir, then swap in ----
                if os.path.isdir(staging):
                    shutil.rmtree(staging, ignore_errors=True)
                stage_root = staging + "_x"
                if os.path.isdir(stage_root):
                    shutil.rmtree(stage_root, ignore_errors=True)
                os.makedirs(stage_root, exist_ok=True)
                zf.extractall(stage_root)
        except zipfile.BadZipFile as exc:
            raise ModelDownloadError(
                "The downloaded file is not a valid zip archive - the download "
                "was probably truncated. Please try again.") from exc

        extracted = os.path.join(stage_root, top)
        if not os.path.isdir(extracted):
            raise ModelDownloadError(
                "The archive did not contain the expected model folder.")

        # Move extracted model to the final staging path, then atomically swap.
        shutil.move(extracted, staging)
        shutil.rmtree(stage_root, ignore_errors=True)
        if os.path.isdir(dest):
            shutil.rmtree(dest, ignore_errors=True)
        os.replace(staging, dest)

        logging.info("Vosk %s model ready at %s", size, dest)
        return dest
    finally:
        for junk in (tmp_zip.name, staging, staging + "_x"):
            try:
                if os.path.isdir(junk):
                    shutil.rmtree(junk, ignore_errors=True)
                elif os.path.exists(junk):
                    os.remove(junk)
            except OSError:
                pass


def get_transcriber(cfg: dict):
    """Factory that returns a transcriber based on config['stt']['engine'].

    For Vosk the model is selected in priority order:
      1. an explicit ``stt.vosk_model_path`` (backward compatible), else
      2. ``stt.model_size`` ("small" / "large") resolved under ``models/``.
    
    If the selected model is missing, it is auto-downloaded (the large model
    is ~1.8 GB so this may take a few minutes on first use).
    """
    stt_cfg = cfg.get("stt", {})
    engine = stt_cfg.get("engine", "vosk").lower()
    sample_rate = cfg.get("audio", {}).get("sample_rate", 16000)
    if engine == "vosk":
        model_size = str(stt_cfg.get("model_size", "small")).lower()
        model_path = stt_cfg.get("vosk_model_path")
        if not model_path:
            resolved = get_model_path(model_size)
            if not resolved:
                # Model missing - auto-download it before proceeding.
                logging.warning("Vosk %s model not found, downloading...", model_size)
                resolved = download_vosk_model(model_size)
            model_path = resolved
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
