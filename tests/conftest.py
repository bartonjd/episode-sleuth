"""Shared pytest fixtures for the audio-fingerprint test suite.

Everything here is headless and offline: no Vosk model, no ffmpeg and no
network are required. The reference database is built at test time from the
small subtitle fixtures in ``tests/fixtures/`` so it always matches the current
schema.
"""
import os
import shutil
import sys

import pytest

# Make the project root importable no matter where pytest is invoked from.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


# ---------------------------------------------------------------------------
# Fixture-file paths
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def fixtures_dir():
    return FIXTURES


@pytest.fixture(scope="session")
def sample_srt():
    """Episode 1 subtitle fixture (courtroom dialogue)."""
    return os.path.join(FIXTURES, "sample.srt")


@pytest.fixture(scope="session")
def sample_ep2_srt():
    """Episode 2 subtitle fixture (harbor mystery dialogue)."""
    return os.path.join(FIXTURES, "sample_ep2.srt")


@pytest.fixture(scope="session")
def sample_vtt():
    return os.path.join(FIXTURES, "sample.vtt")


@pytest.fixture(scope="session")
def sample_audio():
    """A 30-second synthetic WAV (a quiet tone). Enough for a real file to
    exist on disk; transcription is mocked in the tests that need it."""
    return os.path.join(FIXTURES, "sample_audio.wav")


# ---------------------------------------------------------------------------
# Engine configuration
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def engine_cfg():
    """The classic engine config dict (from config.json defaults)."""
    from fingerprint_core import load_config
    return load_config()


@pytest.fixture(scope="session")
def fp_cfg(engine_cfg):
    from fingerprint_core import FingerprintConfig
    return FingerprintConfig.from_config(engine_cfg)


# ---------------------------------------------------------------------------
# Reference database built from the subtitle fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def ref_db_path(tmp_path_factory, sample_srt, sample_ep2_srt, engine_cfg, fp_cfg):
    """Build a two-episode reference DB (Matlock S01E01 + S01E02) once per
    session and return its path. Uses the same builder the CLI/GUI use so the
    stored token streams and fingerprints are identical to production."""
    import cli.build_fingerprints as cf
    from fingerprint_core import FingerprintDB

    workdir = tmp_path_factory.mktemp("refdb")
    # Copy the fixtures to canonical SxxExx names so the builder parses the
    # correct season/episode and episode title from each filename.
    ep1 = workdir / "Matlock (1986) - S01E01 - The Trial.srt"
    ep2 = workdir / "Matlock (1986) - S01E02 - The Harbor.srt"
    shutil.copy(sample_srt, ep1)
    shutil.copy(sample_ep2_srt, ep2)

    db_path = str(workdir / "test.db")
    db = FingerprintDB(db_path)
    cf.fingerprint_subtitle_file(str(ep1), db, fp_cfg, "Matlock", 1986,
                                 show_title="Matlock")
    cf.fingerprint_subtitle_file(str(ep2), db, fp_cfg, "Matlock", 1986,
                                 show_title="Matlock")
    db.close()
    return db_path


@pytest.fixture()
def ref_db(ref_db_path):
    """A fresh FingerprintDB connection to the session reference DB."""
    from fingerprint_core import FingerprintDB
    db = FingerprintDB(ref_db_path)
    yield db
    db.close()


# ---------------------------------------------------------------------------
# Known dialogue transcripts (what a perfect / noisy STT pass would yield)
# ---------------------------------------------------------------------------
# A deliberately noisy "STT transcript" of EP1 dialogue: homophones,
# misspellings and dropped words - exactly the sort of thing Vosk produces.
EP1_NOISY_TRANSCRIPT = (
    "your honour i object to this entire line of questioning "
    "the witnes clearly has no knowledge of the events of that nite "
    "ben matlock will proove that my client is inocent of all charges "
    "ladies and gentlemen of the jurry please consider the evidence carefuly "
    "the prosecution cannot place my client anywhere near the warehouse "
    "detective isnt it true that you never checked the security tapes"
)

# Something that has nothing to do with either reference episode.
UNRELATED_TRANSCRIPT = (
    "welcome back to the cooking show today we are making a spicy thai curry "
    "first chop the lemongrass and ginger then toast the coconut in a hot pan "
    "remember to keep stirring so the sauce does not stick or burn"
)


@pytest.fixture()
def ep1_noisy_transcript():
    return EP1_NOISY_TRANSCRIPT


@pytest.fixture()
def unrelated_transcript():
    return UNRELATED_TRANSCRIPT
