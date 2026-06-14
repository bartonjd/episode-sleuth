# Phonetic Audio Fingerprinting for TV & Movie Identification

A "Shazam for dialogue". Instead of matching audio waveforms, this system matches\
**what is being said**. It builds a fingerprint database from subtitles (or audio),\
then identifies live microphone audio (e.g. from your Plex/TV) by transcribing it,\
encoding it phonetically, and matching shingled hashes against the database.

## How it works

```
text ──▶ clean / normalize ──▶ Double Metaphone phonetic encoding
     ──▶ 3-5 word phonetic shingles (sliding window)
     ──▶ stable hash per shingle ──▶ stored with timestamp + show metadata
```

Both sides — subtitles and spoken audio — go through the **identical** pipeline\
(`fingerprint_core.fingerprint_text`), so a fuzzy speech-to-text transcript still\
lines up with the clean subtitle text. The Double Metaphone step absorbs most STT\
spelling/homophone errors ("their" vs "there", "objection" vs "objektion"), and\
multiple shingle sizes (3, 4, 5) add robustness.

## Components

|