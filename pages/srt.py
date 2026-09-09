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
import httpx

from nicegui import app, ui
from utils.common import default_styles
from utils.common import get_auth_header
from utils.common import page_init
from utils.helpers import storage_decrypt
from utils.settings import get_settings
from utils.srt import SRTEditor
from utils.video import create_video_proxy

create_video_proxy()

settings = get_settings()


def save_srt(job_id: str, data: str, editor: SRTEditor, data_format: str) -> None:
    try:
        jsondata = {"format": data_format, "data": data}

        headers = get_auth_header()
        res = httpx.put(
            f"{settings.API_URL}/api/v1/transcriber/{job_id}/result",
            headers=headers,
            json=jsondata,
        )
        res.raise_for_status()
    except httpx.HTTPError as e:
        ui.notify(f"Error: Failed to save file: {e}", type="negative")
        return

    editor.mark_as_saved()
    editor.update_beforeunload_state()

    ui.notify(
        "File saved successfully",
        type="positive",
        position="bottom",
        icon="check_circle",
    )


def create() -> None:
    @ui.page("/srt")
    def result(
        uuid: str, filename: str, model: str, language: str, data_format: str
    ) -> None:
        """
        Display the result of the transcription job.
        """
        page_init(use_drawer=True)
        editor = SRTEditor(uuid, data_format, filename)
        editor.setup_beforeunload_warning()

        ui.add_head_html(
            f"<link rel='preload' as='video' href='/video/{uuid}' type='video/mp4'>"
        )
        ui.add_head_html(
            """
        <script>
        window.addEventListener('keydown', function(e) {
            // Block Cmd + z / Ctrl + z for undo
            if ((e.metaKey || e.ctrlKey) && ! e.shiftKey && e.key.toLowerCase() === 'z') {
                e.preventDefault();
            }

            // Block Cmd + y / Ctrl + y for redo
            if ((e.metaKey || e.ctrlKey) && ! e.shiftKey && e.key.toLowerCase() === 's') {
                e.preventDefault();
            }

            // Block Cmd + Shift + z / Ctrl + Shift + z for redo
            if ((e.metaKey || e.ctrlKey) && ! e.shiftKey && e.key.toLowerCase() === 'y') {
                e.preventDefault();
            }

            // Block Cmd + Shift + z / Ctrl + Shift + z for redo
            if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === 'z') {
                e.preventDefault();
            }

            // Block Ctrl + f / Cmd + f for find
            if ((e.metaKey || e.ctrlKey) && ! e.shiftKey && e.key.toLowerCase() === 'f') {
                e.preventDefault();
            }

            // Block Ctrl + d / Cmd + d for bookmark
            if ((e.metaKey || e.ctrlKey) && ! e.shiftKey && e.key.toLowerCase() === 'd') {
                e.preventDefault();
            }

            // Block Ctrl + e / Cmd + e for search
            if ((e.metaKey || e.ctrlKey) && ! e.shiftKey && e.key.toLowerCase() === 'e') {
                e.preventDefault();
            }

            // Block Ctrl + Shift + m / Cmd + Shift + m for mute tab
            if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === 'm') {
                e.preventDefault();
            }

            // Handle Escape key globally (even when video player has focus)
            if (e.key === 'Escape' && !e.metaKey && !e.ctrlKey && !e.shiftKey && !e.altKey) {
                // Blur active element
                if (document.activeElement && typeof document.activeElement.blur === 'function') {
                    document.activeElement.blur();
                }
                // Dispatch custom event that Python can listen to
                window.dispatchEvent(new CustomEvent('escape-pressed'));
            }
        }, true);
        </script>
        """
        )
        ui.add_head_html(default_styles)
        ui.keyboard(on_key=editor.handle_key_event, ignore=[])

        try:
            if data_format == "srt":
                response = httpx.request(
                    "GET",
                    f"{settings.API_URL}/api/v1/transcriber/{uuid}/result/srt",
                    headers=get_auth_header(),
                    json={
                        "encryption_password": storage_decrypt(
                            app.storage.user.get("encryption_password"),
                        )
                    },
                )
            else:
                response = httpx.request(
                    "GET",
                    f"{settings.API_URL}/api/v1/transcriber/{uuid}/result/txt",
                    headers=get_auth_header(),
                    json={
                        "encryption_password": storage_decrypt(
                            app.storage.user.get("encryption_password"),
                        )
                    },
                )

            response.raise_for_status()
            data = response.json()

        except httpx.HTTPError as e:
            ui.notify(f"Error: Failed to get result: {e}")
            return

        # Word-level timing payload: for txt jobs it is part of the result
        # just fetched; subtitle jobs need one extra fetch of their JSON
        # result. Best-effort — jobs transcribed before word timestamps
        # existed simply have none.
        if data_format == "srt":
            try:
                words_response = httpx.request(
                    "GET",
                    f"{settings.API_URL}/api/v1/transcriber/{uuid}/result/txt",
                    headers=get_auth_header(),
                    json={
                        "encryption_password": storage_decrypt(
                            app.storage.user.get("encryption_password"),
                        )
                    },
                )
                words_response.raise_for_status()
                editor.load_words(words_response.json().get("result"))
            except httpx.HTTPError:
                pass
        else:
            editor.load_words(data["result"])

        ui.add_head_html('<script src="/static/confidence-review.js?v=2" defer></script>')

        # Click-to-seek on caption words: capture phase so a word click
        # seeks the video without also selecting the caption card.
        ui.add_head_html(
            """
            <style>
            .w-confidence-review { outline: 2px solid #b7791f; outline-offset: 2px; border-radius: 3px; }
            .w-seek { cursor: pointer; }
            .w-seek:hover { text-decoration: underline; }
            .w-lowconf { text-decoration: underline wavy #f59e0b; text-underline-offset: 3px; }
            .w-lowconf:hover { text-decoration: underline wavy #f59e0b; }
            .w-current { background-color: rgba(255, 193, 7, 0.45); border-radius: 3px; }
            .caption-current { box-shadow: inset 4px 0 0 0 #f59e0b; }
            </style>
            <script>
            document.addEventListener('click', function(e) {
                const el = e.target.closest && e.target.closest('.w-seek');
                if (!el || !el.dataset.s) return;
                const video = document.querySelector('video');
                if (video) {
                    video.currentTime = parseFloat(el.dataset.s);
                    e.stopPropagation();
                    e.preventDefault();
                }
            }, true);

            // Live subtitle preview: feed the editor's captions to a native
            // TextTrack, using the browser's own subtitle renderer.
            // One permanent preview track whose cues are managed directly
            // via the TextTrack API. Loading VTT through blob URLs and
            // swapping/removing <track> elements intermittently leaves
            // orphaned cue boxes painted over the video.
            window.__updateSubtitlePreview = function(cues, show, attempt) {
                const video = document.querySelector('video');
                if (!video) {
                    // The video element may not be mounted yet right after
                    // the client connects; retry briefly instead of losing
                    // the initial preview push.
                    if ((attempt || 0) < 20) {
                        setTimeout(function() {
                            window.__updateSubtitlePreview(cues, show, (attempt || 0) + 1);
                        }, 250);
                    }
                    return;
                }

                // addTextTrack gives a script-owned TextTrack that accepts
                // cues immediately — unlike a freshly created <track>
                // element, whose cue list is not reliably usable right away.
                if (!video.__previewTrack) {
                    video.__previewTrack = video.addTextTrack('subtitles', 'Preview', '');
                }
                const track = video.__previewTrack;

                track.mode = 'hidden';
                while (track.cues && track.cues.length) {
                    track.removeCue(track.cues[0]);
                }

                if (!show) {
                    track.mode = 'disabled';
                    return;
                }

                for (const cue of cues) {
                    try {
                        // Never extend a cue past the preview boundary.
                        if (!Number.isFinite(cue.s) || !Number.isFinite(cue.e)
                            || cue.e <= cue.s) continue;
                        track.addCue(new VTTCue(cue.s, cue.e, cue.t));
                    } catch (e) { /* skip malformed cue */ }
                }
                track.mode = 'showing';

                // Rebuilding cues while one is on screen can leave a ghost
                // cue box painted; re-assigning currentTime forces the
                // browser to re-evaluate the active cues and repaint.
                if (video.readyState > 0) {
                    video.currentTime = video.currentTime;
                }
            };

            // Follow mode ("Follow video" switch): mark the caption being
            // spoken and keep it scrolled into view — without opening it
            // for editing. Fully client-side; works with or without
            // word-level data via data-s/data-e on the caption cards.
            (function() {
                let cache = null;
                let lastCard = null;

                new MutationObserver(function() { cache = null; }).observe(
                    document.documentElement, { childList: true, subtree: true });

                document.addEventListener('timeupdate', function(e) {
                    if (!e.target || e.target.tagName !== 'VIDEO') return;
                    if (!window.__followPlayback) {
                        if (lastCard) {
                            lastCard.classList.remove('caption-current');
                            lastCard = null;
                        }
                        return;
                    }

                    if (!cache) {
                        cache = Array.from(
                            document.querySelectorAll('.caption-card[data-s]')
                        ).map(function(el) {
                            return {
                                el: el,
                                s: parseFloat(el.dataset.s),
                                e: parseFloat(el.dataset.e || el.dataset.s),
                            };
                        }).sort(function(a, b) { return a.s - b.s; });
                    }

                    const t = e.target.currentTime;
                    let lo = 0, hi = cache.length - 1, idx = -1;
                    while (lo <= hi) {
                        const mid = (lo + hi) >> 1;
                        if (cache[mid].s <= t) { idx = mid; lo = mid + 1; }
                        else { hi = mid - 1; }
                    }

                    let card = null;
                    if (idx >= 0 && t < cache[idx].e + 0.5) card = cache[idx].el;

                    if (card !== lastCard) {
                        if (lastCard) lastCard.classList.remove('caption-current');
                        if (card) {
                            card.classList.add('caption-current');
                            card.scrollIntoView({behavior: 'smooth', block: 'center'});
                        }
                        lastCard = card;
                    }
                }, true);
            })();
            </script>
            """
        )

        # Karaoke follow-along only when the word times are trustworthy
        # (DTW-aligned, or heuristic within short VAD speech segments):
        # plain heuristic times drift within long segments, and a lagging
        # highlight is worse than none.
        if editor.karaoke_enabled():
            ui.add_head_html('<script src="/static/karaoke.js" defer></script>')

        with ui.row().classes("justify-between w-full gap-2"):
            with ui.column().classes("flex-row items-center"):
                editor.create_undo_redo_panel()
                with ui.button("Save", icon="save") as save_button:
                    if data_format == "srt":
                        save_button.on(
                            "click",
                            lambda: save_srt(
                                uuid,
                                editor.export_srt(),
                                editor,
                                data_format,
                            ),
                        )
                    else:
                        save_button.on(
                            "click",
                            lambda: save_srt(
                                uuid,
                                json.dumps(editor.export_json()),
                                editor,
                                "json",
                            ),
                        )

                    save_button.props("flat", remove="color")

                # Export button - opens dialog
                ui.button("Export", icon="download").props("flat", remove="color").on(
                    "click", lambda: editor.show_export_dialog(filename)
                )

                if data_format == "srt":
                    with ui.button("Check subtitles", icon="check").props("flat", remove="color") as validate_button:
                        ui.tooltip("Check timing errors and readability recommendations.")
                        validate_button.on(
                            "click",
                            lambda: editor.validate_captions(),
                        )
                editor.create_search_panel()
                editor.show_keyboard_shortcuts()
            with ui.button("Close editor", icon="close").props("flat", remove="color") as close_button:
                close_button.on("click", lambda: editor.close_editor("/home"))

        ui.add_head_html('<link rel="stylesheet" href="/static/caption-cards.css?v=3">')
        card_style = app.storage.user.get("caption_card_style", "minimal")
        if card_style not in ("minimal", "classic"):
            card_style = "minimal"

        def set_card_style(event):
            style = event.value
            if style not in ("minimal", "classic"):
                return
            app.storage.user["caption_card_style"] = style
            # Change appearance without rebuilding cards or resetting playback.
            caption_panel.classes(
                add="caption-style-minimal" if style == "minimal" else "",
                remove="caption-style-minimal" if style == "classic" else "",
            )

        with ui.splitter(value=60).classes("w-full h-full") as splitter:
            with splitter.before:
                with ui.card().classes(
                    "w-full h-full caption-panel" + (" caption-style-minimal" if card_style == "minimal" else "")
                ) as caption_panel:
                    with ui.row().classes("w-full items-center justify-end gap-2"):
                        ui.toggle(
                            {"minimal": "Minimal", "classic": "Classic"},
                            value=card_style, on_change=set_card_style,
                        ).props("no-caps dense unelevated toggle-color=grey-8").classes("caption-style-toggle").tooltip(
                            "Classic restores the previous card appearance. Your choice is remembered."
                        )
                    with ui.scroll_area().style("height: calc(90vh - 140px);"):
                        editor.main_container = ui.column().classes("w-full h-full").props("id=subtitle-editor-captions")

                    if data_format == "srt":
                        editor.parse_srt(data["result"])
                    else:
                        editor.parse_txt(data["result"])

                    editor.refresh_display()
                with splitter.after:
                    with ui.card().classes("w-full h-full"):
                        ui.label(filename).classes("w-full text-base font-medium").style(
                            "line-height: 1.4; overflow-wrap: anywhere;"
                        )
                        video = ui.video(
                            f"/video/{uuid}",
                            controls=True,
                            autoplay=False,
                            loop=False,
                        ).classes("w-full h-full")
                        editor.set_video_player(video)
                        video.props("preload='auto' id=subtitle-editor-video")
                        with ui.row().classes("items-center gap-4"):
                            with ui.switch("Follow video") as autoscroll:
                                ui.tooltip(
                                    "Scroll the current caption into view during "
                                    "playback (click a caption to edit it)."
                                )
                            autoscroll.on(
                                "click",
                                lambda: editor.set_autoscroll(autoscroll.value),
                            )

                            with ui.switch(
                                "Preview subtitles", value=editor.subtitle_preview
                            ) as subtitle_preview_switch:
                                ui.tooltip(
                                    "Show the captions on the video the way a "
                                    "player would render them — including "
                                    "unsaved edits."
                                )
                            subtitle_preview_switch.on(
                                "click",
                                lambda: editor.set_subtitle_preview(
                                    subtitle_preview_switch.value
                                ),
                            )

                            editor.create_confidence_panel()

                        # Apply a persisted preview preference. The push must
                        # wait for the websocket connection — a fixed delay
                        # can fire before the client is connected on large
                        # jobs, and the run_javascript is then lost.
                        if editor.subtitle_preview:

                            async def apply_initial_preview() -> None:
                                await ui.context.client.connected()
                                editor.refresh_subtitle_preview(force=True)

                            ui.timer(0.1, apply_initial_preview, once=True)
                        editor.create_confidence_workspace()

                if data_format == "txt":
                    with splitter.after:
                        with ui.card().classes("w-full h-full"):
                            with ui.column().classes("p-4 w-full"):
                                ui.label("Speakers").classes("text-h6").style(
                                    "align-self: center;"
                                )
                                editor.render_speakers()
                                with ui.button("Prune", on_click=editor.prune_speakers).props("outline color=black").classes("text-black prune-btn"):
                                    ui.tooltip(
                                        "Remove speakers that are not assigned "
                                        "to any caption."
                                    )
                    
