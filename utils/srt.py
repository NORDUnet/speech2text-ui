# Copyright (c) 2025-2026 Sunet.
# Contributor: Kristofer Hallin
#
# This file is part of Sunet Scribe.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import re
import httpx

import html as html_module

from nicegui import app, events, ui
from typing import Callable, List, Optional
from utils.caption import SRTCaption
from utils.common import default_styles, get_auth_header, sanitize_filename
from utils.settings import get_settings
from utils.undo_redo import UndoRedoManager
from utils.word_timing import match_word_indices
from utils.confidence_review import review_targets, review_index
from utils.subtitle_checks import check_subtitles

CHARACTER_LIMIT_EXCEEDED_COLOR = "text-amber-600"
CHARACTER_LIMIT = 42

# Words whose model confidence is below this are flagged in the editor.
# Calibrated on real lecture audio: confidences run much lower there than
# on clean recordings, so a high threshold flags far too much.
LOW_CONFIDENCE_THRESHOLD = 0.3

settings = get_settings()


class SRTEditor:
    def __init__(self, uuid: str, srt_format: str, filename: str):
        """
        Initialize the SRT editor with empty captions and other properties.
        """

        self.uuid = uuid
        self.srt_format = srt_format
        self.captions: List[SRTCaption] = []
        self.selected_caption: Optional[SRTCaption] = None
        self.caption_cards = {}
        self.caption_containers = {}
        self.main_container = None
        self.search_term = ""
        self.search_results = []
        self.current_search_index = 0
        self.case_sensitive = False
        self.exact_match = False
        self.search_container = None
        self._search_input = None
        self.__video_player = None
        self.words_per_minute_element = None
        self.speakers = []
        self.data_format = None
        self.filename = filename

        # Word-level timing payload ({"t", "s", "e", "c"} dicts, time-ordered).
        # Empty for jobs transcribed before word timestamps existed.
        self.words: list = []
        self._word_match_cache = None
        self._confidence_review_id = None
        self._confidence_review_caption_index = None
        self._confidence_review_status = None
        self._confidence_review_list = None
        self._confidence_review_page = 0
        self._confidence_review_caption_snapshot = None
        self._reviewed_words_key = f"reviewed_words:{uuid}"
        self._reviewed_words = set(app.storage.user.get(self._reviewed_words_key, []))

        # How the word times were aligned: "dtw" (accurate), "vad"
        # (heuristic within short VAD speech segments) or "heuristic"
        # (drifts within long segments). Karaoke is offered for dtw and vad.
        self.words_alignment: str = "heuristic"

        # Per-user flagging threshold, persisted across sessions.
        self.confidence_threshold: float = app.storage.user.get(
            "confidence_threshold", LOW_CONFIDENCE_THRESHOLD
        )

        # Live subtitle preview on the video (native <track>, WebVTT).
        self.subtitle_preview: bool = app.storage.user.get(
            "subtitle_preview", False
        )
        self._preview_refresh_pending = False

        # Initialize undo/redo manager
        self.undo_redo_manager = UndoRedoManager()
        self.undo_button = None
        self.redo_button = None

        # Track unsaved changes
        self._has_unsaved_changes = False
        self._save_confirmation_dialog = None
        self._pending_action_after_save: Optional[Callable] = None
        self._play_pause = False

    def has_unsaved_changes(self) -> bool:
        """
        Check if there are unsaved changes.
        """

        return self._has_unsaved_changes

    def set_subtitle_preview(self, enabled: bool) -> None:
        """
        Toggle the live subtitle preview on the video. Persisted per user.
        """
        self.subtitle_preview = enabled
        app.storage.user["subtitle_preview"] = enabled
        self.refresh_subtitle_preview(force=True)

    def refresh_subtitle_preview(self, force: bool = False) -> None:
        """
        Push the current captions to the video's preview subtitle track as
        native TextTrack cues (no subtitle file request). Reflects the editor's *current* state, unsaved
        edits included. No-op while the preview is off, unless forced (to
        clear the track when toggling off).
        """
        if not (self.subtitle_preview or force):
            return

        def push() -> None:
            self._preview_refresh_pending = False

            cues = []
            if self.subtitle_preview:
                for caption in self.captions:
                    try:
                        cues.append(
                            {
                                "s": caption.get_start_seconds(),
                                "e": caption.get_end_seconds(),
                                "t": caption.text,
                            }
                        )
                    except (ValueError, AttributeError):
                        continue

                # Chrome can retain both cues when paused exactly where
                # one ends and the next starts. Keep preview ends strictly
                # before the next start; exported caption times are unchanged.
                cues.sort(key=lambda c: c["s"])
                for i in range(len(cues) - 1):
                    cues[i]["e"] = min(cues[i]["e"], cues[i + 1]["s"] - 0.001)
                # Equal starts can leave an earlier cue with no duration.
                cues = [cue for cue in cues if cue["e"] > cue["s"]]

            ui.run_javascript(
                "window.__updateSubtitlePreview && window.__updateSubtitlePreview("
                f"{json.dumps(cues)}, {str(bool(self.subtitle_preview)).lower()});"
            )

        if force:
            # Toggle / initial page load: push immediately. Creating a
            # nested ui.timer is not possible from a timer callback (no
            # slot context), and there is no mutation to wait out.
            push()
            return

        # Edit-driven refresh: callers mark changes *before* mutating the
        # captions (save_state_for_undo), so push on a short delay to read
        # the post-mutation state — also coalesces bursts (replace all).
        if self._preview_refresh_pending:
            return
        self._preview_refresh_pending = True
        ui.timer(0.15, push, once=True)

    def mark_as_changed(self) -> None:
        """
        Mark the editor as having unsaved changes.
        """
        self.refresh_subtitle_preview()

        self._has_unsaved_changes = True

    def mark_as_saved(self) -> None:
        """
        Mark the editor as having no unsaved changes.
        """

        self._has_unsaved_changes = False

    def setup_beforeunload_warning(self) -> None:
        """
        Setup browser beforeunload warning for unsaved changes.
        """

        ui.run_javascript(
            """
            window.addEventListener('beforeunload', function(e) {
                if (window.hasUnsavedChanges) {
                    e.preventDefault();
                    e.returnValue = 'You have unsaved changes. Are you sure you want to leave?';
                    return e.returnValue;
                }
            });
        """
        )

    def update_beforeunload_state(self) -> None:
        """
        Update the browser's beforeunload state based on unsaved changes.
        """

        if self._has_unsaved_changes:
            ui.run_javascript("window.hasUnsavedChanges = true;")
        else:
            ui.run_javascript("window.hasUnsavedChanges = false;")

    def show_save_confirmation_dialog(
        self,
        on_save: Optional[Callable] = None,
        on_discard: Optional[Callable] = None,
        on_cancel: Optional[Callable] = None,
    ) -> None:
        """
        Show a dialog asking the user to save, discard, or cancel.
        """

        def handle_save():
            dialog.close()
            self.save_srt_changes()
            if on_save:
                on_save()

        def handle_discard():
            dialog.close()
            self.mark_as_saved()
            self.update_beforeunload_state()
            if on_discard:
                on_discard()

        def handle_cancel():
            dialog.close()
            if on_cancel:
                on_cancel()

        with ui.dialog() as dialog, ui.card().classes("w-96"):
            ui.label("Unsaved changes").classes("text-h6 q-mb-md")
            ui.label("You have unsaved changes. What would you like to do?").classes(
                "q-mb-lg"
            )

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button("Cancel", on_click=handle_cancel).props("flat", remove="color")
                ui.button("Discard", on_click=handle_discard).props("flat color=red")
                ui.button("Save", on_click=handle_save).props("color=primary")

        dialog.open()

    def close_editor(self, redirect_url: Optional[str] = None) -> None:
        """
        Close the editor, prompting to save if there are unsaved changes.
        If redirect_url is provided, navigate there after closing.
        """

        def do_close():
            if redirect_url:
                ui.navigate.to(redirect_url)

        # if self.has_unsaved_changes():
        #     self.show_save_confirmation_dialog(
        #         on_save=do_close,
        #         on_discard=do_close,
        #         on_cancel=None,  # Just close the dialog, don't navigate
        #     )
        # else:
        do_close()

    def save_state_for_undo(self) -> None:
        """
        Save the current state before making changes.
        """

        self.undo_redo_manager.save_state(self.captions)
        self._update_undo_redo_buttons()
        # Mark as having unsaved changes
        self.mark_as_changed()
        self.update_beforeunload_state()

    def undo(self) -> None:
        """
        Undo the last action.
        """
        previous_state = self.undo_redo_manager.undo(self.captions)
        if previous_state is not None:
            self.captions = previous_state
            self.selected_caption = None
            self.renumber_captions()
            self.update_words_per_minute()
            self.refresh_display(force_full_refresh=True)
            self._update_undo_redo_buttons()
            # Mark as having unsaved changes (undo is still a change from saved state)
            self.mark_as_changed()
            self.update_beforeunload_state()
        else:
            ui.notify("Nothing to undo", type="info", position="bottom")

    def redo(self) -> None:
        """
        Redo the last undone action.
        """
        next_state = self.undo_redo_manager.redo(self.captions)
        if next_state is not None:
            self.captions = next_state
            self.selected_caption = None
            self.renumber_captions()
            self.update_words_per_minute()
            self.refresh_display(force_full_refresh=True)
            self._update_undo_redo_buttons()
            # Mark as having unsaved changes
            self.mark_as_changed()
            self.update_beforeunload_state()
        else:
            ui.notify("Nothing to redo", type="info", position="bottom")

    def _update_undo_redo_buttons(self) -> None:
        """
        Update the enabled state of undo/redo buttons.
        """
        if self.undo_button:
            if self.undo_redo_manager.can_undo():
                self.undo_button.enable()
                self.undo_button.props("flat dense", remove="color")
            else:
                self.undo_button.disable()
                self.undo_button.props("flat dense color=grey")

        if self.redo_button:
            if self.undo_redo_manager.can_redo():
                self.redo_button.enable()
                self.redo_button.props("flat dense", remove="color")
            else:
                self.redo_button.disable()
                self.redo_button.props("flat dense color=grey")

    def create_undo_redo_panel(self) -> None:
        """
        Create the undo/redo buttons panel.
        """
        with ui.row().classes("gap-2"):
            self.undo_button = (
                ui.button("Undo", icon="undo")
                .props("flat dense color=grey")
                .on("click", self.undo)
            )
            self.undo_button.disable()

            self.redo_button = (
                ui.button("Redo", icon="redo")
                .props("flat dense color=grey")
                .on("click", self.redo)
            )
            self.redo_button.disable()

    def save_srt_changes(self) -> None:
        try:
            if self.srt_format == "srt":
                data = self.export_srt()
            else:
                data = json.dumps(self.export_json())

            jsondata = {"format": self.srt_format, "data": data}
            headers = get_auth_header()
            headers["Content-Type"] = "application/json"
            res = httpx.put(
                f"{settings.API_URL}/api/v1/transcriber/{self.uuid}/result",
                headers=headers,
                json=jsondata,
            )
            res.raise_for_status()
        except httpx.HTTPError as e:
            ui.notify(f"Error:  Failed to save file:  {e}", type="negative")
            return

        # Mark as saved after successful save
        self.mark_as_saved()
        self.update_beforeunload_state()

        ui.notify(
            "File saved successfully",
            type="positive",
            position="bottom",
            icon="check_circle",
        )

    def set_autoscroll(self, autoscroll: bool) -> None:
        """
        Toggle follow mode: during playback the current caption is scrolled
        into view and marked (and karaoke highlights the word, when
        available) — without opening the caption for editing. Runs fully
        client-side; this just flips the JS flag.
        """
        ui.run_javascript(
            f"window.__followPlayback = {'true' if autoscroll else 'false'};"
        )

    def handle_key_event(self, event: events.KeyEventArguments) -> None:
        # Only handle keydown events, not keyup to prevent double-firing
        if not event.action.keydown:
            return

        match event.key:
            # Next block of captions, Alt+Down
            case "ArrowDown" if event.modifiers.alt and not event.modifiers.shift and not event.modifiers.ctrl and not event.modifiers.meta:
                self.select_next_caption()

            # Prev block of captions, Alt+Up
            case "ArrowUp" if event.modifiers.alt and not event.modifiers.shift and not event.modifiers.ctrl and not event.modifiers.meta:
                self.select_prev_caption()

            # Split block, Ctrl/⌘+Enter
            case "Enter" if event.modifiers.ctrl and not event.modifiers.shift and not event.modifiers.alt and not event.modifiers.meta:
                self.split_caption(self.selected_caption)
            case "Enter" if event.modifiers.meta and not event.modifiers.shift and not event.modifiers.alt and not event.modifiers.ctrl:
                self.split_caption(self.selected_caption)

            # Merge block with next, Ctrl+M
            case "m" if event.modifiers.ctrl:
                self.merge_with_next(self.selected_caption)

            # Merge block with previous, Ctrl+Shift+M
            case "M" if event.modifiers.ctrl:
                self.merge_with_previous(self.selected_caption)

            # Add caption after, Shift+Ctrl+Enter
            case "Enter" if self.data_format == "txt" and event.modifiers.ctrl and event.modifiers.shift:
                self.add_caption_after(self.selected_caption)
            case "Enter" if self.data_format == "txt" and event.modifiers.meta and event.modifiers.shift:
                self.add_caption_after(self.selected_caption)

            # Delete block, Ctrl+D
            case "d" if event.modifiers.ctrl:
                self.remove_caption(self.selected_caption)

            # Validate captions, Ctrl+Shift+V
            case "V" if event.modifiers.ctrl and event.modifiers.shift:
                self.validate_captions()

            # Play/pause video, Ctrl+Space
            case " " if event.modifiers.ctrl and not event.modifiers.shift and not event.modifiers.alt and not event.modifiers.meta:
                if self.__video_player:
                    if self._play_pause:
                        self.__video_player.pause()
                        self._play_pause = False
                    else:
                        self.__video_player.play()
                        self._play_pause = True

            # Undo, Ctrl+Z
            case "z" if event.modifiers.ctrl and not event.modifiers.shift:
                self.undo()
            case "z" if event.modifiers.meta and not event.modifiers.shift:
                self.undo()

            # Redo, Ctrl+Y
            case "y" if event.modifiers.ctrl and not event.modifiers.shift:
                self.redo()
            case "z" if event.modifiers.meta and event.modifiers.shift:
                self.redo()
            case "y" if event.modifiers.meta and not event.modifiers.shift:
                self.redo()

            # Close block, Escape
            case "Escape":
                # Click the "Close" button to save changes before closing
                # This behaves the same as clicking the Close button
                ui.run_javascript("document.querySelector('.caption-close')?.click()")

            # Open find, Ctrl+F
            case "f" if event.modifiers.ctrl and not event.modifiers.shift:
                self.open_search_panel()
            case "f" if event.modifiers.meta and not event.modifiers.shift:
                self.open_search_panel()

            # Save file, Ctrl+S / Cmd+S
            case "s" if event.modifiers.ctrl or event.modifiers.meta:
                self.save_srt_changes()

            # Export file, Ctrl+E / Cmd+E
            case "e" if event.modifiers.ctrl and not event.modifiers.shift:
                self.show_export_dialog(self.filename)
            case "e" if event.modifiers.meta and not event.modifiers.shift:
                self.show_export_dialog(self.filename)
            # Everything else
            case _:
                pass

    def select_next_caption(self) -> None:
        """
        Select the next caption in the list.
        """

        if not self.captions:
            return

        if self.selected_caption:
            current_index = self.captions.index(self.selected_caption)

            if current_index + 1 >= len(self.captions):
                return

            self.select_caption(self.captions[current_index + 1])
        else:
            self.select_caption(self.captions[0])

    def select_prev_caption(self) -> None:
        """
        Select the previous caption in the list.
        """

        if not self.captions:
            return

        if self.selected_caption:
            current_index = self.captions.index(self.selected_caption)
            if current_index > 0:
                self.select_caption(self.captions[current_index - 1])
            else:
                self.select_caption(self.captions[0])

    def set_words_per_minute_element(self, element) -> None:
        """
        Set the element to display words per minute.
        """

        self.words_per_minute_element = element

    def update_words_per_minute(self) -> None:
        """
        Update the words per minute display.
        """

        if self.words_per_minute_element:
            wpm = self.get_words_per_minute()
            self.words_per_minute_element.set_content(
                f"<b>Words per minute:</b> {wpm:.2f}"
            )

    def get_words_per_minute(self) -> float:
        """
        Calculate the average words per minute based on caption text.
        """

        total_words = sum(len(caption.text.split()) for caption in self.captions)
        total_seconds = sum(
            caption.get_end_seconds() - caption.get_start_seconds()
            for caption in self.captions
        )

        if total_seconds == 0:
            return 0.0

        return (total_words / total_seconds) * 60.0

    def set_video_player(self, player) -> None:
        """
        Set the video player for the editor.
        """

        self.__video_player = player

    @ui.refreshable
    def render_speakers(self) -> None:
        for idx, speaker in enumerate(self.speakers):
            if speaker == "UNKNOWN":
                continue

            speaker_inp = ui.input(value=speaker)
            speaker_inp.on("keydown.enter", lambda e: e.sender.run_method('blur'))
            speaker_inp.on("blur", lambda e, i=idx: self.rename_speaker_global(i, e.sender.value))

        inp = ui.input(placeholder="Add Speaker")
        inp.on("keydown.enter", lambda e: e.sender.run_method('blur'))
        inp.on("blur", lambda e: self.add_speaker(e.sender.value))


    def rename_speaker_global(self, index, new_name):
        new_name = new_name.strip()
        # prevent empty names
        if not new_name:
            return

        # check for duplicates (excluding current index)
        if new_name in (s for i, s in enumerate(self.speakers) if i != index):
            ui.notify(f'"{new_name}" already exists', color="error")
            return

        old_name = self.speakers[index]

        for caption in self.captions:
            if caption.speaker == old_name:
                caption.speaker = new_name
        self.speakers[index] = new_name
        self.refresh_display()


    def add_speaker(self, speaker):
        if not speaker:
            return

        if speaker in self.speakers:
            ui.notify(f'"{speaker}" already exists', color="error")
            return

        self.speakers.append(speaker)
        self.render_speakers.refresh()

    def prune_speakers(self):
        self.speakers = list(dict.fromkeys(c.speaker for c in self.captions))
        if "UNKNOWN" not in self.speakers:
            self.speakers.append("UNKNOWN")
        self.refresh_display()
        self.render_speakers.refresh()

    def load_words(self, data) -> None:
        """
        Load the word-level timing payload from a JSON result (dict or JSON
        string). Silently a no-op when the job has no word data, so old jobs
        behave exactly as before.
        """

        try:
            if isinstance(data, str):
                data = json.loads(data)
            payload = (data or {}).get("words") or {}
            words = payload.get("words") or []
        except (ValueError, AttributeError, TypeError):
            return

        self.words = [
            w for w in words
            if isinstance(w, dict) and "t" in w and "s" in w and "e" in w
        ]
        self.words_alignment = payload.get("alignment", "heuristic")

    def karaoke_enabled(self) -> bool:
        """
        Preserve the capability gate for existing jobs. The legacy alignment
        label does not guarantee accuracy; per-word provenance distinguishes
        original token times from approximations.
        """
        return bool(self.words) and self.words_alignment in ("dtw", "vad")

    def low_confidence_words(self, caption: SRTCaption) -> list:
        """
        Report the same matched occurrences used by inline underlines.
        Edited, unaligned words have no inherited model confidence.
        """

        return [
            (token, confidence)
            for token, _, _, confidence, provenance in self.caption_word_times(caption)
            if confidence is not None and confidence < self.confidence_threshold
            and provenance.get("word_id") not in self._reviewed_words
        ]

    def confidence_review_targets(self):
        if not self.captions:
            return []
        # Populate the full-document matching cache once, then walk it once.
        self.caption_word_times(self.captions[0])
        return review_targets(self.captions, self._word_match_cache[1], self.confidence_threshold, self._reviewed_words)

    async def navigate_confidence(self, direction=1, replay=False, word_id=None):
        targets = self.confidence_review_targets()
        position = review_index(targets, word_id or self._confidence_review_id, 0 if replay or word_id else direction)
        if position is None:
            self._confidence_review_id = None
            self._confidence_review_status.set_text("No uncertain words with usable timestamps.")
            ui.run_javascript("window.__stopConfidenceReplay?.()")
            self.refresh_display(force_full_refresh=True)
            self.refresh_confidence_review()
            return
        target = targets[position]
        changed = {target["caption_index"], self._confidence_review_caption_index}
        self._confidence_review_id = target["word_id"]
        self._confidence_review_caption_index = target["caption_index"]
        # Review in the collapsed card so the exact occurrence can be marked.
        if self.selected_caption:
            changed.add(self.selected_caption.index)
            self.selected_caption.is_selected = False
            self.selected_caption = None
        if self.search_term:
            self.clear_search()
        self.refresh_display(specific_indices=changed - {None})
        self._confidence_review_status.set_text(
            f'“{target["text"]}” · Caption {target["caption_index"]}'
        )
        self._confidence_review_page = position // 20
        self.refresh_confidence_review()
        container = self.caption_containers.get(target["caption_index"])
        if container:
            ui.run_javascript(f"document.getElementById('c{container.id}')?.scrollIntoView({{block:'center'}})")
        result = await ui.run_javascript(
            f"window.__reviewConfidenceWord({json.dumps(dict(target, replay=replay))})", timeout=6
        )
        if result in ("blocked", "unavailable"):
            ui.notify("Could not replay this word. Check that the video is loaded and can play.", type="warning")

    def end_confidence_review(self):
        previous = self._confidence_review_caption_index
        self._confidence_review_id = None
        self._confidence_review_caption_index = None
        self._confidence_review_status.set_text("")
        ui.run_javascript("window.__stopConfidenceReplay?.(true)")
        self.refresh_confidence_review()
        if previous is not None:
            self.refresh_display(specific_indices={previous})

    def create_confidence_panel(self) -> None:
        """
        Toolbar control for the word-confidence flagging threshold.
        Renders nothing when the job has no word-level data.
        """

        if not self.words:
            return

        with ui.button("Word confidence", icon="signal_cellular_alt").props(
            "flat no-caps icon-right=expand_more", remove="color"
        ):
            ui.tooltip(
                "Highlight words the transcription model is less confident about. "
                "Adjust the threshold to show more or fewer words."
            ).style("max-width: 300px; white-space: normal;")
            with ui.menu():
                with ui.column().classes("p-3").style("min-width: 240px;"):
                    threshold_label = ui.label().classes("text-sm")

                    def update_label() -> None:
                        threshold_label.set_text(
                            f"Flag words below {self.confidence_threshold:.0%} confidence"
                        )

                    update_label()

                    slider = ui.slider(
                        min=0.0,
                        max=1.0,
                        step=0.05,
                        value=self.confidence_threshold,
                    )

                    async def apply_threshold() -> None:
                        self.confidence_threshold = round(slider.value, 2)
                        app.storage.user["confidence_threshold"] = (
                            self.confidence_threshold
                        )
                        update_label()
                        self._confidence_review_id = None
                        self._confidence_review_status.set_text("")
                        ui.run_javascript("window.__stopConfidenceReplay?.()")
                        self.refresh_display(force_full_refresh=True)
                        self.refresh_confidence_review()
                        await self.navigate_confidence()

                    # 'change' fires on release, not on every drag step.
                    slider.on("change", lambda e: apply_threshold())

                    ui.label("Raise the threshold to flag more words; 0% flags none.").classes(
                        "text-xs text-gray-500"
                    )

    async def mark_confidence_reviewed(self):
        targets = self.confidence_review_targets()
        current = next((i for i, t in enumerate(targets)
                        if t["word_id"] == self._confidence_review_id), None)
        if current is None:
            return
        self._reviewed_words.add(self._confidence_review_id)
        app.storage.user[self._reviewed_words_key] = sorted(self._reviewed_words)
        remaining = self.confidence_review_targets()
        if remaining:
            await self.navigate_confidence(word_id=remaining[current % len(remaining)]["word_id"])
        else:
            self.end_confidence_review()
            self._confidence_review_status.set_text("All matching words reviewed.")

    async def reset_confidence_reviewed(self):
        self._reviewed_words.clear()
        app.storage.user[self._reviewed_words_key] = []
        self._confidence_review_status.set_text("")
        self.refresh_display(force_full_refresh=True)
        self.refresh_confidence_review()
        self._confidence_review_id = None
        await self.navigate_confidence()

    def create_confidence_workspace(self):
        """Keep review actions beside the video, outside the settings menu."""
        if not self.words:
            return
        with ui.column().classes("w-full confidence-workspace"):
            with ui.row().classes("w-full items-center justify-between"):
                ui.label("Review uncertain words").classes("text-sm font-semibold")
                with ui.icon("info_outline").classes("opacity-60"):
                    ui.tooltip("Review each occurrence, replay its audio, then mark it reviewed. "
                               "Progress is remembered for you on this transcription; text and confidence scores are unchanged.").style(
                                   "max-width: 300px; white-space: normal;")
            self._confidence_review_status = ui.label("").classes("text-sm font-medium confidence-current")
            with ui.row().classes("items-center gap-2 flex-wrap confidence-actions"):
                ui.button(icon="chevron_left", on_click=lambda: self.navigate_confidence(-1)).props('flat round dense aria-label="Previous word"', remove="color").tooltip("Previous word")
                ui.button(icon="chevron_right", on_click=lambda: self.navigate_confidence(1)).props('flat round dense aria-label="Next word"', remove="color").tooltip("Next word")
                ui.button("Listen", icon="play_arrow", on_click=lambda: self.navigate_confidence(replay=True)).props("flat no-caps", remove="color").tooltip("Play this word with two seconds of surrounding audio, then pause.")
                self._confidence_mark_button = ui.button("Reviewed", icon="done", on_click=self.mark_confidence_reviewed).props("flat no-caps", remove="color").style(
                    "background: color-mix(in srgb, currentColor 7%, transparent); border-radius: 8px;"
                ).tooltip("Mark this word reviewed and move to the next.")
                ui.button(icon="stop", on_click=self.end_confidence_review).props('flat round dense aria-label="Stop review"', remove="color").tooltip("Stop playback and clear the selected word")
            self._confidence_review_list = ui.column().classes("w-full confidence-queue")
            self.refresh_confidence_review()
            client = ui.context.client

            async def start_review():
                await client.connected()
                await self.navigate_confidence()

            ui.timer(0.1, start_review, once=True)

    def refresh_confidence_review(self):
        if self._confidence_review_list is None:
            return
        self._confidence_review_caption_snapshot = tuple((c.index, c.text) for c in self.captions)
        targets = self.confidence_review_targets()
        self._confidence_mark_button.set_enabled(any(t["word_id"] == self._confidence_review_id for t in targets))
        pages = max(1, (len(targets) + 19) // 20)
        self._confidence_review_page = min(self._confidence_review_page, pages - 1)
        self._confidence_review_list.clear()
        with self._confidence_review_list:
            with ui.row().classes("w-full items-center justify-between"):
                ui.label(f"{len(targets)} remaining · {len(self._reviewed_words)} reviewed").classes("text-xs opacity-70")
                if self._reviewed_words:
                    with ui.button(icon="more_horiz").props('flat round dense aria-label="Review options"', remove="color"):
                        with ui.menu() as options:
                            async def reset_review():
                                options.close()
                                with self._confidence_review_list:
                                    await self.reset_confidence_reviewed()
                            ui.menu_item("Reset reviewed words", on_click=reset_review)
            if not targets:
                ui.label("No remaining words at this threshold.").classes("text-sm opacity-70 py-3")
            with ui.column().classes("w-full confidence-words").style("max-height: 280px; overflow-y: auto;"):
                for target in targets[self._confidence_review_page * 20:(self._confidence_review_page + 1) * 20]:
                    async def select_word(word_id=target["word_id"]):
                        # Navigation rebuilds the list; retain its stable slot.
                        with self._confidence_review_list:
                            await self.navigate_confidence(word_id=word_id)
                    selected = target["word_id"] == self._confidence_review_id
                    with ui.button(on_click=select_word).props("flat no-caps align=left", remove="color").classes("w-full confidence-word" + (" confidence-word-selected" if selected else "")):
                        with ui.row().classes("w-full items-center justify-between gap-2"):
                            ui.label(target["text"]).classes("font-medium")
                            ui.label(f'#{target["caption_index"]} · {int(target["start"]) // 60}:{int(target["start"]) % 60:02d} · {target["confidence"]:.0%}').classes("text-xs opacity-60")
            if pages > 1:
                def change_page(delta):
                    self._confidence_review_page = max(0, min(pages - 1, self._confidence_review_page + delta))
                    self.refresh_confidence_review()
                with ui.row().classes("w-full items-center justify-end confidence-pages"):
                    ui.label(f"Page {self._confidence_review_page + 1} of {pages}").classes("text-xs")
                    ui.button(icon="chevron_left", on_click=lambda: change_page(-1)).props('flat dense round size=sm aria-label="Previous page"', remove="color").set_enabled(self._confidence_review_page > 0)
                    ui.button(icon="chevron_right", on_click=lambda: change_page(1)).props('flat dense round size=sm aria-label="Next page"', remove="color").set_enabled(self._confidence_review_page < pages - 1)

    def caption_word_times(self, caption: SRTCaption) -> list:
        """Return text, timing, confidence and provenance for each display word.

        Original occurrence indices survive changes to card boundaries.
        Unmatched edited words have no timing or confidence, never invented
        timestamps. Matching is cached until text or the source payload changes.
        """
        texts = tuple(c.text for c in self.captions)
        key = (id(self.words), texts)
        if self._word_match_cache is None or self._word_match_cache[0] != key:
            tokens = [token for text in texts for token in text.split()]
            matches = match_word_indices(self.words, tokens)
            rows = []
            for token, source_index in zip(tokens, matches):
                if source_index is None:
                    rows.append((token, None, None, None, {
                        "source": "unaligned", "adjustments": [], "word_id": None,
                    }))
                else:
                    word = self.words[source_index]
                    rows.append((token, word["s"], word["e"], word.get("c"), {
                        "source": word.get("timing_source", "unknown"),
                        "adjustments": word.get("timing_adjustments", []),
                        "word_id": f"{self.uuid}:{source_index}",
                    }))
            self._word_match_cache = (key, rows)
        offset = 0
        for current in self.captions:
            count = len(current.text.split())
            if current is caption:
                return self._word_match_cache[1][offset:offset + count]
            offset += count
        return []

    def render_caption_text(self, caption: SRTCaption) -> None:
        """
        Caption text for the list view. With word timings available, each
        word becomes a click-to-seek span and low-confidence words get a
        wavy underline; otherwise a plain label, exactly as before.
        """

        if not self.words:
            ui.label(caption.text).classes(
                "text-sm leading-relaxed whitespace-pre-wrap caption-body"
            )
            return

        word_times = self.caption_word_times(caption)

        # Rebuild the caption line by line so subtitle line breaks survive
        # (str.split() in caption_word_times flattens them, but its token
        # order matches iterating the lines in order).
        lines_html = []
        index = 0
        for line in caption.text.split("\n"):
            spans = []
            for _ in line.split():
                if index >= len(word_times):
                    break
                token, start, end, confidence, provenance = word_times[index]
                index += 1
                if start is None or end is None:
                    spans.append(
                        '<span data-timing-source="unaligned">'
                        f"{html_module.escape(token)}</span>"
                    )
                    continue
                css = "w-seek"
                if provenance["word_id"] == getattr(self, "_confidence_review_id", None):
                    css += " w-confidence-review"
                if (
                    confidence is not None
                    and confidence < self.confidence_threshold
                    and provenance.get("word_id") not in self._reviewed_words
                ):
                    css += " w-lowconf"
                spans.append(
                    f'<span class="{css}" data-s="{start:.3f}" data-e="{end:.3f}" '
                    f'data-word-id="{html_module.escape(provenance["word_id"], quote=True)}" '
                    f'data-timing-source="{html_module.escape(str(provenance["source"]), quote=True)}" '
                    f'data-timing-adjustments="{html_module.escape(json.dumps(provenance["adjustments"]), quote=True)}">'
                    f"{html_module.escape(token)}</span>"
                )
            lines_html.append(" ".join(spans))

        ui.html("\n".join(lines_html), sanitize=False).classes(
            "text-sm leading-relaxed whitespace-pre-wrap caption-body"
        )

    def render_confidence_badge(self, caption: SRTCaption) -> None:
        """
        Warning badge listing the caption's low-confidence words, so
        proofreaders can jump straight to the likely errors. Renders
        nothing when there is no word data or nothing suspicious.
        """

        flagged = self.low_confidence_words(caption)
        if not flagged:
            return

        listing = ", ".join(f"{text} ({conf:.0%})" for text, conf in flagged[:8])
        if len(flagged) > 8:
            listing += f" +{len(flagged) - 8} more"

        with ui.icon("spellcheck").classes(
            "text-orange-500 text-sm confidence-badge"
        ):
            ui.tooltip(f"Low-confidence words: {listing}")

    def parse_txt(self, data: dict) -> None:
        """
        Parse TXT content and populate captions list.
        """

        self.data_format = "txt"

        original_data = json.loads(data)

        self.speakers.append("UNKNOWN")

        if not original_data.get("segments"):
            return

        raw_segments = original_data["segments"]

        if not raw_segments:
            return

        max_words = 50

        concatenated = []
        current = raw_segments[0].copy()

        for segment in raw_segments[1:]:
            word_count = len(current["text"].split())
            past_limit = word_count >= max_words
            if segment["speaker"] != current["speaker"]:
                concatenated.append(current)
                current = segment.copy()
            elif past_limit and current["text"].rstrip().endswith("."):
                concatenated.append(current)
                current = segment.copy()
            else:
                current["text"] += " " + segment["text"]
                current["end"] = segment["end"]
                current["duration"] = current["end"] - current["start"]

        concatenated.append(current)

        import re

        def capitalize_after_periods(text: str) -> str:
            return re.sub(r'(\.\s+)([a-z])', lambda m: m.group(1) + m.group(2).upper(), text)

        for index, seg in enumerate(concatenated):
            if seg.get("text", "").strip():
                seg["text"] = capitalize_after_periods(seg["text"])
                seg["text"] = seg["text"][0].upper() + seg["text"][1:]
                start_time = self.seconds_to_timestamp(seg.get("start", 0.0))
                end_time = self.seconds_to_timestamp(seg.get("end", 0.0))

                self.captions.append(
                    SRTCaption(
                        index,
                        start_time,
                        end_time,
                        seg["text"],
                        speaker=seg["speaker"]
                    )
                )
                seg_speaker = seg["speaker"].strip()
                if seg_speaker not in self.speakers:
                    self.speakers.append(seg_speaker)

    def parse_srt(self, srt_content: str) -> None:
        """
        Parse SRT content and populate captions list.
        """

        self.data_format = "srt"

        caption_blocks = re.split(r"\n\s*\n", srt_content.strip())

        for block in caption_blocks:
            if not block.strip():
                continue

            lines = block.strip().split("\n")
            if len(lines) < 3:
                continue

            try:
                index = int(lines[0])
                timestamp_line = lines[1]

                lines[2:] = [line.lstrip() for line in lines[2:]]
                text = "\n".join(lines[2:])

                # Parse timestamp
                if " --> " in timestamp_line:
                    start_time, end_time = timestamp_line.split(" --> ")
                    caption = SRTCaption(
                        index, start_time.strip(), end_time.strip(), text
                    )
                    self.captions.append(caption)
            except (ValueError, IndexError):
                continue

        self.renumber_captions()

    def export_csv(self) -> str:
        """
        Export to CSV format.
        Fields: Start time, stop time, speaker, text
        """
        lines = []
        for caption in self.captions:
            escaped_text = caption.text.replace('"', '""')
            lines.append(
                f'"{caption.start_time}","{caption.end_time}","{caption.speaker}","{escaped_text}"'
            )
        return "\n".join(lines)

    def export_tsv(self) -> str:
        """
        Export to TSV format.
        Fields: Start time, stop time, speaker, text
        """
        lines = []
        for caption in self.captions:
            escaped_text = caption.text.replace("\t", "    ").replace("\n", " ")
            lines.append(
                f"{caption.start_time}\t{caption.end_time}\t{caption.speaker}\t{escaped_text}"
            )
        return "\n".join(lines)

    def export_rtf(
        self,
        speakers: bool,
        times: bool,
        block_nr: bool,
        ts_which: str = "both",
        ts_fmt: str = "srt",
    ) -> str:
        """
        Export captions to RTF format with proper Unicode handling.
        """

        def fmt_ts(ts: str, f: str) -> str:
            """
            Format timestamp according to selected format.
            """
            p = ts.replace(",", ":").split(":")
            h, m, s, ms = int(p[0]), int(p[1]), int(p[2]), int(p[3])
            if f == "seconds":
                return f"{h*3600 + m*60 + s + ms/1000:.3f}"
            if f == "ms":
                return str(h * 3600000 + m * 60000 + s * 1000 + ms)
            if f == "vtt":
                return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
            return ts

        def to_rtf_unicode(text: str) -> str:
            result = []
            for ch in text:
                code = ord(ch)
                if code < 128:
                    if ch in ["\\", "{", "}"]:
                        result.append("\\" + ch)
                    else:
                        result.append(ch)
                elif code <= 0xFFFF:
                    # BMP character - use signed 16-bit representation
                    signed_code = code if code <= 0x7FFF else code - 0x10000
                    result.append(f"\\u{signed_code}?")
                else:
                    # Supplementary character (outside BMP) - use UTF-16 surrogate pair
                    code -= 0x10000
                    high_surrogate = 0xD800 + (code >> 10)
                    low_surrogate = 0xDC00 + (code & 0x3FF)
                    # Surrogates are always > 0x7FFF, convert to signed
                    result.append(
                        f"\\u{high_surrogate - 0x10000}?\\u{low_surrogate - 0x10000}?"
                    )
            return "".join(result)

        rtf_content = (
            r"{\rtf1\ansi\deff0{\fonttbl{\f0 Arial;}}" r"\viewkind4\uc1\pard\f0\fs20 "
        )

        parts = []

        for caption in self.captions:
            header_parts = []
            if block_nr:
                header_parts.append(to_rtf_unicode(f"[{caption.index}]"))

            if times:
                ts_parts = []
                if ts_which in ["start", "both"]:
                    ts_parts.append(fmt_ts(caption.start_time, ts_fmt))
                if ts_which in ["end", "both"]:
                    ts_parts.append(fmt_ts(caption.end_time, ts_fmt))
                if ts_parts:
                    header_parts.append(to_rtf_unicode(f"({' - '.join(ts_parts)})"))

            if speakers:
                header_parts.append(to_rtf_unicode(f"{caption.speaker}:"))

            # Build RTF data with consistent bold formatting for header
            rtf_data = ""
            if header_parts:
                rtf_data = r"\b " + " ".join(header_parts) + r"\b0\line "

            rtf_data += (
                to_rtf_unicode(caption.text).replace("\n", r"\line ") + r"\line\line "
            )

            parts.append(rtf_data)

        rtf_content += "".join(parts) + "}"

        return rtf_content

    def export_txt(self) -> str:
        """
        Export captions to TXT format.
        """

        txt_content = "\n\n".join(
            f"{caption.speaker}: {caption. start_time} - {caption.end_time}\n{caption.text}"
            for caption in self.captions
        )

        return txt_content.strip()

    def export_json(self) -> str:
        return {
            "segments": [seg.to_dict() for seg in self.captions],
            "speaker_count": len(self.speakers),
            "full_transcription": " ".join(seg.text for seg in self.captions),
        }

    def export_srt(self) -> str:
        """
        Export captions to SRT format.
        """

        return "\n\n".join(caption.to_srt_format() for caption in self.captions)

    def export_vtt(self) -> str:
        """
        Export captions to VTT format.
        """

        parts = ["WEBVTT\n\n"]
        for caption in self.captions:
            parts.append(f"{caption.index}\n")
            parts.append(
                f"{caption.start_time.replace(',', '.')} --> {caption.end_time.replace(',', '.')}\n"
            )
            parts.append(f"{caption.text}\n\n")
        return "".join(parts)

    def renumber_captions(self) -> None:
        """
        Renumber all captions sequentially.
        """

        for i, caption in enumerate(self.captions, 1):
            caption.index = i

    def format_time_display(self, timestamp: str) -> str:
        """
        Format timestamp for display.
        """

        return str(timestamp).replace(",", ".")

    def seconds_to_timestamp(self, seconds: float) -> str:
        """
        Convert seconds back to SRT timestamp format.
        """

        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        milliseconds = int((secs % 1) * 1000)
        secs = int(secs)

        return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"

    def search_captions(self, search_term: str) -> None:
        """
        Search for captions containing the search term.
        """

        self.search_term = search_term
        self.search_results = []

        # Track which captions change highlight state
        changed_indices = set()

        # Clear previous highlights
        for caption in self.captions:
            if caption.is_highlighted:
                caption.is_highlighted = False
                changed_indices.add(caption.index)

        if not search_term.strip():
            self.refresh_display(
                specific_indices=changed_indices if changed_indices else None
            )
            self.update_search_info()
            return

        # Find matching occurrences: one result per occurrence, so several
        # matches inside one caption can each be navigated to.
        pattern = self._search_pattern(search_term)

        for i, caption in enumerate(self.captions):
            occurrences = len(pattern.findall(caption.text))
            if occurrences:
                self.search_results.extend(
                    (i, occurrence) for occurrence in range(occurrences)
                )
                caption.is_highlighted = True
                changed_indices.add(caption.index)

        self.current_search_index = 0
        self.refresh_display(
            specific_indices=changed_indices if changed_indices else None
        )
        self.update_search_info()

        if self.search_results:
            self.scroll_to_result(0)

    def navigate_search_results(self, direction: int) -> None:
        """
        Navigate through search results (direction:  1 for next, -1 for previous).
        """
        if not self.search_results:
            return

        previous = self._current_match()
        self.current_search_index = (self.current_search_index + direction) % len(
            self.search_results
        )
        current = self._current_match()

        # Re-render the captions whose current-match marking changed.
        changed = set()
        for match in (previous, current):
            if match is not None and match[0] < len(self.captions):
                changed.add(self.captions[match[0]].index)
        if changed:
            self.refresh_display(specific_indices=changed)

        self.scroll_to_result(self.current_search_index)
        self.update_search_info()

    def scroll_to_result(self, result_index: int) -> None:
        """
        Scroll the caption holding the given search result into view —
        without selecting it, so the highlight marks stay visible.
        """
        if not self.search_results or result_index >= len(self.search_results):
            return

        caption_position = self.search_results[result_index][0]
        if caption_position >= len(self.captions):
            return

        container = self.caption_containers.get(
            self.captions[caption_position].index
        )
        if container is not None:
            ui.run_javascript(
                f"document.getElementById('c{container.id}')"
                "?.scrollIntoView({behavior: 'smooth', block: 'center'});"
            )

    def replace_in_current_caption(self, replacement: str) -> None:
        """
        Replace the current search occurrence (falling back to the selected
        caption when there is no active search result).
        """
        if not self.search_term:
            ui.notify("Search term empty", type="warning")
            return

        match = self._current_match()
        if match is not None and match[0] < len(self.captions):
            caption, occurrence = self.captions[match[0]], match[1]
        elif self.selected_caption:
            caption, occurrence = self.selected_caption, 0
        else:
            ui.notify("No search result or caption selected", type="warning")
            return

        pattern = self._search_pattern()
        spans = [m.span() for m in pattern.finditer(caption.text)]

        if occurrence >= len(spans):
            ui.notify("Current caption doesn't contain search term", type="warning")
            return

        # Save state before making changes
        self.save_state_for_undo()

        start, end = spans[occurrence]
        caption.text = caption.text[:start] + replacement + caption.text[end:]

        # Occurrences shifted: rebuild results. current_search_index now
        # naturally points at the next remaining occurrence.
        keep_index = self.current_search_index
        self.search_captions(self.search_term)
        if self.search_results:
            previous = self._current_match()
            self.current_search_index = min(
                keep_index, len(self.search_results) - 1
            )
            current = self._current_match()

            changed = set()
            for shifted in (previous, current):
                if shifted is not None and shifted[0] < len(self.captions):
                    changed.add(self.captions[shifted[0]].index)
            if changed:
                self.refresh_display(specific_indices=changed)

            self.scroll_to_result(self.current_search_index)
            self.update_search_info()

        ui.notify("Replacement made", type="positive")

    def replace_all(self, replacement: str) -> None:
        """
        Replace search term in all matching captions.
        """
        if not self.search_term:
            ui.notify("No search term entered", type="warning")
            return

        pattern = self._search_pattern()

        # Check if there are any matches before saving state
        has_matches = any(
            pattern.search(caption.text) for caption in self.captions
        )

        if has_matches:
            # Save state before making changes
            self.save_state_for_undo()

        count = 0
        for caption in self.captions:
            new_text, replaced = pattern.subn(lambda m: replacement, caption.text)
            if replaced:
                caption.text = new_text
                count += replaced

        if count > 0:
            # Refresh search results
            self.search_captions(self.search_term)
            ui.notify(f"Replaced {count} occurrences", type="positive")
        else:
            ui.notify("No matches found to replace", type="info")

    def update_search_info(self) -> None:
        """
        Update search information display.
        """
        if hasattr(self, "search_info_label") and self.search_info_label:
            if self.search_results:
                info_text = f"{self.current_search_index + 1} of {len(self.search_results)} matches"
            else:
                info_text = "No matches" if self.search_term else ""
            self.search_info_label.set_text(info_text)

    def _search_pattern(self, term: str = None, group: bool = False) -> re.Pattern:
        """
        Compiled regex for the search term honouring the case-sensitive and
        exact-word toggles. With exact_match, "prøve" no longer matches
        inside "prøver" (\\b is unicode-aware, so øæå are word characters).
        """
        escaped = re.escape(term if term is not None else self.search_term)
        if group:
            escaped = f"({escaped})"
        if self.exact_match:
            escaped = rf"\b{escaped}\b"
        flags = 0 if self.case_sensitive else re.IGNORECASE
        return re.compile(escaped, flags)

    def _card_time_attrs(self, caption: SRTCaption) -> str:
        """
        data-s/data-e attributes for a caption card, so the client-side
        follow-mode script can find the current caption during playback.
        """
        try:
            return (
                f'data-s="{caption.get_start_seconds():.3f}" '
                f'data-e="{caption.get_end_seconds():.3f}"'
            )
        except (ValueError, AttributeError):
            return ""

    def _current_match(self) -> Optional[tuple]:
        """
        The current search result as (caption_position, occurrence_index),
        or None when there are no results.
        """
        if self.search_results and 0 <= self.current_search_index < len(
            self.search_results
        ):
            return self.search_results[self.current_search_index]
        return None

    def get_highlighted_text(self, text: str, caption: SRTCaption = None) -> str:
        """
        Get text with search term highlighted (for display purposes).
        Every occurrence is marked; when the caption holds the current
        search result, that specific occurrence gets a distinct colour.
        """
        if not self.search_term or not text:
            return text

        # Which occurrence within this caption is the current match?
        current_occurrence = None
        match = self._current_match()
        if match is not None and caption is not None:
            try:
                if self.captions.index(caption) == match[0]:
                    current_occurrence = match[1]
            except ValueError:
                pass

        pattern = self._search_pattern(group=True)

        counter = {"n": -1}

        def mark(m: re.Match) -> str:
            counter["n"] += 1
            if counter["n"] == current_occurrence:
                return (
                    '<mark style="background-color: orange; padding: 2px;">'
                    f"{m.group(1)}</mark>"
                )
            return (
                '<mark style="background-color: yellow; padding: 2px;">'
                f"{m.group(1)}</mark>"
            )

        return pattern.sub(mark, text)

    @staticmethod
    def reflow_split_text(text: str) -> str:
        """Discard stale line breaks and balance a two-line subtitle."""
        words = text.split()
        joined = " ".join(words)
        if len(joined) <= CHARACTER_LIMIT or len(words) < 2:
            return joined
        cuts = range(2, len(words) - 1) if len(words) >= 4 else range(1, len(words))
        def score(cut):
            left, right = " ".join(words[:cut]), " ".join(words[cut:])
            return (max(len(left), len(right)), abs(len(left) - len(right)))
        cut = min(cuts, key=score)
        return " ".join(words[:cut]) + "\n" + " ".join(words[cut:])

    def split_at_word(self, caption: SRTCaption, word_index: int, expected_text: str) -> bool:
        """Start the second caption at a matched word; reject stale selections."""
        if caption not in self.captions or caption.text != expected_text:
            ui.notify("The caption changed. Place the cursor again.", type="warning")
            return False
        tokens = list(re.finditer(r"\S+", caption.text))
        timings = self.caption_word_times(caption)
        if not 0 < word_index < len(tokens) or word_index >= len(timings):
            return False
        boundary = timings[word_index][1]
        start, end = caption.get_start_seconds(), caption.get_end_seconds()
        if boundary is None or not start < round(boundary, 3) < end:
            ui.notify("This word has no usable split timing.", type="warning")
            return False
        boundary = round(boundary, 3)
        cut = tokens[word_index].start()
        first = self.reflow_split_text(caption.text[:cut])
        second = self.reflow_split_text(caption.text[cut:])
        self.save_state_for_undo()
        old_end = caption.end_time
        caption.text = first
        total_ms = round(boundary * 1000)
        hours, remainder = divmod(total_ms, 3600000)
        minutes, remainder = divmod(remainder, 60000)
        seconds, milliseconds = divmod(remainder, 1000)
        caption.end_time = f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"
        new_caption = SRTCaption(
            caption.index + 1, caption.end_time, old_end, second,
        )
        new_caption.speaker = caption.speaker
        new_caption.generated_speaker = caption.generated_speaker
        self.captions.insert(self.captions.index(caption) + 1, new_caption)
        self.renumber_captions()
        self.update_words_per_minute()
        self.refresh_display(force_full_refresh=True)
        return True

    def split_at_cursor(self, caption: SRTCaption, text: str, cursor: int) -> bool:
        """Split before the word containing/following the caret, never mid-word."""
        if caption not in self.captions or not isinstance(cursor, int) or not 0 <= cursor <= len(text):
            return False
        tokens = list(re.finditer(r"\S+", text))
        index = next((i for i, token in enumerate(tokens) if token.end() > cursor), None)
        if index is None or index == 0:
            ui.notify("Place the cursor between words, leaving text on both sides.", type="info")
            return False
        # Capture the textarea value supplied with the caret, including edits
        # not yet delivered by the normal blur event.
        if caption.text != text:
            self.update_caption_text(caption, text)
        return self.split_at_word(caption, index, text)

    def create_split_cursor_button(self, caption: SRTCaption, text_area) -> None:
        button = ui.button("Split at cursor", icon="call_split").props("flat dense")
        # Keep textarea focus/caret on pointer clicks; keyboard activation
        # can still read the textarea's retained selectionStart.
        button.on("mousedown", js_handler="e => e.preventDefault()")
        button.on(
            "click",
            lambda e: (
                ui.notify("Could not read the cursor. Click inside the text and try again.", type="warning")
                if e.args.get("error") else
                self.split_at_cursor(caption, e.args["text"], e.args["cursor"])
            ),
            js_handler=f"""(event) => {{
                event.stopPropagation();
                const field = getElement({text_area.id})?.$refs?.qRef?.getNativeElement?.();
                if (!field || field.selectionStart === null) {{
                    emit({{error: 'textarea unavailable'}});
                    return;
                }}
                emit({{text: field.value,
                    cursor: [...field.value.slice(0, field.selectionStart)].length}});
            }}""",
        )
        with button:
            ui.tooltip("Place the cursor in the text. The word to its right starts the next caption.")

    def split_caption(self, caption: SRTCaption) -> None:
        """
        Split a caption into two parts.
        """

        if not caption:
            return

        # Save state before making changes
        self.save_state_for_undo()

        text_lines = caption.text.split("\n")

        if len(text_lines) == 1:
            # Split single line in half
            text = caption.text
            mid_point = len(text) // 2
            # Find nearest space to split at
            while mid_point > 0 and text[mid_point] != " ":
                mid_point -= 1
            if mid_point == 0:
                mid_point = len(text) // 2

            first_part = text[:mid_point].strip()
            second_part = text[mid_point:].strip()
        else:
            # Split at middle line
            mid_line = len(text_lines) // 2
            first_part = "\n".join(text_lines[:mid_line])
            second_part = "\n".join(text_lines[mid_line:])

        # Calculate time split
        start_seconds = caption.get_start_seconds()
        end_seconds = caption.get_end_seconds()
        mid_seconds = (start_seconds + end_seconds) / 2

        # Update first caption
        caption.text = first_part
        caption.end_time = self.seconds_to_timestamp(mid_seconds)

        # Create second caption
        new_caption = SRTCaption(
            caption.index + 1,
            self.seconds_to_timestamp(mid_seconds),
            self.seconds_to_timestamp(end_seconds),
            second_part,
        )

        # Insert new caption
        caption_index = self.captions.index(caption)
        self.captions.insert(caption_index + 1, new_caption)

        self.renumber_captions()
        self.update_words_per_minute()
        self.refresh_display(force_full_refresh=True)

    def add_caption_after(self, caption: SRTCaption) -> None:
        """
        Add a new caption after the selected one.
        """

        # Save state before making changes
        self.save_state_for_undo()

        # Calculate new caption timing
        start_seconds = caption.get_end_seconds()

        # Find next caption or add 3 seconds if it's the last one
        caption_index = self.captions.index(caption)
        if caption_index < len(self.captions) - 1:
            next_caption = self.captions[caption_index + 1]
            end_seconds = next_caption.get_start_seconds()
        else:
            end_seconds = start_seconds + 3.0

        # Create new caption
        new_caption = SRTCaption(
            caption.index + 1,
            self.seconds_to_timestamp(start_seconds),
            self.seconds_to_timestamp(end_seconds),
            "New caption text",
        )

        # Insert new caption
        self.captions.insert(caption_index + 1, new_caption)

        self.renumber_captions()
        self.refresh_display(force_full_refresh=True)
        self.update_words_per_minute()

    def remove_caption(self, caption: SRTCaption) -> None:
        """
        Remove a caption.
        """

        if not caption:
            return

        if len(self.captions) > 1:  # Don't remove if it's the only caption
            # Save state before making changes
            self.save_state_for_undo()

            self.captions.remove(caption)
            self.renumber_captions()
            self.refresh_display(force_full_refresh=True)
        else:
            ui.notify("Cannot remove the only remaining caption", type="warning")

        self.update_words_per_minute()

    def select_caption(
        self,
        caption: SRTCaption,
        speaker: Optional[ui.input] = None,
        button: Optional[bool] = False,
        seek: Optional[bool] = True,
        new_text: Optional[str] = None,
    ) -> None:
        """
        Select/deselect a caption.
        """

        if speaker:
            if str(speaker.value) not in self.speakers:
                self.speakers.append(speaker.value)
            self.selected_caption.speaker = speaker.value

        old_selected = self.selected_caption

        if self.selected_caption:
            self.selected_caption.is_selected = False

        if self.selected_caption == caption:
            self.selected_caption = None
        else:
            caption.is_selected = True
            self.selected_caption = caption

            # Get caption start time
            if self.__video_player and seek:
                start_seconds = caption.get_start_seconds()
                seek_seconds = start_seconds
                if self.subtitle_preview:
                    # Chrome may not activate a cue on a paused seek exactly
                    # at its start. Seek just inside the preview interval,
                    # bounded by both this caption and the next cue start.
                    end_seconds = caption.get_end_seconds()
                    next_start = min(
                        (c.get_start_seconds() for c in self.captions
                         if c.get_start_seconds() > start_seconds),
                        default=end_seconds + 0.001,
                    )
                    preview_end = min(end_seconds, next_start - 0.001)
                    if preview_end > start_seconds:
                        seek_seconds += min(0.01, (preview_end - start_seconds) / 2)
                self.__video_player.seek(seek_seconds)

        if new_text is not None and new_text != caption.text:
            self.update_caption_text(
                caption, caption.text, force=True
            )  # To mark as changed
            caption.text = new_text

        self.update_words_per_minute()

        # Only update the captions that changed state
        indices_to_update = set()
        if old_selected:
            indices_to_update.add(old_selected.index)
        if caption:
            indices_to_update.add(caption.index)
        self.refresh_display(specific_indices=indices_to_update)

        if self.selected_caption:
            ui.run_javascript(
                """
                requestAnimationFrame(() => {
                    const el = document.getElementById("action_row");
                    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
                });
                """
            )

    def update_caption_text(
        self, caption: SRTCaption, new_text: str, force: Optional[bool] = False
    ) -> None:
        """
        Update caption text.
        """

        # Only save state if text actually changed
        if caption.text != new_text or force:
            self.save_state_for_undo()
            caption.text = new_text

    def update_caption_timing(
        self, caption: SRTCaption, start_time: str, end_time: str
    ) -> None:
        """
        Update caption timing.
        """
        # Only save state if timing actually changed
        if caption.start_time != start_time or caption.end_time != end_time:
            self.save_state_for_undo()
            caption.start_time = start_time
            caption.end_time = end_time
            # Only update this specific caption
            self.refresh_display(specific_indices={caption.index})

    def clear_search(self) -> None:
        """
        Clear the search state and remove all match highlighting from the
        captions. Called when the search dialog closes.
        """
        if not self.search_term and not self.search_results:
            return

        self.search_term = ""
        self.search_results = []
        self.current_search_index = 0

        changed = {c.index for c in self.captions if c.is_highlighted}
        for caption in self.captions:
            caption.is_highlighted = False

        if changed:
            self.refresh_display(specific_indices=changed)
        self.update_search_info()

    def open_search_panel(self) -> None:
        """
        Open the existing search dialog (creating it first if needed) and
        focus the search input so typing can start immediately. Reuses one
        dialog, so repeated Ctrl+F cannot stack panels.
        """

        if self.search_container is None:
            self.create_search_panel()

        self.search_container.open()

        if self._search_input is not None:
            # Focus via QInput's own methods once the dialog's open
            # animation has finished; select the previous term so typing
            # replaces it.
            def focus_input() -> None:
                self._search_input.run_method("focus")
                self._search_input.run_method("select")

            ui.timer(0.3, focus_input, once=True)

    def create_search_panel(self, open_window: Optional[bool] = False) -> None:
        """
        Create the search panel UI.
        """

        with ui.dialog() as self.search_container:
            # Closing the dialog (button, Esc or click outside) ends the
            # search: remove the match highlighting from the captions.
            self.search_container.on("hide", lambda: self.clear_search())
            with ui.card().classes("w-1/2 max-w-full").style("padding: 16px;"):
                # Title
                ui.label("Find & Replace").classes("text-h6 mb-3")

                # FIND SECTION
                with ui.column().classes("w-full gap-2"):
                    ui.label("Find").classes("text-caption text-gray-600")

                    with ui.row().classes("w-full items-center gap-2"):
                        search_input = (
                            ui.input(
                                placeholder="Search in captions…",
                                value=self.search_term,
                            )
                            .classes("flex-1")
                            .props("outlined dense clearable")
                        )
                        self._search_input = search_input

                        ui.button(icon="search").props("flat dense round", remove="color").on(
                            "click", lambda: self.search_captions(search_input.value)
                        ).tooltip(
                            "Find"
                        )

                    # Toggling an option re-runs the search and hands focus
                    # back to the search box, so Enter keeps navigating
                    # results instead of re-toggling the checkbox.
                    def on_option_toggle() -> None:
                        if self.search_term:
                            self.search_captions(search_input.value)
                        search_input.run_method("focus")

                    with ui.row().classes("w-full items-center justify-between mt-1"):
                        with ui.row().classes("items-center gap-4"):
                            ui.checkbox("Case sensitive").bind_value_to(
                                self, "case_sensitive"
                            ).on(
                                "update:model-value",
                                lambda: on_option_toggle(),
                            )

                            with ui.checkbox("Exact word").bind_value_to(
                                self, "exact_match"
                            ).on(
                                "update:model-value",
                                lambda: on_option_toggle(),
                            ):
                                ui.tooltip(
                                    'Match whole words only — "prøve" will not match "prøver".'
                                )

                        # Navigation + info
                        with ui.row().classes("items-center gap-1"):
                            ui.button(icon="keyboard_arrow_up").props("flat dense round", remove="color").on(
                                "click", lambda: self.navigate_search_results(-1)
                            ).tooltip(
                                "Previous match"
                            )
                            ui.button(icon="keyboard_arrow_down").props("flat dense round", remove="color").on(
                                "click", lambda: self.navigate_search_results(1)
                            ).tooltip(
                                "Next match"
                            )

                            self.search_info_label = ui.label("").classes(
                                "text-caption text-gray-600"
                            )

                ui.separator().classes("my-3")

                # REPLACE SECTION
                with ui.column().classes("w-full gap-2"):
                    ui.label("Replace").classes("text-caption text-gray-600")

                    replace_input = (
                        ui.input(
                            placeholder="Replace with…",
                        )
                        .classes("w-full")
                        .props("outlined dense clearable")
                    )

                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button("Replace").props("flat dense", remove="color").on(
                            "click",
                            lambda: self.replace_in_current_caption(
                                replace_input.value
                            ),
                        )

                        ui.button("Replace all").props("flat dense", remove="color").on(
                            "click", lambda: self.replace_all(replace_input.value)
                        )

                ui.separator().classes("my-3")

                with ui.row().classes("w-full justify-end"):
                    ui.button("Close").props("flat dense", remove="color").on(
                        "click", self.search_container.close
                    )

                # Enter runs the search; further Enters on the same term
                # jump to the next match (Shift+Enter to the previous).
                def on_search_enter(shift: bool = False) -> None:
                    term = search_input.value or ""
                    if term == self.search_term and self.search_results:
                        self.navigate_search_results(-1 if shift else 1)
                    else:
                        self.search_captions(term)

                search_input.on(
                    "keydown.enter.exact",
                    lambda: on_search_enter(),
                )
                search_input.on(
                    "keydown.shift.enter",
                    lambda: on_search_enter(shift=True),
                )

        if open_window:
            self.open_search_panel()
        else:
            ui.button("Search").props("icon=search flat dense", remove="color").on(
                "click", lambda: self.open_search_panel()
            ).classes("button-open-search")

    def merge_with_next(self, caption: SRTCaption) -> None:
        """
        Merge the current caption with the next one.
        Update the current cation with the text and end_time from
        the next caption and remove the next caption.
        """

        caption_index = self.captions.index(caption)
        if caption_index == len(self.captions) - 1:
            ui.notify("No next caption to merge with", type="warning")
            return

        # Save state before making changes
        self.save_state_for_undo()

        next_caption = self.captions[caption_index + 1]

        # Merge text and update end time
        caption.text += "\n" + next_caption.text
        caption.end_time = next_caption.end_time

        # Remove next caption
        self.captions.remove(next_caption)

        self.renumber_captions()
        self.update_words_per_minute()
        self.refresh_display(force_full_refresh=True)

    def merge_with_previous(self, caption: SRTCaption) -> None:
        """
        Merge the current caption with the previous one.
        Update the current cation with the text and end_time from
        the previous caption and remove the previous caption.
        """

        caption_index = self.captions.index(caption)
        if caption_index == 0:
            ui.notify("No previous caption to merge with", type="warning")
            return

        # Save state before making changes
        self.save_state_for_undo()

        previous_caption = self.captions[caption_index - 1]

        # Merge text and update end time
        previous_caption.text += "\n" + caption.text
        previous_caption.end_time = caption.end_time

        # Remove current caption
        self.captions.remove(caption)

        self.renumber_captions()
        self.update_words_per_minute()
        self.refresh_display(force_full_refresh=True)


    def create_caption_card(self, caption: SRTCaption) -> ui.card:
        """
        Create a visual card for a caption.
        """

        card_class = "cursor-pointer border-0 transition-all duration-200 w-full"
        # Semantic states are styled only under the Minimal appearance scope.
        if caption.is_selected:
            card_class += " caption-editing"
        if not caption.is_valid:
            card_class += " caption-invalid"
        if caption.is_highlighted:
            card_class += " caption-search-match"

        if not caption.is_valid:
            card_class += " border-red-400 bg-red-50 hover:border-red-500"
        elif caption.is_selected and caption.is_highlighted:
            # Slightly darker yellow background
            card_class += (
                " shadow-lg border-yellow-400 bg-yellow-100 hover:border-yellow-500"
            )
        elif caption.is_selected:
            card_class += " shadow-lg"
        elif caption.is_highlighted:
            card_class += " border-yellow-400 bg-yellow-50 hover:border-yellow-500"
        else:
            card_class += " hover:shadow-md shadow-none"

        # Create container for this caption that persists
        container = ui.column().classes("w-full")

        with container:
            with ui.card().classes(card_class + " caption-card").props(
                self._card_time_attrs(caption)
            ) as card:
                # Caption text (editable when selected)
                if caption.is_selected:
                    with ui.row().classes("w-full justify-between") as action_row:
                        action_row.props("id=action_row").classes("caption-meta-row")
                        ui.label(f"#{caption.index}").classes(
                            "font-bold text-sm text-gray-500"
                        )

                        speaker_select = None

                        start_input = ui.input("", value=caption.start_time).props(
                            "dense borderless"
                        ).classes("caption-time-input")
                        end_input = ui.input("", value=caption.end_time).props(
                            "dense borderless"
                        ).classes("caption-time-input")

                    start_input.on(
                        "blur",
                        lambda: self.update_caption_timing(
                            caption, start_input.value, end_input.value
                        ),
                    )
                    end_input.on(
                        "blur",
                        lambda: self.update_caption_timing(
                            caption, start_input.value, end_input.value
                        ),
                    )

                    text_area = (
                        ui.textarea(value=caption.text)
                        .classes("w-full caption-text-input")
                        .props("outlined input-class=h-32")
                    )
                    text_area.on(
                        "blur",
                        lambda e: self.update_caption_text(caption, e.sender.value),
                    )

                    # Action buttons
                    # Row with buttons to the left
                    with ui.row().classes("w-full justify-between caption-btn-row"):
                        if self.words:
                            self.create_split_cursor_button(caption, text_area)
                        ui.button("Merge with previous", icon="merge_type").props(
                            "flat dense"
                        ).on(
                            "click",
                            lambda: (
                                self.merge_with_previous(caption)
                                if self.captions.index(caption) > 0
                                else None
                            ),
                        ).tooltip('Combine this caption with the previous caption, joining their text and time range.')
                        ui.button("Merge with next", icon="merge_type").props(
                            "flat dense"
                        ).on(
                            "click",
                            lambda: (
                                self.merge_with_next(caption)
                                if self.captions.index(caption) < len(self.captions) - 1
                                else None
                            ),
                        ).tooltip('Combine this caption with the next caption, joining their text and time range.')

                        ui.button("Close").props("flat dense").on(
                            "click",
                            lambda: self.select_caption(
                                caption,
                                speaker_select,
                                True,
                                new_text=text_area.value,
                            ),
                        ).classes("caption-close").tooltip('Finish editing this card. Use Save to save your changes to the transcription.')

                        if self.data_format == "txt":
                            ui.button("Add caption").props("flat dense").on(
                                "click", lambda: self.add_caption_after(caption)
                            ).tooltip('Insert a new caption immediately after this one, then edit its text and timing.')

                        ui.button("Delete", color="red").props("flat dense").classes(
                            "caption-delete-btn"
                        ).on(
                            "click", lambda: self.remove_caption(caption)
                        ).tooltip('Remove this caption from the transcription. Use Undo to restore it.')
                else:
                    # Show text with search highlighting
                    if caption.is_highlighted and self.search_term:
                        highlighted_text = self.get_highlighted_text(caption.text, caption)

                        with ui.row():
                            ui.label(f"#{caption.index}").classes("font-bold text-sm")

                            if self.data_format == "txt":
                                ui.label(f"{caption.speaker}:").classes(
                                    "font-bold text-sm"
                                )
                        ui.label(f"{caption.start_time} - {caption.end_time}").classes(
                            "text-sm text-gray-500 caption-time-label"
                        )

                        ui.html(highlighted_text, sanitize=False).classes(
                            "text-sm leading-relaxed whitespace-pre-wrap caption-body"
                        )
                    else:
                        with ui.row().classes("w-full justify-between"):
                            with ui.row():
                                ui.label(f"#{caption.index}").classes(
                                    "font-bold text-sm"
                                )

                                if self.data_format == "txt":
                                    ui.label(f"{caption.speaker}:").classes(
                                        "font-bold text-sm"
                                    )
                            ui.label(
                                f"{caption.start_time} - {caption.end_time}"
                            ).classes("text-sm text-gray-500 caption-time-label")
                        with ui.row().classes("w-full justify-between items-end"):
                            self.render_caption_text(caption)
                            text_color = "text-gray-500"

                            tooltip_text = (
                                "Character count."
                                if self.data_format == "txt"
                                else "Character count.  Max 42 per line (guideline)."
                            )

                            lines = caption.text.split("\n")
                            line_lengths = [str(len(x)) for x in lines]

                            # Check for exceeded limit
                            if self.data_format != "txt" and any(
                                len(x) > CHARACTER_LIMIT for x in lines
                            ):
                                text_color = CHARACTER_LIMIT_EXCEEDED_COLOR
                                tooltip_text = f"Readability recommendation: {CHARACTER_LIMIT} characters or fewer per line. Longer lines are allowed but may wrap differently across players."

                            character_label = "/".join(line_lengths)

                            with ui.row().classes("items-center gap-1"):
                                self.render_confidence_badge(caption)
                                with ui.label(f"({character_label})").classes(
                                    f"text-sm text-right {text_color}"
                                ):
                                    ui.tooltip(tooltip_text)

                card.on(
                    "click",
                    lambda: (
                        self.select_caption(caption)
                        if not caption.is_selected
                        else None
                    ),
                )

        # Store reference to container
        self.caption_containers[caption.index] = container
        return card

    def refresh_display(
        self, force_full_refresh: bool = False, specific_indices: set = None
    ) -> None:
        """Refresh the caption display - only recreate if necessary

        Args:
            force_full_refresh: If True, recreate all captions
            specific_indices: If provided, only update these specific caption indices
        """
        if self.main_container:
            if force_full_refresh or not self.caption_containers:
                # Full refresh - clear and recreate everything
                self.main_container.clear()
                self.caption_containers.clear()
                with self.main_container:
                    if not self.captions:
                        ui.label("No captions loaded").classes(
                            "text-gray-500 text-center p-8"
                        )
                    else:
                        for caption in self.captions:
                            self.create_caption_card(caption)
            else:
                # Incremental update - update existing containers
                current_indices = {cap.index for cap in self.captions}
                existing_indices = set(self.caption_containers.keys())

                # Remove containers for deleted captions
                for idx in existing_indices - current_indices:
                    if idx in self.caption_containers:
                        container = self.caption_containers[idx]
                        container.clear()
                        container.delete()
                        del self.caption_containers[idx]

                # Add new captions or update existing ones
                with self.main_container:
                    for caption in self.captions:
                        # Only update if no specific_indices filter, or if index is in the filter
                        should_update = (
                            specific_indices is None
                            or caption.index in specific_indices
                        )

                        if caption.index not in self.caption_containers:
                            # New caption - create it
                            self.create_caption_card(caption)
                        elif should_update:
                            # Existing caption - update it only if needed
                            container = self.caption_containers[caption.index]
                            container.clear()
                            with container:
                                self.update_caption_card_content(caption)

        if self._confidence_review_list is not None and self._confidence_review_caption_snapshot != tuple((c.index, c.text) for c in self.captions):
            self.refresh_confidence_review()

    def update_caption_card_content(self, caption: SRTCaption) -> None:
        """
        Update the content of an existing caption card
        """
        card_class = "cursor-pointer border-0 transition-all duration-200 w-full"
        # Semantic states are styled only under the Minimal appearance scope.
        if caption.is_selected:
            card_class += " caption-editing"
        if not caption.is_valid:
            card_class += " caption-invalid"
        if caption.is_highlighted:
            card_class += " caption-search-match"

        if not caption.is_valid:
            card_class += " border-red-400 bg-red-50 hover:border-red-500"
        elif caption.is_selected and caption.is_highlighted:
            card_class += (
                " shadow-lg border-yellow-400 bg-yellow-100 hover:border-yellow-500"
            )
        elif caption.is_selected:
            card_class += " shadow-lg"
        elif caption.is_highlighted:
            card_class += " border-yellow-400 bg-yellow-50 hover:border-yellow-500"
        else:
            card_class += " hover:shadow-md shadow-none"

        with ui.card().classes(card_class + " caption-card").props(
            self._card_time_attrs(caption)
        ) as card:
            if caption.is_selected:
                with ui.row().classes("w-full justify-between") as action_row:
                    action_row.props("id=action_row").classes("caption-meta-row")
                    ui.label(f"#{caption.index}").classes(
                        "font-bold text-sm text-gray-500"
                    )

                    if self.data_format == "txt":
                        speaker_select = ui.select(
                            options=list(self.speakers),
                            value=caption.speaker,
                            with_input=True,
                            label="Speaker",
                            new_value_mode="add",
                        ).bind_value(caption, 'speaker')
                    else:
                        speaker_select = None

                    start_input = ui.input("", value=caption.start_time).props(
                        "dense borderless"
                    ).classes("caption-time-input")
                    end_input = ui.input("", value=caption.end_time).props(
                        "dense borderless"
                    ).classes("caption-time-input")

                start_input.on(
                    "blur",
                    lambda: self.update_caption_timing(
                        caption, start_input.value, end_input.value
                    ),
                )
                end_input.on(
                    "blur",
                    lambda: self.update_caption_timing(
                        caption, start_input.value, end_input.value
                    ),
                )

                text_area = (
                    ui.textarea(value=caption.text)
                    .classes("w-full caption-text-input")
                    .props("outlined input-class=h-32")
                )
                text_area.on(
                    "blur", lambda e: self.update_caption_text(caption, e.sender.value)
                )

                with ui.row().classes("w-full justify-between caption-btn-row"):
                    if self.words:
                        self.create_split_cursor_button(caption, text_area)
                    ui.button("Merge with previous", icon="merge_type").props(
                        "flat dense"
                    ).on(
                        "click",
                        lambda: (
                            self.merge_with_previous(caption)
                            if self.captions.index(caption) > 0
                            else None
                        ),
                    ).tooltip('Combine this caption with the previous caption, joining their text and time range.')
                    ui.button("Merge with next", icon="merge_type").props(
                        "flat dense"
                    ).on(
                        "click",
                        lambda: (
                            self.merge_with_next(caption)
                            if self.captions.index(caption) < len(self.captions) - 1
                            else None
                        ),
                    ).tooltip('Combine this caption with the next caption, joining their text and time range.')

                    ui.button("Close").props("flat dense").on(
                        "click",
                        lambda: self.select_caption(
                            caption, speaker_select, True, new_text=text_area.value
                        ),
                    ).classes("caption-close").tooltip('Finish editing this card. Use Save to save your changes to the transcription.')

                    if self.data_format == "txt":
                        ui.button("Add caption").props("flat dense").on(
                            "click", lambda: self.add_caption_after(caption)
                        ).tooltip('Insert a new caption immediately after this one, then edit its text and timing.')

                    ui.button("Delete", color="red").props("flat dense").classes(
                        "caption-delete-btn"
                    ).on(
                        "click", lambda: self.remove_caption(caption)
                    ).tooltip('Remove this caption from the transcription. Use Undo to restore it.')
            else:
                if caption.is_highlighted and self.search_term:
                    highlighted_text = self.get_highlighted_text(caption.text, caption)

                    with ui.row():
                        ui.label(f"#{caption.index}").classes("font-bold text-sm")

                        if self.data_format == "txt":
                            ui.label(f"{caption.speaker}:").classes("font-bold text-sm")
                    ui.label(f"{caption.start_time} - {caption.end_time}").classes(
                        "text-sm text-gray-500 caption-time-label"
                    )

                    ui.html(highlighted_text, sanitize=False).classes(
                        "text-sm leading-relaxed whitespace-pre-wrap caption-body"
                    )
                else:
                    with ui.row().classes("w-full justify-between"):
                        with ui.row():
                            ui.label(f"#{caption.index}").classes("font-bold text-sm")

                            if self.data_format == "txt":
                                ui.label(f"{caption. speaker}:").classes(
                                    "font-bold text-sm"
                                )
                        ui.label(f"{caption.start_time} - {caption.end_time}").classes(
                            "text-sm text-gray-500 caption-time-label"
                        )
                    with ui.row().classes("w-full justify-between items-end"):
                        self.render_caption_text(caption)
                        text_color = "text-gray-500"

                        tooltip_text = (
                            "Character count."
                            if self.data_format == "txt"
                            else "Character count.  Max 42 per line (guideline)."
                        )

                        lines = caption.text.split("\n")
                        line_lengths = [str(len(x)) for x in lines]

                        # Check for exceeded limit
                        if self.data_format != "txt" and any(
                            len(x) > CHARACTER_LIMIT for x in lines
                        ):
                            text_color = CHARACTER_LIMIT_EXCEEDED_COLOR
                            tooltip_text = f"Readability recommendation: {CHARACTER_LIMIT} characters or fewer per line. Longer lines are allowed but may wrap differently across players."

                        character_label = "/".join(line_lengths)

                        with ui.row().classes("items-center gap-1"):
                            self.render_confidence_badge(caption)
                            with ui.label(f"({character_label})").classes(
                                f"text-sm text-right {text_color}"
                            ):
                                ui.tooltip(tooltip_text)

            card.on(
                "click",
                lambda: (
                    self.select_caption(caption) if not caption.is_selected else None
                ),
            )

    def validate_captions(self):
        """Report timing/content errors separately from readability advice."""
        issues = check_subtitles(self.captions, CHARACTER_LIMIT)
        errors = [issue for issue in issues if issue["severity"] == "error"]
        warnings = [issue for issue in issues if issue["severity"] == "warning"]
        invalid = {index for issue in errors for index in issue["indices"]}
        changed = set()
        for caption in self.captions:
            valid = caption.index not in invalid
            if caption.is_valid != valid:
                changed.add(caption.index)
            caption.is_valid = valid
        if changed:
            self.refresh_display(specific_indices=changed)

        with ui.dialog() as dialog, ui.card().classes("p-6").style(
            "max-width: 700px; width: 90vw; max-height: 90vh; overflow-y: auto;"
        ):
            with ui.row().classes("items-center gap-2"):
                ui.label("Check subtitles").classes("text-h5 font-bold")
                with ui.icon("info_outline", size="20px").classes("opacity-60 cursor-help").props(
                    'tabindex=0 aria-label="About subtitle checks"'
                ):
                    ui.tooltip(
                        "Checks subtitle timing and readability. Warnings suggest improvements; "
                        "they do not necessarily prevent playback or export."
                    ).style("max-width: min(320px, calc(100vw - 32px)); white-space: normal; overflow-wrap: break-word;")
            ui.label(f"{len(self.captions)} captions checked · {len(errors)} errors · {len(warnings)} warnings")
            for title, entries, icon, color in (
                ("Timing and content errors", errors, "error", "text-red-500"),
                ("Readability warnings", warnings, "warning", "text-amber-600"),
            ):
                if not entries:
                    continue
                ui.separator()
                ui.label(title).classes("text-lg font-semibold")
                for issue in entries:
                    def jump(index):
                        container = self.caption_containers.get(index)
                        if container is None:
                            ui.notify("Caption no longer available. Run the check again.", type="warning")
                            return
                        dialog.close()
                        ui.run_javascript(
                            f"requestAnimationFrame(() => document.getElementById('c{container.id}')"
                            "?.scrollIntoView({behavior: 'smooth', block: 'center'}));"
                        )
                    with ui.row().classes("items-start flex-nowrap w-full gap-2 rounded-lg p-2").style(
                        "border: 1px solid color-mix(in srgb, currentColor 12%, transparent);"
                    ):
                        ui.icon(icon, size="20px").classes(f"{color} mt-3 shrink-0")
                        with ui.column().classes("gap-1 flex-1 min-w-0"):
                            ui.button(
                                issue["message"],
                                on_click=lambda index=issue["indices"][0]: jump(index),
                            ).props("flat no-caps align=left icon-right=chevron_right", remove="color").classes(
                                "text-left w-full rounded-md"
                            ).style("color: inherit; font-weight: 400; line-height: 1.5;")
                            if len(issue["indices"]) > 1:
                                with ui.row().classes("gap-1"):
                                    for index in issue["indices"]:
                                        ui.button(
                                            f"Go to #{index}",
                                            on_click=lambda index=index: jump(index),
                                        ).props("flat dense no-caps", remove="color").classes(
                                            "rounded-md px-2"
                                        ).style("color: inherit; font-size: 0.8rem; opacity: 0.8;")
            if not errors:
                ui.label("No timing or content errors found.").classes("text-sm opacity-70")
            if not warnings:
                ui.label("No readability warnings found.").classes("text-sm opacity-70")
            with ui.row().classes("w-full justify-end"):
                ui.button("Close", on_click=dialog.close).props("flat")
        dialog.open()

    def show_keyboard_shortcuts(self, open_window: Optional[bool] = False) -> None:
        """
        Show keyboard shortcuts dialog.
        """

        shortcut_groups = [
            (
                "Navigation",
                [
                    ("Next caption", "Alt + ↓"),
                    ("Previous caption", "Alt + ↑"),
                    ("Close/deselect block", "Esc"),
                ],
            ),
            (
                "Editing",
                [
                    ("Split caption", "Ctrl/⌘ + Enter"),
                    ("Merge with next", "Ctrl + M"),
                    ("Merge with previous", "Ctrl + Shift + M"),
                    *([("Add caption after", "Ctrl/⌘ + Shift + Enter")] if self.data_format == "txt" else []),
                    ("Delete caption", "Ctrl + D"),
                ],
            ),
            (
                "File Operations",
                [
                    ("Save file", "Ctrl/⌘ + S"),
                    ("Export file", "Ctrl/⌘ + E"),
                    ("Find", "Ctrl/⌘ + F"),
                    ("Validate captions", "Ctrl + Shift + V"),
                ],
            ),
            (
                "History",
                [
                    ("Undo", "Ctrl/⌘ + Z"),
                    ("Redo", "Ctrl + Y / ⌘ + Shift + Z"),
                ],
            ),
            (
                "Video",
                [
                    ("Play/Pause", "Ctrl + Space"),
                ],
            ),
        ]

        with ui.dialog() as dialog:
            with ui.card().classes("w-2/3 max-w-2xl").style("padding: 24px; max-height: 90vh; overflow-y: auto;"):
                ui.label("Keyboard shortcuts").classes("text-h5 mb-4 font-bold")

                with ui.column().classes("w-full gap-4"):
                    for group_name, shortcuts in shortcut_groups:
                        ui.label(group_name).classes(
                            "text-subtitle1 font-semibold mt-2"
                        )
                        with ui.column().classes("w-full gap-1 ml-4"):
                            for action, keys in shortcuts:
                                with ui.row().classes(
                                    "justify-between w-full items-center"
                                ):
                                    ui.label(action).classes("text-body1")
                                    ui.label(keys).classes(
                                        "text-body2 font-mono bg-gray-100 px-2 py-1 rounded kbd-key"
                                    )

                with ui.row().classes("w-full justify-end mt-4").style(
                    "position: sticky; bottom: -24px; background: var(--color-bg-surface); padding-bottom: 8px; z-index: 1;"
                ):
                    ui.button("Close").props("flat color=primary").on(
                        "click", dialog.close
                    )

        if open_window:
            dialog.open()
        else:
            ui.button("Shortcuts").props("icon=keyboard flat dense", remove="color").on(
                "click", lambda: dialog.open()
            ).classes("button-open-search")

    def show_export_dialog(
        self, filename: str, bulk_editors: list | None = None
    ) -> None:
        """
        Show comprehensive export dialog with format options and live preview.
        When bulk_editors is provided (list of (filename, editor) tuples),
        the preview is skipped and files are exported as a zip archive.
        """
        import io
        import zipfile
        from pathlib import Path

        filename = sanitize_filename(filename)
        if bulk_editors:
            bulk_editors = [
                (sanitize_filename(fn), ed) for fn, ed in bulk_editors
            ]

        is_bulk = bulk_editors is not None and len(bulk_editors) > 0
        # For bulk mode with txt formats: show preview using first file
        bulk_needs_preview = is_bulk and self.data_format == "txt"

        ui.add_head_html(default_styles)
        with ui.dialog() as dialog:
            card = ui.card().classes("p-6").style(
                f"min-width: {'1000' if (not is_bulk or bulk_needs_preview) else '500'}px; "
                f"max-width: {'1400' if (not is_bulk or bulk_needs_preview) else '700'}px; "
                "max-height: 90vh; overflow-y: auto; "
                "background-color: #ffffff;"
            )
            with card:
                # Header
                with ui.row().classes("w-full items-center justify-between mb-4"):
                    ui.label("Export transcript").classes(
                        "text-h5 font-bold"
                    )
                    ui.button(icon="close", on_click=dialog.close).props(
                        "flat round dense color=grey-7"
                    )

                ui.separator().classes("mb-4")

                if is_bulk:
                    ui.label(f"Exporting {len(bulk_editors)} file(s)").classes(
                        "text-body1 mb-2"
                    )

                # Two-column layout (single column for bulk srt/vtt)
                with ui.row().classes("w-full gap-6"):
                    # Left: Options (fixed width when preview shown)
                    with ui.column().classes("gap-4").style(
                        "flex: 0 0 400px;" if (not is_bulk or bulk_needs_preview) else "width: 100%;"
                    ):
                        # Format
                        ui.label("Format").classes("text-subtitle1 font-semibold")
                        if self.data_format == "srt":
                            format_opts = {
                                "srt": "SubRip (.srt)",
                                "vtt": "WebVTT (.vtt)",
                            }
                        else:
                            format_opts = {
                                "txt": "Text (.txt)",
                                "json": "JSON (.json)",
                                "rtf": "RTF (.rtf)",
                                "csv": "CSV (.csv)",
                                "tsv": "TSV (.tsv)",
                            }

                        fmt = (
                            ui.select(
                                options=format_opts, value=list(format_opts.keys())[0]
                            )
                            .classes("w-full")
                            .props("outlined dense")
                        )

                        ui.separator()

                        # Options container - will show/hide based on format
                        options_container = ui.column().classes("gap-4")

                        with options_container:
                            # Timestamps (for txt, json, csv, tsv)
                            ts_section = ui.column().classes("gap-2")
                            with ts_section:
                                ui.label("Timestamps").classes(
                                    "text-subtitle1 font-semibold"
                                )
                                ts_incl = ui.checkbox("Include timestamps", value=True)

                                with ui.row().classes("w-full gap-2 items-center"):
                                    ts_which = (
                                        ui.select(
                                            options={
                                                "both": "Start & End",
                                                "start": "Start only",
                                                "end": "End only",
                                            },
                                            value="both",
                                            label="Timestamp range",
                                        )
                                        .classes("flex-1")
                                        .props("dense outlined")
                                    )
                                    ts_pos = (
                                        ui.select(
                                            options={
                                                "before": "Before text",
                                                "after": "After text",
                                            },
                                            value="before",
                                            label="Position",
                                        )
                                        .classes("flex-1")
                                        .props("dense outlined")
                                    )
                                    ts_pos.visible = False

                                ts_fmt = (
                                    ui.select(
                                        options={
                                            "srt": "SRT (00:00:00,000)",
                                            "vtt": "VTT (00:00:00.000)",
                                            "seconds": "Seconds (0.000)",
                                            "ms": "Milliseconds",
                                        },
                                        value="srt",
                                        label="Format",
                                    )
                                    .classes("w-full mt-2")
                                    .props("dense outlined")
                                )
                                ui.separator()

                            # Text options (for txt only)
                            txt_section = ui.column().classes("gap-2")
                            with txt_section:
                                ui.label("Text options").classes(
                                    "text-subtitle1 font-semibold"
                                )
                                txt_spk_incl = ui.checkbox(
                                    "Include speakers", value=True
                                )
                                txt_idx_incl = ui.checkbox(
                                    "Include block numbers", value=False
                                )
                                txt_sep_type = (
                                    ui.select(
                                        options={
                                            "\\n\\n": "Double newline",
                                            "\\n": "Single newline",
                                            "---": "Line",
                                            "custom": "Custom",
                                        },
                                        value="\\n\\n",
                                        label="Separator",
                                    )
                                    .classes("w-full mt-2")
                                    .props("dense outlined")
                                )
                                txt_sep_custom = (
                                    ui.input(placeholder="Custom separator")
                                    .classes("w-full mt-2")
                                    .props("dense outlined")
                                )
                                txt_sep_custom.visible = False
                                txt_sep_type.on(
                                    "update:model-value",
                                    lambda e: setattr(
                                        txt_sep_custom, "visible", e.args == "custom"
                                    ),
                                )
                                ui.separator()

                            # RTF options
                            rtf_section = ui.column().classes("gap-2")
                            with rtf_section:
                                ui.label("RTF Options").classes(
                                    "text-subtitle1 font-semibold"
                                )
                                rtf_spk_incl = ui.checkbox(
                                    "Include speakers", value=True
                                )
                                rtf_idx_incl = ui.checkbox(
                                    "Include block numbers", value=False
                                )
                                ui.separator()

                            # CSV options (for csv only)
                            csv_section = ui.column().classes("gap-2")
                            with csv_section:
                                ui.label("CSV Options").classes(
                                    "text-subtitle1 font-semibold"
                                )
                                csv_hdr = ui.checkbox("Include header row", value=True)
                                csv_spk_incl = ui.checkbox(
                                    "Include speakers", value=True
                                )
                                csv_qt = (
                                    ui.input(label="Quote character", value='"')
                                    .classes("w-full mt-2")
                                    .props("dense outlined")
                                )
                                csv_delim = (
                                    ui.input(label="Delimiter", value=",")
                                    .classes("w-full mt-2")
                                    .props("dense outlined")
                                )
                                ui.separator()

                            # TSV options (for tsv only)
                            tsv_section = ui.column().classes("gap-2")
                            with tsv_section:
                                ui.label("TSV Options").classes(
                                    "text-subtitle1 font-semibold"
                                )
                                tsv_hdr = ui.checkbox("Include header row", value=True)
                                tsv_spk_incl = ui.checkbox(
                                    "Include speakers", value=True
                                )
                                tsv_tab_type = (
                                    ui.select(
                                        options={
                                            "\\t": "Real tab character",
                                            "spaces": "Spaces",
                                        },
                                        value="\\t",
                                        label="Tab type",
                                    )
                                    .classes("w-full mt-2")
                                    .props("dense outlined")
                                )
                                tsv_tab_width = (
                                    ui.number(
                                        label="Tab width (spaces)",
                                        value=4,
                                        min=1,
                                        max=16,
                                    )
                                    .classes("w-full mt-2")
                                    .props("dense outlined")
                                )
                                tsv_tab_width.visible = False
                                tsv_tab_type.on(
                                    "update:model-value",
                                    lambda e: setattr(
                                        tsv_tab_width, "visible", e.args == "spaces"
                                    ),
                                )
                                ui.separator()

                            # JSON options (for json only)
                            json_section = ui.column().classes("gap-2")
                            with json_section:
                                ui.label("JSON Options").classes(
                                    "text-subtitle1 font-semibold"
                                )
                                json_indent = (
                                    ui.number(
                                        label="Indentation spaces",
                                        value=2,
                                        min=0,
                                        max=8,
                                    )
                                    .classes("w-full mt-2")
                                    .props("dense outlined")
                                )
                                json_ascii = ui.checkbox(
                                    "Escape non-ASCII characters", value=False
                                )
                                ui.separator()

                        bulk_preview_col = None

                        def update_options_visibility():
                            """
                            Show/hide options based on selected format
                            """
                            current_fmt = fmt.value

                            # SRT/VTT - no options
                            ts_section.visible = current_fmt in [
                                "txt",
                                "json",
                                "csv",
                                "tsv",
                                "rtf",
                            ]
                            txt_section.visible = current_fmt == "txt"
                            csv_section.visible = current_fmt == "csv"
                            tsv_section.visible = current_fmt == "tsv"
                            json_section.visible = current_fmt == "json"
                            rtf_section.visible = current_fmt == "rtf"

                            # In bulk mode, show/hide preview based on format
                            if bulk_needs_preview and bulk_preview_col is not None:
                                show_prev = current_fmt not in ["srt", "vtt"]
                                bulk_preview_col.visible = show_prev
                                # Resize dialog based on preview visibility
                                card.style(
                                    f"min-width: {'1000' if show_prev else '500'}px; "
                                    f"max-width: {'1400' if show_prev else '700'}px; "
                                    "background-color: #ffffff;"
                                )

                        fmt.on(
                            "update:model-value", lambda: update_options_visibility()
                        )

                    # Right: Preview (60%) - show for single mode and bulk txt formats
                    show_preview = not is_bulk or bulk_needs_preview
                    if show_preview:
                        preview_col = ui.column().classes("flex-1")
                        if bulk_needs_preview:
                            bulk_preview_col = preview_col
                        with preview_col:
                            if bulk_needs_preview:
                                first_fn = bulk_editors[0][0] if bulk_editors else filename
                                ui.label("Preview").classes(
                                    "text-subtitle1 font-semibold mb-2"
                                ).tooltip(f"Showing: {first_fn}")
                            else:
                                ui.label("Preview").classes(
                                    "text-subtitle1 font-semibold mb-2"
                                )
                            with ui.card().classes("bg-gray-900 p-4").style(
                                "height: 550px; overflow-y: auto;"
                            ):
                                prev = (
                                    ui.html("", sanitize=False)
                                    .classes("text-white")
                                    .style(
                                        "font-family: 'Courier New', monospace; font-size: 13px; white-space: pre-wrap;"
                                    )
                                )
                            cnt_lbl = ui.label("").classes("text-caption mt-2")

                update_options_visibility()

                if show_preview:

                    def upd_prev():
                        try:
                            caps = self.captions[:5]
                            out = ""

                            def fmt_ts(ts, f):
                                p = ts.replace(",", ":").split(":")
                                h, m, s, ms = int(p[0]), int(p[1]), int(p[2]), int(p[3])
                                if f == "seconds":
                                    return f"{h*3600 + m*60 + s + ms/1000:.3f}"
                                if f == "ms":
                                    return str(h * 3600000 + m * 60000 + s * 1000 + ms)
                                if f == "vtt":
                                    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
                                return ts  # srt format

                            def build_ts_str(cap):
                                """
                                Build timestamp string based on options
                                """
                                if not ts_incl.value:
                                    return ""
                                parts = []
                                if ts_which.value in ["start", "both"]:
                                    parts.append(fmt_ts(cap.start_time, ts_fmt.value))
                                if ts_which.value in ["end", "both"]:
                                    parts.append(fmt_ts(cap.end_time, ts_fmt.value))
                                return " - ".join(parts) if parts else ""

                            match fmt.value:
                                case "srt":
                                    out = "\n\n".join(c.to_srt_format() for c in caps)
                                case "vtt":
                                    out = "WEBVTT\n\n" + "\n\n".join(
                                        f"{c.index}\n{c.start_time.replace(',','.')} --> {c.end_time.replace(',','.')}\n{c.text}"
                                        for c in caps
                                    )
                                case "txt":
                                    parts = []
                                    for c in caps:
                                        p_parts = []
                                        if txt_idx_incl and txt_idx_incl.value:
                                            p_parts.append(f"[{c.index}]")

                                        ts_str = build_ts_str(c)
                                        if ts_str and ts_pos.value == "before":
                                            p_parts.append(f"({ts_str})")

                                        if txt_spk_incl and txt_spk_incl.value:
                                            p_parts.append(f"{c.speaker}:")

                                        # Add text on new line or same line
                                        if p_parts:
                                            p = " ".join(p_parts) + "\n" + c.text
                                        else:
                                            p = c.text

                                        if ts_str and ts_pos.value == "after":
                                            p += f"\n({ts_str})"

                                        parts.append(p)
                                    s = (
                                        txt_sep_custom.value
                                        if txt_sep_type.value == "custom"
                                        else txt_sep_type.value.replace("\\n", "\n")
                                    )
                                    out = s.join(parts)
                                case "rtf":
                                    parts = []
                                    for c in caps:
                                        p_parts = []
                                        if rtf_idx_incl and rtf_idx_incl.value:
                                            p_parts.append(f"[{c.index}]")

                                        ts_str = build_ts_str(c)
                                        if ts_str:
                                            p_parts.append(f"({ts_str})")

                                        if rtf_spk_incl and rtf_spk_incl.value:
                                            p_parts.append(f"{c.speaker}:")

                                        # Add text on new line or same line
                                        if p_parts:
                                            p = " ".join(p_parts) + "\n" + c.text
                                        else:
                                            p = c.text

                                        parts.append(p)
                                    out = "\n\n".join(parts)
                                case "json":
                                    d = {"total": len(self.captions), "captions": []}
                                    for c in caps:
                                        cd = {
                                            "index": c.index,
                                            "speaker": c.speaker,
                                            "text": c.text,
                                        }
                                        if ts_incl.value:
                                            if ts_which.value in ["start", "both"]:
                                                cd["start"] = fmt_ts(
                                                    c.start_time, ts_fmt.value
                                                )
                                            if ts_which.value in ["end", "both"]:
                                                cd["end"] = fmt_ts(c.end_time, ts_fmt.value)
                                        d["captions"].append(cd)
                                    out = json.dumps(
                                        d,
                                        indent=int(json_indent.value),
                                        ensure_ascii=json_ascii.value,
                                    )
                                case "csv":
                                    q = csv_qt.value
                                    delim = csv_delim.value
                                    lines = []
                                    if csv_hdr.value:
                                        h = ["index"]
                                        if ts_incl.value:
                                            if ts_which.value in ["start", "both"]:
                                                h.append("start")
                                            if ts_which.value in ["end", "both"]:
                                                h.append("end")
                                        if csv_spk_incl.value:
                                            h.append("speaker")
                                        h.append("text")
                                        lines.append(delim.join(f"{q}{x}{q}" for x in h))
                                    for c in caps:
                                        r = [str(c.index)]
                                        if ts_incl.value:
                                            if ts_which.value in ["start", "both"]:
                                                r.append(fmt_ts(c.start_time, ts_fmt.value))
                                            if ts_which.value in ["end", "both"]:
                                                r.append(fmt_ts(c.end_time, ts_fmt.value))
                                        if csv_spk_incl.value:
                                            r.append(c.speaker)
                                        r.append(
                                            c.text.replace(q, q + q).replace("\n", " ")
                                        )
                                        lines.append(delim.join(f"{q}{x}{q}" for x in r))
                                    out = "\n".join(lines)
                                case "tsv":
                                    # Determine tab character
                                    if tsv_tab_type.value == "\\t":
                                        tab_char = "\t"
                                    else:
                                        tab_char = " " * int(tsv_tab_width.value)

                                    lines = []
                                    if tsv_hdr.value:
                                        h = ["index"]
                                        if ts_incl.value:
                                            if ts_which.value in ["start", "both"]:
                                                h.append("start")
                                            if ts_which.value in ["end", "both"]:
                                                h.append("end")
                                        if tsv_spk_incl.value:
                                            h.append("speaker")
                                        h.append("text")
                                        lines.append(tab_char.join(h))
                                    for c in caps:
                                        r = [str(c.index)]
                                        if ts_incl.value:
                                            if ts_which.value in ["start", "both"]:
                                                r.append(fmt_ts(c.start_time, ts_fmt.value))
                                            if ts_which.value in ["end", "both"]:
                                                r.append(fmt_ts(c.end_time, ts_fmt.value))
                                        if tsv_spk_incl.value:
                                            r.append(c.speaker)
                                        r.append(
                                            c.text.replace("\t", "  ").replace("\n", " ")
                                        )
                                        lines.append(tab_char.join(r))
                                    out = "\n".join(lines)
                                case "rtf":
                                    # Show RTF source code for preview
                                    parts = []
                                    for c in caps:
                                        parts.append(
                                            f"{c.speaker}: {c.start_time} - {c.end_time}"
                                        )
                                        parts.append(c.text.replace("\n", "\\line "))
                                        parts.append("")
                                    out = "\n".join(parts)
                                case _:
                                    out = "(RTF preview unavailable)"

                            if len(self.captions) > 5:
                                out += f"\n\n... {len(self.captions)-5} more captions"

                            import html

                            prev.set_content(html.escape(out).replace("\n", "<br>"))
                            cnt_lbl.set_text(
                                f"Total: {len(self.captions)} | Showing: {min(5, len(self.captions))}"
                            )
                        except Exception as e:
                            import html

                            prev.set_content(
                                f"<span style='color:#f88'>{html.escape(str(e))}</span>"
                            )

                    # Connect updates
                    for ctrl in [fmt, ts_incl, ts_fmt, ts_which, ts_pos]:
                        ctrl.on("update:model-value", lambda: upd_prev())

                    # CSV controls
                    csv_hdr.on("update:model-value", lambda: upd_prev())
                    csv_spk_incl.on("update:model-value", lambda: upd_prev())
                    csv_qt.on("blur", lambda: upd_prev())
                    csv_delim.on("blur", lambda: upd_prev())

                    # TSV controls
                    tsv_hdr.on("update:model-value", lambda: upd_prev())
                    tsv_spk_incl.on("update:model-value", lambda: upd_prev())
                    tsv_tab_type.on("update:model-value", lambda: upd_prev())
                    tsv_tab_width.on("blur", lambda: upd_prev())

                    # JSON controls
                    json_indent.on("blur", lambda: upd_prev())
                    json_ascii.on("update:model-value", lambda: upd_prev())

                    # TXT controls
                    txt_spk_incl.on("update:model-value", lambda: upd_prev())
                    txt_idx_incl.on("update:model-value", lambda: upd_prev())
                    txt_sep_type.on("update:model-value", lambda: upd_prev())
                    txt_sep_custom.on("blur", lambda: upd_prev())

                    # RTF controls
                    rtf_spk_incl.on("update:model-value", lambda: upd_prev())
                    rtf_idx_incl.on("update:model-value", lambda: upd_prev())

                    upd_prev()

                ui.separator().classes("my-4")

                # Footer
                with ui.row().classes("w-full justify-between items-center export-footer").style(
                    "position: sticky; bottom: -24px; background: white; padding-bottom: 8px; z-index: 1;"
                ):
                    if is_bulk:
                        ui.label("").bind_text_from(
                            fmt, "value", backward=lambda v: f"Format: .{v}"
                        ).classes("text-body2")
                    else:
                        ui.label(f"File: {Path(filename).stem}.{fmt.value}").classes("text-body2")
                    with ui.row().classes("gap-2"):
                        ui.button("Close", on_click=dialog.close).props("outline", remove="color")

                        def exp():
                            try:
                                def fmt_ts(ts, f):
                                    p = ts.replace(",", ":").split(":")
                                    h, m, s, ms = (
                                        int(p[0]),
                                        int(p[1]),
                                        int(p[2]),
                                        int(p[3]),
                                    )
                                    if f == "seconds":
                                        return f"{h*3600 + m*60 + s + ms/1000:.3f}"
                                    if f == "ms":
                                        return str(
                                            h * 3600000 + m * 60000 + s * 1000 + ms
                                        )
                                    if f == "vtt":
                                        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"
                                    return ts

                                def build_ts_str(cap):
                                    """
                                    Build timestamp string based on options
                                    """
                                    if not ts_incl.value:
                                        return ""
                                    parts = []
                                    if ts_which.value in ["start", "both"]:
                                        parts.append(
                                            fmt_ts(cap.start_time, ts_fmt.value)
                                        )
                                    if ts_which.value in ["end", "both"]:
                                        parts.append(fmt_ts(cap.end_time, ts_fmt.value))
                                    return " - ".join(parts) if parts else ""

                                def export_one(editor):
                                    """
                                    Export a single editor to string content.
                                    """
                                    c = None
                                    if fmt.value == "srt":
                                        c = editor.export_srt()
                                    elif fmt.value == "vtt":
                                        c = editor.export_vtt()
                                    elif fmt.value == "rtf":
                                        c = editor.export_rtf(
                                            rtf_spk_incl.value,
                                            ts_incl.value,
                                            rtf_idx_incl.value,
                                            ts_which.value,
                                            ts_fmt.value,
                                        )
                                    elif fmt.value == "txt":
                                        parts = []
                                        sep_str = "\n\n"
                                        if txt_sep_type.value == "custom":
                                            sep_str = txt_sep_custom.value.replace(
                                                "\\n", "\n"
                                            )
                                        elif txt_sep_type.value != "\\n\\n":
                                            sep_str = txt_sep_type.value.replace(
                                                "\\n", "\n"
                                            )

                                        for cap in editor.captions:
                                            p_parts = []
                                            if txt_idx_incl.value:
                                                p_parts.append(f"[{cap.index}]")

                                            ts_str = build_ts_str(cap)
                                            if ts_str and ts_pos.value == "before":
                                                p_parts.append(f"({ts_str})")

                                            if txt_spk_incl.value:
                                                p_parts.append(f"{cap.speaker}:")

                                            if p_parts:
                                                p = " ".join(p_parts) + "\n" + cap.text
                                            else:
                                                p = cap.text

                                            if ts_str and ts_pos.value == "after":
                                                p += f"\n({ts_str})"

                                            parts.append(p)
                                        c = sep_str.join(parts)
                                    elif fmt.value == "csv":
                                        q = csv_qt.value or '"'
                                        d = csv_delim.value or ","
                                        lines = []
                                        if csv_hdr.value:
                                            h = ["index"]
                                            if ts_incl.value:
                                                if ts_which.value in ["start", "both"]:
                                                    h.append("start")
                                                if ts_which.value in ["end", "both"]:
                                                    h.append("end")
                                            if csv_spk_incl.value:
                                                h.append("speaker")
                                            h.append("text")
                                            lines.append(d.join(f"{q}{x}{q}" for x in h))
                                        for cap in editor.captions:
                                            r = [str(cap.index)]
                                            if ts_incl.value:
                                                if ts_which.value in ["start", "both"]:
                                                    r.append(
                                                        fmt_ts(cap.start_time, ts_fmt.value)
                                                    )
                                                if ts_which.value in ["end", "both"]:
                                                    r.append(
                                                        fmt_ts(cap.end_time, ts_fmt.value)
                                                    )
                                            if csv_spk_incl.value:
                                                r.append(cap.speaker)
                                            r.append(
                                                cap.text.replace(q, q + q).replace(
                                                    "\n", " "
                                                )
                                            )
                                            lines.append(d.join(f"{q}{x}{q}" for x in r))
                                        c = "\n".join(lines)
                                    elif fmt.value == "tsv":
                                        if tsv_tab_type.value == "\\t":
                                            tab_char = "\t"
                                        else:
                                            tab_char = " " * int(tsv_tab_width.value)

                                        lines = []
                                        if tsv_hdr.value:
                                            h = ["index"]
                                            if ts_incl.value:
                                                if ts_which.value in ["start", "both"]:
                                                    h.append("start")
                                                if ts_which.value in ["end", "both"]:
                                                    h.append("end")
                                            if tsv_spk_incl.value:
                                                h.append("speaker")
                                            h.append("text")
                                            lines.append(tab_char.join(h))
                                        for cap in editor.captions:
                                            r = [str(cap.index)]
                                            if ts_incl.value:
                                                if ts_which.value in ["start", "both"]:
                                                    r.append(
                                                        fmt_ts(cap.start_time, ts_fmt.value)
                                                    )
                                                if ts_which.value in ["end", "both"]:
                                                    r.append(
                                                        fmt_ts(cap.end_time, ts_fmt.value)
                                                    )
                                            if tsv_spk_incl.value:
                                                r.append(cap.speaker)
                                            r.append(
                                                cap.text.replace("\t", "  ").replace(
                                                    "\n", " "
                                                )
                                            )
                                            lines.append(tab_char.join(r))
                                        c = "\n".join(lines)
                                    elif fmt.value == "json":
                                        data = editor.export_json()
                                        if ts_incl.value:
                                            for i, cap in enumerate(editor.captions):
                                                if i < len(data["segments"]):
                                                    seg = data["segments"][i]
                                                    if ts_which.value == "start":
                                                        seg["start"] = fmt_ts(
                                                            cap.start_time, ts_fmt.value
                                                        )
                                                        if "end" in seg:
                                                            del seg["end"]
                                                    elif ts_which.value == "end":
                                                        seg["end"] = fmt_ts(
                                                            cap.end_time, ts_fmt.value
                                                        )
                                                        if "start" in seg:
                                                            del seg["start"]
                                                    else:
                                                        seg["start"] = fmt_ts(
                                                            cap.start_time, ts_fmt.value
                                                        )
                                                        seg["end"] = fmt_ts(
                                                            cap.end_time, ts_fmt.value
                                                        )
                                        else:
                                            for seg in data["segments"]:
                                                if "start" in seg:
                                                    del seg["start"]
                                                if "end" in seg:
                                                    del seg["end"]

                                        indent = (
                                            int(json_indent.value)
                                            if json_indent.value
                                            else None
                                        )
                                        c = json.dumps(
                                            data,
                                            indent=indent,
                                            ensure_ascii=json_ascii.value,
                                        )
                                    return c

                                if is_bulk:
                                    zip_buffer = io.BytesIO()
                                    chosen_fmt = fmt.value
                                    seen_names = {}

                                    with zipfile.ZipFile(
                                        zip_buffer, "w", zipfile.ZIP_DEFLATED
                                    ) as zf:
                                        for bfn, beditor in bulk_editors:
                                            content = export_one(beditor)
                                            base_name = f"{Path(bfn).stem}.{chosen_fmt}"

                                            if base_name in seen_names:
                                                seen_names[base_name] += 1
                                                base_name = f"{Path(bfn).stem}_{seen_names[base_name]}.{chosen_fmt}"
                                            else:
                                                seen_names[base_name] = 0

                                            zf.writestr(base_name, content)

                                    ui.download(
                                        zip_buffer.getvalue(),
                                        filename="bulk_export.zip",
                                    )

                                    ui.notify(
                                        f"Exported {len(bulk_editors)} files as {chosen_fmt.upper()}",
                                        type="positive",
                                    )
                                else:
                                    c = export_one(self)
                                    ui.download(
                                        c.encode("utf-8"),
                                        filename=f"{Path(filename).stem}.{fmt.value}",
                                    )

                                    ui.notify(
                                        f"Exported as {fmt.value.upper()}",
                                        type="positive",
                                    )
                            except Exception as e:
                                ui.notify(f"Export failed: {str(e)}", type="negative")

                        ui.button("Export", icon="download", on_click=exp).props(
                            "flat color=white"
                        ).classes("button-default-style")

            dialog.open()
