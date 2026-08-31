"""audio-fingerprint - a phonetic "Shazam for dialogue".

This package identifies TV/movie episodes from DVD-rip audio by transcribing a
few short samples with speech-to-text and matching the dialogue (phonetically)
against a reference database built from subtitle files.

The project currently uses a flat module layout (all modules live in the
project root). This module exposes the version string and the three command
entry points for convenience:

    from audio_fingerprint import gui_main, identify_main, fingerprint_main

Note: because the layout is flat, importing these convenience helpers works
when the project root is on ``sys.path`` (which is the case after
``pip install -e .`` or when running from the project directory).
"""

__version__ = "1.0.0"
__author__ = "audio-fingerprint contributors"
__license__ = "MIT"

__all__ = [
    "__version__",
    "gui_main",
    "identify_main",
    "fingerprint_main",
]


def gui_main():
    """Launch the Fluent Design desktop GUI (entry point: ``dvd-gui``)."""
    from dvd_identifier_fluent import main
    return main()


def identify_main(argv=None):
    """Run the batch DVD identifier CLI (entry point: ``dvd-identify``)."""
    from identify_dvd_episodes import main
    return main(argv)


def fingerprint_main(argv=None):
    """Build reference fingerprints from subtitles (entry: ``dvd-fingerprint``)."""
    from create_fingerprint import main
    return main(argv)
