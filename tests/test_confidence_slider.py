"""The confidence threshold slider filters; it never drives the video.

Dragging it used to clear the review state and then navigate to the first
remaining word, which paused playback and seeked to that word. Adjusting
the filter while watching the video must leave playback untouched.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from nicegui import ui

from utils.srt import SRTCaption, SRTEditor


def _editor(monkeypatch, storage):
    monkeypatch.setattr('utils.srt.app', SimpleNamespace(storage=SimpleNamespace(user=storage)))
    # Usage counting registers a client-wide flush timer; irrelevant here.
    monkeypatch.setattr('utils.usage.record', lambda *a, **k: None)
    editor = SRTEditor('slider-test', 'srt', 'test.srt')
    editor.words = [
        {'t': 'hello', 's': 1, 'e': 2, 'c': .2},
        {'t': 'world', 's': 2, 'e': 3, 'c': .9},
    ]
    editor.words_alignment = 'dtw'
    editor.captions = [SRTCaption(1, '00:00:01,000', '00:00:03,000', 'hello world')]
    editor.main_container = ui.column()
    editor.refresh_display()
    editor.create_confidence_workspace()
    return editor


def _move_slider(editor, value):
    slider = next(element for element in ui.context.client.elements.values()
                  if isinstance(element, ui.slider))
    slider.value = value
    listener = next(event for event in slider._event_listeners.values()
                    if event.type == 'change')
    listener.handler(None)


@pytest.mark.parametrize('threshold', [0.0, 0.5, 1.0])
def test_threshold_change_never_pauses_or_seeks(monkeypatch, threshold):
    storage = {}
    javascript = Mock()
    monkeypatch.setattr(ui, 'run_javascript', javascript)
    with ui.column() as root:
        editor = _editor(monkeypatch, storage)
        player = Mock()
        editor.set_video_player(player)
        containers = dict(editor.caption_containers)
        try:
            _move_slider(editor, threshold)

            assert editor.confidence_threshold == threshold
            assert storage['confidence_threshold'] == threshold
            # Playback is the viewer's: no seek, no pause, no play.
            assert player.mock_calls == []
            # The only script sent releases the bounded replay window; it
            # must not be the variant that also pauses the video.
            scripts = [call.args[0] for call in javascript.call_args_list]
            assert scripts == ['window.__releaseConfidenceReplay?.()']
            # Containers are reused, so the transcript keeps its scroll spot.
            assert editor.caption_containers == containers
        finally:
            root.clear()
            root.delete()


def test_threshold_change_keeps_a_still_flagged_review_word(monkeypatch):
    storage = {}
    monkeypatch.setattr(ui, 'run_javascript', Mock())
    with ui.column() as root:
        editor = _editor(monkeypatch, storage)
        try:
            _move_slider(editor, 0.5)
            target = editor.confidence_review_targets()[0]
            editor._confidence_review_id = target['word_id']
            editor._confidence_review_caption_index = target['caption_index']

            # 'hello' (0.2) is still below 0.3: the reviewer keeps their place.
            _move_slider(editor, 0.3)
            assert editor._confidence_review_id == target['word_id']
            assert editor._confidence_review_caption_index == target['caption_index']

            # Below 0.2 it is no longer flagged, so the selection is dropped.
            _move_slider(editor, 0.15)
            assert editor.confidence_review_targets() == []
            assert editor._confidence_review_id is None
            assert editor._confidence_review_caption_index is None
        finally:
            root.clear()
            root.delete()
