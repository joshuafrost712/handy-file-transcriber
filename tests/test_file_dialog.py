"""Guards the exact bug that made 'Add files…' silently do nothing.

pywebview validates file-dialog filter strings with a strict regex. The old
filter ("Audio/Video (*.mp3 *.wav)") failed it on two counts — a '/' in the
description and space-separated extensions — so create_file_dialog raised
before any dialog opened. These tests assert the filter we now build is valid,
and that the old shape genuinely was not (so this can't silently regress).
"""
import app
from webview.util import parse_file_type


def test_built_filter_passes_pywebview_validation():
    # Should not raise; returns (description, extensions).
    desc, exts = parse_file_type(app.Api.audio_video_filter())
    assert desc.strip() == "Audio and Video"
    # Every supported extension is present, semicolon-separated.
    for e in app.AUDIO_EXTS:
        assert f"*{e}" in exts.split(";")


def test_all_files_fallback_is_valid():
    desc, exts = parse_file_type("All files (*.*)")
    assert desc.strip() == "All files"
    assert exts == "*.*"


def test_old_buggy_filter_was_invalid():
    import pytest
    bad_exts = " ".join(f"*{e}" for e in sorted(app.AUDIO_EXTS))
    with pytest.raises(ValueError):
        parse_file_type(f"Audio/Video ({bad_exts})")
