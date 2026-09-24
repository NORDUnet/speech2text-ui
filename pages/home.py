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

from time import monotonic
from nicegui import ui, events
from utils.background_upload import owner_queue
from utils.upload_state import merge_rows
from utils.common import (
    default_styles,
    page_init,
    jobs_get,
    jobs_columns,
    table_click,
    table_upload,
    table_delete,
    table_bulk_export,
)


def create() -> None:
    @ui.refreshable
    @ui.page("/home")
    def home() -> None:
        """
        Main page of the application.
        """
        ui.add_head_html("""<script>
            if (window.top === window.self) location.replace('/workspace?view=' + encodeURIComponent(location.pathname + location.search));
        </script>""")
        page_init(use_drawer=True)

        def toggle_buttons(selected: list) -> None:
            """
            Toggle the state of buttons based on selected rows.
            """
            has_selection = bool(selected)
            delete.set_enabled(has_selection and all(r.get("status") != "Uploading" and (not r.get("local_upload") or r.get("status") == "Failed") for r in selected))

            # Update delete tooltip
            if has_selection:
                delete_tooltip.text = "Delete selected files"
            else:
                delete_tooltip.text = "Select one or more files to delete"

            # Enable bulk export only when all selected completed jobs share the same type
            completed = [r for r in selected if r.get("status") == "Completed"]
            formats = set(r.get("output_format", "") for r in completed)
            bulk_export.set_enabled(len(completed) >= 1 and len(formats) == 1)

            # Update export tooltip
            if not has_selection:
                export_tooltip.text = "Select one or more files to export"
            elif len(completed) >= 1 and len(formats) > 1:
                export_tooltip.text = "Subtitles and Transcript can't be exported together."
            elif len(completed) >= 1 and len(formats) == 1:
                export_tooltip.text = "Export selected files"
            else:
                export_tooltip.text = "Select one or more already completed files to export"

        table = ui.table(
            on_select=lambda e: toggle_buttons(e.selection),
            columns=jobs_columns,
            rows=[],
            selection="multiple",
            pagination=10,
        )
        table.props(":selected-rows-label=\"(n) => n + ' files selected'\"")
        # Don't show Quasar's "No data available" bottom layer — an empty list is
        # the expected state before a user uploads anything.
        table.props("hide-no-data flat separator=horizontal")
        ui.add_head_html('<link rel="stylesheet" href="/static/files-table.css?v=3">')

        # Custom header checkbox that selects/deselects ALL rows across all pages
        table.add_slot(
            "header-selection",
            """
            <q-checkbox
                :model-value="props.selected"
                @update:model-value="val => { if (!val) { $parent.$emit('deselect_all'); } else { props.selected = true; } }"
            />
            """,
        )

        def deselect_all():
            table.selected = []
            toggle_buttons([])

        table.on("deselect_all", deselect_all)

        def table_handle_row_click(e: events.GenericEventArguments) -> None:
            if e.args.get("status") == "Completed":
                table_click(e)

        ui.add_head_html(default_styles)

        table.style(
            "width: 100%; height: calc(100vh - 100px - var(--banner-offset, 0px)); box-shadow: none;"
        )
        table.classes("files-table")
        table.add_slot(
            "body-cell-status",
            """
            <q-td key="status" :props="props">
                <div class="file-status" :class="{'file-status-failed': props.value === 'Failed'}">
                    <span class="file-status-dot" aria-hidden="true"></span>
                    <span>{{ props.value }}</span>
                    <q-icon v-if="props.row.upload_error" name="info_outline" size="16px" tabindex="0" aria-label="Failure details">
                        <q-tooltip max-width="300px">{{ props.row.upload_error }}</q-tooltip>
                    </q-icon>
                </div>
                <div v-if="props.row.upload_progress" class="file-progress">{{ props.row.upload_progress }}</div>
            </q-td>
            """,
        )
        table.add_slot(
            "body-cell-action",
            """
            <q-td key="action" :props="props">
                <q-btn v-if="props.row.status === 'Completed'"
                    label="Edit" icon="edit" flat no-caps dense
                    class="file-edit-button"
                    :aria-label="'Edit ' + props.row.filename"
                    @click.stop="$parent.$emit('table_handle_row_click', props.row)"
                />
            </q-td>
            """,
        )
        table.add_slot(
            "body-cell-filename",
            """
            <q-td key="filename" :props="props">
                <span class="file-name">{{ props.value }}<q-tooltip>{{ props.value }}</q-tooltip></span>
            </q-td>
            """,
        )
        table.add_slot(
            "body-cell-deletion_date",
            """
            <q-td key="deletion_date" :props="props">
                <div :class="props.row.deletion_approaching ? 'deletion-warning' : ''">
                    <span>{{ props.row.deletion_date }}</span>
                    <q-icon
                        v-if="props.row.deletion_approaching"
                        name="warning"
                        class="deletion-warning-icon"
                    >
                        <q-tooltip>This file will be permanently deleted within 24 hours.</q-tooltip>
                    </q-icon>
                </div>
            </q-td>
            """,
        )
        table.on("table_handle_row_click", table_handle_row_click)

        with table.add_slot("top-left"):
            ui.label("My files").classes("files-title")

        with table.add_slot("top-right"):
            with ui.row().classes("items-center files-actions"):
                with ui.button("Delete", icon="delete") as delete:
                    delete.props("flat no-caps", remove="color")
                    delete.classes("file-toolbar-button")
                    delete.on("click", lambda: table_delete(table, on_deleted=forget_deleted))
                    delete.set_enabled(False)
                    delete_tooltip = ui.tooltip("Select one or more files to delete")

                with ui.button("Export", icon="download") as bulk_export:
                    bulk_export.props("flat no-caps", remove="color")
                    bulk_export.classes("file-toolbar-button")
                    bulk_export.on("click", lambda: table_bulk_export(table))
                    bulk_export.set_enabled(False)
                    export_tooltip = ui.tooltip("Select one or more files to export")

                with ui.button("Upload", icon="upload") as upload:
                    upload.props("flat no-caps", remove="color")
                    upload.classes("file-toolbar-button file-upload-button")
                    upload.on("click", lambda: table_upload(table))

        backend_rows = []
        last_fetch = None
        deleted_ids = set()

        def forget_deleted(uuid):
            nonlocal backend_rows
            deleted_ids.add(uuid)
            backend_rows = [r for r in backend_rows if r["uuid"] != uuid]
            table.selected = [r for r in table.selected if r["uuid"] != uuid]
            table.update_rows([r for r in table.rows if r["uuid"] != uuid], clear_selection=False)
            toggle_buttons(table.selected)


        async def update_rows(force=True):
            """
            Update the rows in the table.

            Avoid clearing the existing table during temporary backend/API failures.
            This can happen while large uploads are being stored and encrypted.
            """
            nonlocal backend_rows, last_fetch
            uploads = owner_queue()
            active = bool(uploads) or any(r['status'].lower() in ('transcribing', 'queued', 'uploading') for r in backend_rows)
            interval = 5.0 if active else 30.0
            if force or last_fetch is None or monotonic() - last_fetch >= interval:
                fetched = await jobs_get(include_uploads=False)
                last_fetch = monotonic()
                if fetched is not None:
                    backend_rows = [r for r in fetched if r["uuid"] not in deleted_ids]
            rows = [r for r in merge_rows(backend_rows, uploads) if r["uuid"] not in deleted_ids]

            # Replacing identical rows rebuilds hovered tooltips every tick.
            # Keep browser elements intact until displayed data changes.
            if rows == table.rows:
                return

            if not rows:
                delete.set_enabled(False)
                bulk_export.set_enabled(False)

            # Keep selection enabled even when empty: toggling to "none" and back
            # does not reliably re-render the per-row checkboxes without a page
            # reload, so newly uploaded files would appear without selection boxes.
            table.selection = "multiple"
            table.update_rows(rows, clear_selection=False)

        # Local progress is cheap and refreshed each second. The expensive API
        # listing (including filename decryption) retains the original cadence.
        ui.timer(1.0, lambda: update_rows(force=False))
        ui.timer(0.0, update_rows, once=True)
