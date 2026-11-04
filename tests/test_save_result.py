"""Saving a transcript must keep the word timings and the user's splits.

The backend replaces the stored JSON result on save. The editor used to send
only the segments, so the word timings were lost and split at cursor and
click-to-seek were gone the next time the job was opened. Loading also merged
same-speaker segments again, undoing every split the user had saved.
"""
import json
from types import SimpleNamespace

import pytest

from utils.srt import SRTEditor

RESULT = {
    "segments": [{"start": 0.0, "end": 2.0, "text": "hello world", "speaker": "SPEAKER_00"}],
    "speaker_count": 1,
    "full_transcription": "hello world",
    "words": {
        "alignment": "dtw",
        "words": [
            {"t": "hello", "s": 0.0, "e": 1.0, "c": 0.9},
            {"t": "world", "s": 1.0, "e": 2.0, "c": 0.8},
        ],
    },
}


@pytest.fixture
def editor(monkeypatch):
    monkeypatch.setattr("utils.srt.app", SimpleNamespace(storage=SimpleNamespace(user={})))
    return SRTEditor("save-test", "txt", "test.mp4")


def test_saved_result_keeps_word_timings(editor):
    editor.parse_txt(json.dumps(RESULT))
    editor.load_words(json.dumps(RESULT))
    editor.captions[0].text = "hello there world"

    saved = json.loads(json.dumps(editor.export_result_json()))

    assert saved["words"] == RESULT["words"]
    assert saved["segments"][0]["text"] == "hello there world"
    assert saved["full_transcription"] == "hello there world"

    reopened = SRTEditor("save-test", "txt", "test.mp4")
    reopened.load_words(json.dumps(saved))
    assert len(reopened.words) == 2
    assert reopened.words_alignment == "dtw"


def test_download_export_stays_without_word_timings(editor):
    editor.parse_txt(json.dumps(RESULT))
    editor.load_words(RESULT)

    assert "words" not in editor.export_json()


def test_jobs_without_word_timings_save_as_before(editor):
    old = {key: value for key, value in RESULT.items() if key != "words"}
    editor.parse_txt(json.dumps(old))
    editor.load_words(old)

    assert editor.export_result_json() == {**editor.export_json(), "edited": True}


def test_first_load_merges_same_speaker_segments(editor):
    raw = {"segments": [
        {"start": 0.0, "end": 1.0, "text": "hello", "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "text": "world", "speaker": "SPEAKER_00"},
    ]}
    editor.parse_txt(json.dumps(raw))

    assert [c.text for c in editor.captions] == ["Hello world"]
    assert editor.captions[0].index == 1


def test_saved_split_survives_reload(editor):
    raw = {"segments": [
        {"start": 0.0, "end": 1.0, "text": "hello", "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "text": "world", "speaker": "SPEAKER_00"},
    ]}
    editor.parse_txt(json.dumps(raw))
    editor.load_words(raw)
    first = editor.captions[0]
    second = first.copy()
    first.text, first.end_time = "Hello", "00:00:01,000"
    second.text, second.start_time = "world", "00:00:01,000"
    editor.captions.append(second)

    saved = json.dumps(editor.export_result_json())
    reopened = SRTEditor("save-test", "txt", "test.mp4")
    reopened.parse_txt(saved)

    assert [c.text for c in reopened.captions] == ["Hello", "world"]
    assert [c.index for c in reopened.captions] == [1, 2]
