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

import glob
import os
import re
import tempfile

import httpx
import pytz

from datetime import datetime, timedelta
from nicegui import ui, app
from starlette.formparsers import MultiPartParser
from typing import Optional
from utils.settings import get_settings
from utils.token import (
    get_admin_status,
    get_auth_header,
    get_bofh_status,
    get_user_data,
    token_refresh,
)
from utils.helpers import storage_decrypt, feedback_send

settings = get_settings()
# Keep large uploads off RAM (spill to disk past the spool threshold) and route
# temp files to the configured upload dir, off the small root disk. The dir is
# provisioned by ops; we only point at it.
MultiPartParser.spool_max_size = 1024 * 1024 * settings.MULTIPART_SPOOL_MAX_SIZE_MB
if settings.UPLOAD_TMP_DIR:
    tempfile.tempdir = settings.UPLOAD_TMP_DIR

UPLOAD_TEMP_PREFIX = "scribe-ui-upload-"
UPLOAD_TEMP_SUFFIX = ".upload"


def sweep_stale_uploads() -> None:
    """
    Remove orphaned upload temp files left behind by an unclean shutdown.

    Normal uploads delete their temp file in a finally block, but a crash or
    hard reset can leave plaintext upload temps on disk. Sweep them on startup.
    """
    tmp_dir = settings.UPLOAD_TMP_DIR or tempfile.gettempdir()
    pattern = os.path.join(tmp_dir, f"{UPLOAD_TEMP_PREFIX}*{UPLOAD_TEMP_SUFFIX}")
    for path in glob.glob(pattern):
        try:
            os.remove(path)
        except OSError:
            pass

def sanitize_filename(filename: str) -> str:
    """
    Remove or replace characters that are unsafe in filenames.
    """
    # Replace path separators and null bytes
    filename = filename.replace("/", "_").replace("\\", "_").replace("\x00", "")
    # Remove other problematic characters
    filename = re.sub(r'[<>:"|?*\x01-\x1f]', "_", filename)
    # Strip leading/trailing dots and spaces
    filename = filename.strip(". ")
    return filename or "unnamed"


jobs_columns = [
    {
        "name": "filename",
        "label": "Filename",
        "field": "filename",
        "align": "left",
        "classes": "text-weight-medium",
    },
    {
        "name": "job_type",
        "label": "Type",
        "field": "job_type",
        "align": "left",
        "classes": "text-weight-medium",
    },
    {
        "name": "created_at",
        "label": "Created",
        "field": "created_at",
        "align": "left",
    },
    {
        "name": "update_at",
        "label": "Modified",
        "field": "updated_at",
        "align": "left",
    },
    {
        "name": "deletion_date",
        "label": "Scheduled deletion",
        "field": "deletion_date",
        "align": "left",
    },
    {
        "name": "status",
        "label": "Status",
        "field": "status",
        "align": "left",
    },
    {"name": "action", "label": "Action", "field": "action", "align": "center"},
]

default_styles = """
    <style>
        /* Shared brand palette for NiceGUI controls and success states. */
        :root, .body--light, .body--dark {
            --color-nordunet-blue: #005eb8;
            --q-primary: var(--color-nordunet-blue) !important;
            --q-secondary: var(--color-nordunet-blue) !important;
            --q-accent: var(--color-nordunet-blue) !important;
            --q-positive: var(--color-nordunet-blue) !important;
        }
        /* ── Theme variables (light) ── */
        :root, .body--light {
            --color-bg-page: #ffffff;
            --color-bg-surface: #ffffff;
            --color-bg-surface-alt: #f5f5f5;
            --color-bg-hover: #e0e0e0;
            --color-control-bg: #ffffff;
            --color-header-bg: #ffffff;
            --color-text-primary: #000000;
            --color-text-muted: #666666;
            --color-brand: #082954;
            --color-accent: var(--color-nordunet-blue);
            --color-on-accent: #ffffff;
            --color-on-brand: #ffffff;
            /* Primary action buttons (Upload, Start transcribing, etc.) */
            --color-primary-btn-bg: var(--color-nordunet-blue);
            --color-primary-btn-text: #ffffff;
            /* Solid blue buttons (login, edit, etc.) */
            --color-btn-blue-bg: #082954;
            --color-delete-text: #721c24;
            --color-border: #000000;
            --color-disabled-bg: #e0e0e0;
            --color-disabled-border: #bdbdbd;
            --color-danger: #d32f2f;
        }
        /* ── Theme variables (dark) ── */
        .body--dark {
            --color-bg-page: #121212;
            --color-bg-surface: #000000;
            --color-bg-surface-alt: #181818;
            --color-bg-hover: #333333;
            --color-control-bg: #2d2d2d;
            --color-header-bg: #1e1e1e;
            --color-text-primary: #ffffff;
            --color-text-muted: #b0b0b0;
            /* Tailwind v4 builds .text-gray-N as `color: var(--color-gray-N)`
               inside a cascade @layer, so a scoped property override is
               unreliable. Redefining the variables globally in dark mode makes
               every .text-gray-* render white regardless of DOM context.
               Shades 400-800 are used only for text here; gray-900 is also used
               as bg-gray-900, so it is recoloured scoped to text elsewhere. */
            --color-gray-400: #ffffff;
            --color-gray-500: #ffffff;
            --color-gray-600: #ffffff;
            --color-gray-700: #ffffff;
            --color-gray-800: #ffffff;
            --color-brand: #5b9bd5;
            --color-accent: var(--color-nordunet-blue);
            --color-on-accent: #ffffff;
            --color-on-brand: #ffffff;
            /* Primary action buttons + solid blue buttons: NORDUnet blue, white text */
            --color-primary-btn-bg: #005eb8;
            --color-primary-btn-text: #ffffff;
            --color-btn-blue-bg: #005eb8;
            --color-delete-text: #ff8a80;
            --color-border: #5a5a5a;
            --color-disabled-bg: #333333;
            --color-disabled-border: #555555;
            --color-danger: #ef5350;
        }

        /* ── Dark-mode overrides for hardcoded light colors ──
           A stylesheet !important beats inline styles set without !important,
           so these flip the app's hardcoded white surfaces/black text without
           having to edit every element. */
        /* Buttons with color="primary" (the theme picker's selected segment and
           various dialog Save/Close/Test buttons) render via Quasar's bg-primary
           / text-primary, whose `!important` lives in a cascade @layer and beats
           an unlayered override regardless of specificity. So instead of fighting
           the property, point the variable they read (--q-primary) at the brand
           blue — scoped to buttons so links/radios elsewhere keep Quasar's primary. */
        .body--dark .q-btn,
        .body--dark .q-btn-toggle,
        .body--dark .q-btn-toggle .q-btn[aria-pressed="true"] {
            --q-primary: var(--color-btn-blue-bg) !important;
        }
        .body--dark body,
        .body--dark .nicegui-content {
            background-color: var(--color-bg-page) !important;
        }
        /* Header text follows the theme in BOTH modes (Quasar's q-header
           defaults to white text, which is invisible on the light header).
           Flat icon buttons + title inherit this; the red admin buttons keep
           their own text-red colour. */
        .q-header {
            color: var(--color-text-primary) !important;
        }
        .body--dark .q-header {
            background-color: var(--color-header-bg) !important;
        }
        .body--dark .q-drawer {
            background-color: var(--color-bg-surface-alt) !important;
        }
        .body--dark .q-card {
            background-color: var(--color-bg-surface) !important;
            color: var(--color-text-primary);
        }
        /* Transcribe dialog "N file(s) will be transcribed" notice: the light
           cream (#fff3e0) is unreadable with the dark-mode white text/icons. */
        .body--dark .transcribe-banner {
            background-color: #4a3a1e !important;
        }
        .body--dark .transcribe-banner .q-icon {
            color: #ffffff !important;
        }
        /* Export dialog sticky footer: inline `background: white` leaves a bright
           strip at the bottom of the otherwise-dark dialog. */
        .body--dark .export-footer {
            background: var(--color-bg-surface) !important;
        }
        /* The customer expansion carries `text-bold` to bold its header label,
           which also bolds every card inside it. Reset the expansion content to
           normal weight so grouped cards match the ungrouped "All users" card —
           only each card's own font-bold/font-semibold parts (name, "Statistics",
           "This month"/"Last month") stay bold. */
        .q-expansion-item__content {
            font-weight: 400;
        }
        /* gray-400..800 are recoloured via global vars above; gray-900 is kept
           scoped here so bg-gray-900 (used as a dark surface) is unaffected.
           The color declaration is a fallback for the case the Tailwind utility
           is built without !important. */
        .body--dark .text-black,
        .body--dark .text-gray-900,
        .body--dark .text-gray-800,
        .body--dark .text-gray-700,
        .body--dark .text-gray-600,
        .body--dark .text-gray-500,
        .body--dark .text-gray-400 {
            --color-gray-900: #ffffff;
            color: var(--color-text-primary) !important;
        }
        /* Icons/labels whose colour is set inline to black (NiceGUI renders
           color="black" as an inline style, not a class). */
        .body--dark [style*="color: black"],
        .body--dark [style*="color:black"],
        .body--dark [style*="color: #000"],
        .body--dark [style*="color:#000"] {
            color: var(--color-text-primary) !important;
        }
        .body--dark .menu-item:hover { background-color: var(--color-bg-hover); }

        .q-chip {
            background-color: var(--color-accent) !important;
            color: var(--color-on-accent) !important;
        }
        /* Announcement banners set light pastel backgrounds + dark text/icon
           inline (good for light mode). In dark mode, give each severity a dark
           tint with light text so the bar fits the theme. Overrides the inline
           styles via !important; keyed off the .severity-* classes. */
        .body--dark .announcement-banner.severity-info {
            background-color: #1a5490 !important;
            border-color: #2e74ad !important;
        }
        .body--dark .announcement-banner.severity-maintenance {
            background-color: #6f4f18 !important;
            border-color: #a07a2c !important;
        }
        .body--dark .announcement-banner.severity-major_incident {
            background-color: #7a2727 !important;
            border-color: #ab3a3a !important;
        }
        .body--dark .announcement-banner.severity-info,
        .body--dark .announcement-banner.severity-info * {
            color: #d6e9ff !important;
        }
        .body--dark .announcement-banner.severity-maintenance,
        .body--dark .announcement-banner.severity-maintenance * {
            color: #ffdca8 !important;
        }
        .body--dark .announcement-banner.severity-major_incident,
        .body--dark .announcement-banner.severity-major_incident * {
            color: #ffc4c4 !important;
        }
        .body--dark .announcement-banner a {
            color: #9cc4ff !important;
            text-decoration: underline;
        }
        .default-style {
            background-color: var(--color-primary-btn-bg);
            color: var(--color-primary-btn-text) !important;
            border: 1px solid var(--color-border);
        }
        .default-style.disabled {
            background-color: var(--color-disabled-bg) !important;
            border: 1px solid var(--color-disabled-border) !important;
            opacity: 0.7;
        }
        .delete-style {
            background-color: var(--color-control-bg);
            color: var(--color-text-primary) !important;
            border: 1px solid var(--color-border);
            width: 150px;
        }
        .delete-style.disabled {
            background-color: var(--color-disabled-bg) !important;
            border: 1px solid var(--color-disabled-border) !important;
            opacity: 0.7;
        }
        .table-style th {
            font-size: 14px;
        }
        .table-style tr {
            font-size: 14px;
        }
        /* Editor caption action buttons (Split / Merge / Close / Add) are flat
           text buttons; the brand blue #005eb8 reads muddy as thin text on the
           black editor, so use a lightened NORDUnet blue here for legibility.
           Direct, higher-specificity rule beats Quasar's unlayered .text-primary
           (and sidesteps the inline --q-primary the buttons inherit). */
        .body--dark .caption-btn-row .q-btn:not(.caption-delete-btn) {
            --q-primary: #2d8fe0 !important;
        }
        /* Editor "Delete" caption button: keep it red but brighter so it stands
           out on the black editor like the blue buttons do. Quasar's .text-red
           is unlayered !important, so a higher-specificity dark rule wins. */
        .body--dark .caption-delete-btn {
            color: #ff5252 !important;
        }
        /* Help dialog: the outer card hardcodes a white gradient background, so
           in dark mode it shows as a white dialog while the global .q-card rule
           forces the inner cards black — black boxes on white. Give the dialog a
           dark surface, raise the inner cards onto a slightly lighter surface,
           and force the dim step text (text-grey-8) white. The `background`
           shorthand with !important also clears the inline white gradient.
           All scoped to dark mode; light mode keeps the gradient + grey text. */
        .body--dark .q-card.help-dialog {
            background: var(--color-bg-surface) !important;
        }
        .body--dark .help-dialog .q-card {
            background: var(--color-bg-surface-alt) !important;
        }
        .body--dark .help-dialog .text-grey-8 {
            color: var(--color-text-primary) !important;
        }
        /* Keyboard-shortcuts dialog key chips use Tailwind bg-gray-100, which
           stays light grey in dark mode → near-white text on light grey,
           illegible. Override the variable bg-gray-100 reads (scoped to the chip)
           so it gets a dark surface in dark mode. Light mode keeps grey-100. */
        .body--dark .kbd-key {
            --color-gray-100: var(--color-control-bg);
        }
        /* "Prune" speakers button is an outline button forced to color=black
           (.text-black), making it black-on-black (invisible) in dark mode.
           Tailwind v4 builds .text-black as `color: var(--color-black)` inside a
           cascade @layer with !important, which beats an unlayered override — so
           recolour the variable it reads (scoped to this button) rather than the
           property. The outline border uses currentColor, so this reveals both
           the label and the border. Light mode keeps text-black (black). */
        .body--dark .prune-btn {
            --color-black: #ffffff;
            color: var(--color-text-primary) !important;
        }
        /* Row hover: brighter than Quasar's default so the hovered line clearly
           stands out on dark tables (My files, admin lists, group statistics).
           Excludes selected rows so the navy selection isn't overridden. */
        .body--dark .q-table tbody tr:not(.selected):hover,
        .body--dark .q-table tbody tr:not(.selected):hover > td {
            background-color: #454545 !important;
        }
        /* Toggle (e.g. the appearance Light/Dark/Auto picker) hover: same
           brighter grey as the table row hover so it clearly stands out,
           instead of Quasar's faint default. The selected segment keeps its
           blue (excluded via aria-pressed). */
        .body--dark .q-btn-toggle .q-btn:not([aria-pressed="true"]):hover {
            background-color: #454545 !important;
        }
        /* Plotly charts use template="plotly_white" — a baked white background
           with dark text, jarring/illegible in dark mode. Make the chart
           backgrounds transparent so the dark card shows through, and recolour
           the text and gridlines. Bar/line trace colours are left intact.
           CSS beats Plotly's inline SVG styling, and this covers every chart
           (stats, health, heatmaps) in all modes. Dark mode only. */
        .body--dark .js-plotly-plot .bg {
            fill: transparent !important;
        }
        .body--dark .js-plotly-plot .main-svg {
            background-color: transparent !important;
        }
        .body--dark .js-plotly-plot text {
            fill: var(--color-text-primary) !important;
        }
        .body--dark .js-plotly-plot .gridlayer path,
        .body--dark .js-plotly-plot .zerolinelayer path {
            stroke: #3a3a3a !important;
        }
        /* Selected rows: muted navy — keeps the NORDUnet-blue identity but at low
           saturation so a full-width selected row reads as highlighted rather
           than a loud blue slab, and stays easy on the eyes in dark mode.
           Applies to all selectable tables (My files, edit group, admin lists). */
        .body--dark .q-table tbody tr.selected,
        .body--dark .q-table tbody tr.selected > td {
            background-color: #1b3a5e !important;
        }
        .cancel-style {
            background-color: var(--color-control-bg);
            color: var(--color-text-primary) !important;
            border: 1px solid var(--color-border);
            width: 150px;
        }
        .upload-style {
            width: 100%;
            height: 200px;
        }
        .upload-dropzone {
            border-color: var(--color-border);
            background-color: var(--color-bg-surface-alt);
            color: var(--color-text-muted);
            transition: background-color 0.15s;
        }
        /* Dropzone prompt text: muted grey is too dim on the dark dropzone —
           use the primary (white) text colour so it stands out. Dark mode only. */
        .body--dark .upload-dropzone {
            color: var(--color-text-primary);
        }
        .upload-dropzone:hover,
        .upload-dropzone.dragover {
            background-color: var(--color-bg-hover);
        }
        .button-default-style {
            background-color: var(--color-btn-blue-bg) !important;
            color: var(--color-on-brand) !important;
            width: 150px;
        }
        .button-replace {
            background-color: var(--color-control-bg);
            color: var(--color-brand) !important;
            border : 1px solid var(--color-brand);
            width: 150px;
        }
        .button-replace-current {
            background-color: var(--color-accent);
            color: var(--color-on-accent) !important;
            width: 150px;
        }
        .button-replace-prev-next {
            background-color: var(--color-control-bg);
            color: var(--color-brand) !important;
        }
        .button-close {
            background-color: var(--color-control-bg);
            color: var(--color-text-primary) !important;
            width: 150px;
            border: 1px solid var(--color-border);
        }
        .button-user-status {
            background-color: var(--color-control-bg);
            color: var(--color-text-primary) !important;
            width: 150px;
            border: 1px solid var(--color-border);
        }
        .button-edit {
            background-color: var(--color-btn-blue-bg);
            color: var(--color-on-brand) !important;
            width: 150px;
        }
        /* NiceGUI buttons default to color="primary" (blue). These higher-
           specificity rules beat Quasar's .text-primary so the button text
           follows the theme (black in light, white in dark) — matching the
           original color="black" look in light mode. */
        .q-btn.default-style {
            color: var(--color-primary-btn-text) !important;
        }
        /* Secondary and disabled actions do not use the filled blue palette. */
        .secondary-style {
            background-color: var(--color-control-bg);
            border: 1px solid var(--color-border);
        }
        .q-btn.secondary-style {
            color: var(--color-text-primary) !important;
        }
        .q-btn.default-style.disabled,
        .q-btn.secondary-style.disabled,
        .q-btn.delete-style.disabled {
            /* Match the original Quasar disabled treatment, including icons. */
            color: var(--color-text-primary) !important;
            background-color: var(--color-disabled-bg) !important;
            border-color: var(--color-disabled-border) !important;
            opacity: 0.6 !important;
        }
        .q-btn.delete-style,
        .q-btn.cancel-style,
        .q-btn.button-close,
        .q-btn.button-user-status {
            color: var(--color-text-primary) !important;
        }
        /* Header icon buttons follow the theme; the red admin buttons keep red. */
        .q-header .q-btn:not(.text-red) {
            color: var(--color-text-primary) !important;
        }
        .deletion-warning {
            color: var(--color-danger);
            font-weight: 500;
            display: flex;
            align-items: center;
            gap: 4px;
        }
        .deletion-warning-icon {
            font-size: 18px;
        }
        .q-tooltip {
            font-size: 14px;
            white-space: nowrap;
        }
    </style>
"""


def show_help_dialog() -> None:
    """Explain the current upload and editing workflow without API lookups."""
    max_size = float(settings.MAX_UPLOAD_BYTES)
    for size_unit in ('bytes', 'KiB', 'MiB', 'GiB', 'TiB'):
        if max_size < 1024 or size_unit == 'TiB':
            break
        max_size /= 1024

    steps = [
        ('Upload & configure', 'upload',
         f'Choose up to 5 files, max {max_size:g} {size_unit} each. Select language and output type, then click Upload.'),
        ('Monitor in My files', 'folder',
         'Follow upload progress and job status. Transcription starts automatically after upload.',
         'Keep this tab open until transfer finishes. You can browse other pages within it.'),
        ('Review & edit', 'edit',
         'Click Edit on a completed file. Correct the text and preview subtitles on the video.'),
        ('Save & export', 'download',
         'Save your changes, then choose Export to download your transcript or subtitles.'),
    ]

    def help_box(title, icon, description, note=None, tint=False):
        with ui.card().classes('no-shadow w-full').style(
            'border: 1px solid var(--color-border, #dedede); border-radius: 12px; '
            'padding: 18px; gap: 10px; height: 100%; '
            + ('background: color-mix(in srgb, #005eb8 5%, var(--color-bg-surface, white));' if tint
               else 'background: var(--color-bg-surface, white);')
        ):
            with ui.row().classes('items-center no-wrap gap-2'):
                ui.icon(icon).classes('text-xl opacity-70')
                ui.label(title).style('font-size: 15px; font-weight: 600; line-height: 1.4;')
            ui.label(description).style('font-size: 14px; line-height: 1.6; opacity: 0.85;')
            if note:
                ui.label(note).style('font-size: 13px; line-height: 1.5; opacity: 0.75;')

    with ui.dialog() as dialog:
        with ui.card().classes('help-dialog no-shadow').style(
            'width: 900px; max-width: 94vw; max-height: 85vh; overflow-y: auto; '
            'padding: 24px; background-color: var(--color-bg-surface); gap: 16px;'
        ):
            with ui.row().classes('w-full items-center justify-between'):
                ui.label('Getting the most out of Speech2Text').style('font-size: 22px; font-weight: 500; line-height: 1.3;')
                ui.button(icon='close', on_click=dialog.close).props(
                    'flat round dense aria-label="Close help"', remove='color'
                )
            ui.label('Getting started').style(
                'font-size: 12px; font-weight: 600; letter-spacing: 0.08em; '
                'text-transform: uppercase; opacity: 0.65; margin-top: 8px;'
            )
            with ui.element('div').style(
                'display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 300px), 1fr)); gap: 14px; width: 100%;'
            ):
                for index, step in enumerate(steps, 1):
                    title, icon, description, *note = step
                    help_box(f'{index}. {title}', icon, description, note[0] if note else None)

            ui.separator().classes('my-1')
            ui.label('Useful to know').style(
                'font-size: 12px; font-weight: 600; letter-spacing: 0.08em; '
                'text-transform: uppercase; opacity: 0.65;'
            )
            with ui.element('div').style(
                'display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 220px), 1fr)); gap: 14px; width: 100%;'
            ):
                help_box('User settings', 'person',
                         'Set your default language here. Remember output type and advanced options in the upload window.')
                help_box('Editor tools', 'keyboard',
                         'Open Shortcuts for keyboard commands. When available, Review words lets you adjust the confidence threshold and review uncertain words.')
                help_box('Give feedback', 'rate_review',
                         'Share ideas or report a problem using Give feedback in the side menu.')
            with ui.element('div').style(
                'display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 300px), 1fr)); gap: 14px; width: 100%;'
            ):
                help_box('Privacy', 'security',
                         'Stored files are encrypted and automatically deleted. See the scheduled deletion date in My files.', tint=True)
                help_box('Support', 'help',
                         "Contact your institution's IT department for technical support or questions.", tint=True)
        dialog.open()


def logout() -> None:
    """
    Log out the user by clearing the token and navigating to the logout endpoint.
    """

    app.storage.user["token"] = None
    app.storage.user["refresh_token"] = None
    app.storage.user["encryption_password"] = None

    ui.navigate.to(settings.OIDC_APP_LOGOUT_ROUTE)


def _show_announcement_banners() -> None:
    """Show active announcement banners below the header."""

    user_data = get_user_data()
    if not user_data:
        return

    announcements = user_data.get("announcements", [])
    if not announcements:
        return

    dismissed = app.storage.user.get("dismissed_announcements", [])

    severity_styles = {
        "info": {
            "bg": "#e3f2fd",
            "border": "#90caf9",
            "icon": "campaign",
            "icon_color": "#1565c0",
            "dismissible": True,
        },
        "maintenance": {
            "bg": "#fff3e0",
            "border": "#ffb74d",
            "icon": "construction",
            "icon_color": "#e65100",
            "dismissible": True,
        },
        "major_incident": {
            "bg": "#fce4ec",
            "border": "#ef9a9a",
            "icon": "crisis_alert",
            "icon_color": "#c62828",
            "dismissible": False,
        },
    }

    visible_count = 0
    for a in announcements:
        sev = a.get("severity", "info")
        style = severity_styles.get(sev, severity_styles["info"])
        if style["dismissible"] and a.get("id") in dismissed:
            continue
        visible_count += 1

    if visible_count > 0:
        ui.add_head_html(
            f"<style>:root {{ --banner-offset: {visible_count * 40}px; }}</style>"
        )

    # CSS for links inside banners
    ui.add_head_html(
        "<style>"
        ".announcement-banner a { color: #1565c0; text-decoration: underline;"
        " font-weight: 500; }"
        ".announcement-banner a:hover { text-decoration: underline;"
        " opacity: 0.8; }"
        ".announcement-banner.severity-maintenance a { color: #bf360c; }"
        ".announcement-banner.severity-major_incident a { color: #b71c1c; }"
        "</style>"
    )

    # JS to fix link attributes (target, rel) for all banner links
    ui.add_head_html(
        "<script>"
        "document.addEventListener('DOMContentLoaded', function() {"
        "  new MutationObserver(function() {"
        "    document.querySelectorAll('.announcement-banner a').forEach(function(a) {"
        "      if (!a.getAttribute('target')) a.setAttribute('target', '_top');"
        "      try { var u = new URL(a.href, location.origin);"
        "        if (u.origin !== location.origin)"
        "          a.setAttribute('rel', 'noopener noreferrer');"
        "      } catch(e) {}"
        "    });"
        "  }).observe(document.body, {childList: true, subtree: true});"
        "});"
        "</script>"
    )

    for announcement in announcements:
        ann_id = announcement.get("id")
        sev = announcement.get("severity", "info")
        style = severity_styles.get(sev, severity_styles["info"])

        if style["dismissible"] and ann_id in dismissed:
            continue

        banner_container = (
            ui.element("div")
            .classes(f"announcement-banner severity-{sev}")
            .style(
                f"background-color: {style['bg']}; border-bottom: 1px solid {style['border']};"
                " padding: 8px 20px; display: flex; align-items: center;"
                " justify-content: space-between;"
                " margin-left: -2rem; margin-right: -2rem; margin-top: -1rem;"
                " width: calc(100% + 4rem);"
            )
        )

        with banner_container:
            with ui.element("div").style(
                "display: flex; align-items: center; gap: 10px; flex: 1;"
            ):
                ui.icon(style["icon"], size="sm").style(
                    f"color: {style['icon_color']};"
                )
                ui.html(announcement.get("message", ""), sanitize=False).style(
                    "color: #000000; font-size: 0.95rem;"
                )

            if style["dismissible"]:

                def dismiss(a_id=ann_id, container=banner_container):
                    current = app.storage.user.get("dismissed_announcements", [])
                    if a_id not in current:
                        current.append(a_id)
                        app.storage.user["dismissed_announcements"] = current
                    container.set_visibility(False)

                ui.button(icon="close", on_click=dismiss).props(
                    "flat round dense size=sm color=grey-7"
                )


def show_feedback_dialog() -> None:
    """
    Dialog for submitting feedback (bug report, feature idea or other).
    Stored in the database for admin review; no notifications are sent.
    """

    try:
        client = ui.context.client
        current_path = client.page.path if client and client.page else ""
    except Exception:
        current_path = ""

    with ui.dialog() as dialog:
        with ui.card().style("min-width: 420px; max-width: 90vw;"):
            ui.label("Send feedback").classes("text-h6")
            ui.label(
                "Tell us about a problem or suggest a new feature. Your feedback "
                "is stored for the service administrators to review."
            ).classes("text-subtitle2").style("margin-bottom: 10px;")

            category_select = ui.select(
                {"feature": "Feature idea", "bug": "Bug report", "other": "Other"},
                value="feature",
                label="Category",
            ).style("width: 100%;")

            message_input = (
                ui.textarea("Your feedback")
                .props("autogrow maxlength=5000 counter")
                .style("width: 100%; margin-bottom: 10px;")
            )

            anonymous_checkbox = ui.checkbox("Submit anonymously")
            anonymous_checkbox.tooltip(
                "Your name and user ID are not stored with the feedback. "
                "Only your organisation is recorded, so the right "
                "administrators can see it."
            )

            def send() -> None:
                message = (message_input.value or "").strip()
                if not message:
                    ui.notify("Please enter your feedback.", color="negative")
                    return

                if feedback_send(
                    category_select.value,
                    message,
                    current_path,
                    anonymous=anonymous_checkbox.value,
                ):
                    ui.notify("Thank you for your feedback!", color="positive")
                    dialog.close()
                else:
                    ui.notify("Failed to send feedback.", color="negative")

            with ui.row().classes("justify-end w-full gap-2"):
                ui.button(
                    "Cancel",
                    on_click=dialog.close,
                ).classes("button-close").props("flat", remove="color").style(
                    "margin-top: 10px;"
                )
                ui.button(
                    "Send",
                    icon="send",
                    on_click=send,
                ).classes("default-style").props("flat", remove="color").style(
                    "margin-top: 10px;"
                )

    dialog.open()


def page_init(header_text: Optional[str] = "", use_drawer: bool = False) -> ui.dark_mode | None:
    """
    Initialize the page with a header and background color.
    """

    if "_scribe_bk" not in app.storage.browser:
        ui.navigate.to("/")
        return

    def refresh():
        if not token_refresh():
            app.storage.user["token"] = None
            app.storage.user["refresh_token"] = None
            app.storage.user["encryption_password"] = None

            ui.navigate.to(settings.OIDC_APP_LOGOUT_ROUTE)

    refresh()

    is_admin = get_admin_status()
    is_bofh = get_bofh_status()
    ui.timer(30, refresh)

    try:
        client = ui.context.client
        current_path = client.page.path if client and client.page else ""
    except Exception:
        current_path = ""

    if header_text:
        header_text = f" - {header_text}"

    if is_admin:
        header_text += " (Administrator)"

    # Dark mode: None = auto (follow system), True = dark, False = light.
    # Stored per session in app.storage.user and applied live (no page reload).
    app.storage.user.setdefault("dark_mode", None)
    dark = ui.dark_mode(app.storage.user["dark_mode"]).bind_value(app.storage.user, "dark_mode")

    # The "Prune" outline button is forced to color=black (.text-black). In dark
    # mode that black is set by an adopted/layered Quasar rule that outranks any
    # stylesheet override we can inject, so enforce visibility with an inline
    # !important colour (the only thing that always wins) — applied solely when
    # body--dark is active, so light mode is untouched. Re-runs on theme toggle
    # and DOM changes (the button re-renders when speakers change).
    ui.add_head_html(
        "<script>(function(){function fix(){var d=document.body.classList."
        "contains('body--dark');document.querySelectorAll('.prune-btn,"
        " .help-dialog .text-grey-8, .upload-status, .upload-spinner,"
        " .simulate-dialog .text-grey-7, .simulate-dialog .text-grey-8')."
        "forEach(function(b){if(d){b.style.setProperty('color','#ffffff',"
        "'important');}else{b.style.removeProperty('color');}});"
        "document.querySelectorAll('.row-action-btn').forEach(function(b){"
        "if(d){b.style.setProperty('background-color','#e0e0e0','important');"
        "b.style.setProperty('color','#000000','important');}else{"
        "b.style.removeProperty('background-color');b.style.removeProperty('color');}});"
        "document.querySelectorAll('.add-attr-btn').forEach(function(b){"
        "if(d){b.style.setProperty('color','#2d8fe0','important');}else{"
        "b.style.removeProperty('color');}});}"
        "if(!window.__pruneFixObs){window.__pruneFixObs=new MutationObserver(fix);"
        "window.__pruneFixObs.observe(document.documentElement,{attributes:true,"
        "attributeFilter:['class'],childList:true,subtree:true});}fix();"
        "setTimeout(fix,150);setTimeout(fix,600);})();</script>"
    )

    def _dark_icon(val) -> str:
        if val is None:
            return "brightness_auto"
        return "dark_mode" if val else "light_mode"

    def cycle_dark(btn) -> None:
        current = app.storage.user.get("dark_mode", None)
        # auto -> dark -> light -> auto
        new_val = True if current is None else (False if current else None)
        app.storage.user["dark_mode"] = new_val
        dark.value = new_val
        btn._props["icon"] = _dark_icon(new_val)
        btn.update()

    if use_drawer:
        drawer_open = app.storage.user.get("drawer_open", False)
        drawer = ui.left_drawer(value=True, elevated=True).style(
            "background-color: var(--color-bg-surface-alt); padding: 0;"
        )

        drawer.props(':mini-width="56" :width="250" :breakpoint="0"')

        if not drawer_open:
            drawer.props(add="mini")

        menu_tooltips = []

        menu_btn = None

        def toggle_drawer():
            is_open = app.storage.user.get("drawer_open", False)
            if is_open:
                drawer.props(add="mini")
                for t in menu_tooltips:
                    t.set_visibility(True)
                if menu_btn:
                    menu_btn._props["icon"] = "menu"
                    menu_btn.update()
                if menu_btn_tooltip_ref:
                    menu_btn_tooltip_ref.text = "Expand menu"
                    menu_btn_tooltip_ref.update()
            else:
                drawer.props(remove="mini")
                for t in menu_tooltips:
                    t.set_visibility(False)
                if menu_btn:
                    menu_btn._props["icon"] = "close"
                    menu_btn.update()
                if menu_btn_tooltip_ref:
                    menu_btn_tooltip_ref.text = "Close menu"
                    menu_btn_tooltip_ref.update()
            app.storage.user["drawer_open"] = not is_open

        menu_btn_tooltip_ref = None

        menu_item_style = (
            "display: flex; align-items: center; gap: 12px; padding: 10px 16px;"
            " cursor: pointer; font-size: 1.05rem;"
            " transition: background-color 0.15s; width: 100%;"
            " white-space: nowrap; overflow: hidden;"
        )
        menu_active_style = " background-color: var(--color-bg-hover); font-weight: 600;"
        menu_hover_css = """
            <style>
                .menu-item:hover { background-color: #e0e0e0; }
                .q-drawer--mini .menu-header { display: none; }
                .q-drawer--mini .menu-separator { margin: 4px 0; }
                .q-drawer--mini .menu-item { justify-content: center; padding: 10px 0; gap: 0; }
                .q-drawer--mini .menu-item .q-icon { margin: 0; }
                .q-drawer--mini .menu-label { display: none; }
            </style>
        """

        def menu_style(path: str) -> str:
            active = current_path == path
            return menu_item_style + (menu_active_style if active else "")

        # Menu items: (path, icon, label)
        menu_items = [
            ("/home", "folder", "My files"),
            ("/user", "person", "User settings"),
        ]

        admin_items = [
            ("/admin/users", "people", "Users"),
            ("/admin", "group_work", "Groups"),
            ("/admin/rules", "rule", "User provisioning"),
            ("/admin/customers", "business", "Customers" if is_bofh else "Account"),
            ("/admin/feedback", "reviews", "Feedback"),
        ]

        system_items = [
            ("/health", "health_and_safety", "System status"),
            ("/admin/analytics", "analytics", "Activity overview"),
            ("/admin/announcements", "campaign", "Announcements"),
        ]

        with drawer:
            ui.add_head_html(menu_hover_css)
            with ui.column().classes("w-full").style("gap: 0;"):
                ui.separator()

                show_tips = not drawer_open

                for path, icon, label in menu_items:
                    with ui.element("div").style(menu_style(path)).classes(
                        "menu-item"
                    ).on("click", lambda p=path: ui.navigate.to(p)):
                        ui.icon(icon, color="black").style("font-size: 20px;")
                        ui.label(label).classes("menu-label")
                        t = ui.tooltip(label)
                        t.set_visibility(show_tips)
                        menu_tooltips.append(t)

                with ui.element("div").style(menu_item_style).classes(
                    "menu-item"
                ).on("click", lambda: show_feedback_dialog()):
                    ui.icon("rate_review", color="black").style("font-size: 20px;")
                    ui.label("Give feedback").classes("menu-label")
                    t = ui.tooltip("Give feedback")
                    t.set_visibility(show_tips)
                    menu_tooltips.append(t)

                if is_admin:
                    ui.separator().classes("menu-separator")
                    ui.label("Administration").classes("menu-header").style(
                        "padding: 10px 16px 4px; font-weight: bold; font-size: 0.85rem; color: #666;"
                    )

                    for path, icon, label in admin_items:
                        with ui.element("div").style(menu_style(path)).classes(
                            "menu-item"
                        ).on("click", lambda p=path: ui.navigate.to(p)):
                            ui.icon(icon, color="black").style("font-size: 20px;")
                            ui.label(label).classes("menu-label")
                            t = ui.tooltip(label)
                            t.set_visibility(show_tips)
                            menu_tooltips.append(t)

                    # Temporarily hidden; remove hidden to restore the API menu item.
                    with ui.element("div").style(menu_item_style).classes(
                        "menu-item hidden"
                    ).on(
                        "click",
                        lambda: ui.run_javascript(
                            "window.open('"
                            + settings.API_URL
                            + "/api/docs?theme="
                            + (
                                "dark"
                                if app.storage.user.get("dark_mode") is True
                                else "light"
                                if app.storage.user.get("dark_mode") is False
                                else "auto"
                            )
                            + "', '_blank')"
                        ),
                    ):
                        ui.icon("description", color="black").style("font-size: 20px;")
                        ui.label("API documentation").classes("menu-label")
                        t = ui.tooltip("API documentation")
                        t.set_visibility(show_tips)
                        menu_tooltips.append(t)

                if is_bofh:
                    ui.separator().classes("menu-separator")
                    ui.label("System").classes("menu-header").style(
                        "padding: 10px 16px 4px; font-weight: bold; font-size: 0.85rem; color: #666;"
                    )

                    for path, icon, label in system_items:
                        with ui.element("div").style(menu_style(path)).classes(
                            "menu-item"
                        ).on("click", lambda p=path: ui.navigate.to(p)):
                            ui.icon(icon, color="black").style("font-size: 20px;")
                            ui.label(label).classes("menu-label")
                            t = ui.tooltip(label)
                            t.set_visibility(show_tips)
                            menu_tooltips.append(t)

                ui.separator()

                with ui.element("div").style(menu_item_style).classes("menu-item").on(
                    "click", lambda: ui.run_javascript("window.top.location.href = '/logout'")
                ):
                    ui.icon("logout", color="black").style("font-size: 20px;")
                    ui.label("Logout").classes("menu-label")
                    t = ui.tooltip("Logout")
                    t.set_visibility(show_tips)
                    menu_tooltips.append(t)

        with (
            ui.header()
            .style("justify-content: space-between; background-color: var(--color-bg-surface);")
            .classes("drop-shadow-md")
        ):
            with ui.element("div").style(
                "display: flex; gap: 0px; align-items: center; margin-left: -12px;"
            ):
                with ui.button(
                    icon="close" if drawer_open else "menu",
                    on_click=lambda: toggle_drawer(),
                ).props("flat", remove="color") as menu_btn:
                    menu_btn_tooltip = ui.tooltip(
                        "Close menu" if drawer_open else "Expand menu"
                    )
                    menu_btn_tooltip_ref = menu_btn_tooltip
                ui.image(f"static/{settings.LOGO_TOPBAR}").classes(
                    "q-mr-sm cursor-pointer"
                ).style("height: 30px; width: 30px;").on(
                    "click", lambda: ui.navigate.to("/home")
                )
                ui.label(settings.TOPBAR_TEXT + header_text).classes(
                    "text-h6 cursor-pointer"
                ).on("click", lambda: ui.navigate.to("/home"))

            with ui.element("div").style("display: flex; gap: 0px;"):
                with ui.button(
                    "Feedback",
                    icon="rate_review",
                    on_click=lambda: show_feedback_dialog(),
                ).props("flat", remove="color"):
                    ui.tooltip("Report a problem or suggest a feature")
                with ui.button(
                    icon=_dark_icon(app.storage.user.get("dark_mode", None)),
                ).props("flat", remove="color") as dark_btn:
                    ui.tooltip("Light / dark / auto")
                dark_btn.bind_icon_from(dark, "value", backward=_dark_icon)
                dark_btn.on("click", lambda: cycle_dark(dark_btn))
                with ui.button(
                    icon="help",
                    on_click=lambda: show_help_dialog(),
                ).props("flat", remove="color"):
                    ui.tooltip("Help")

            ui.add_head_html(
                "<style>"
                "body { background-color: var(--color-bg-surface); }"
                ".nicegui-content { padding-left: 2rem; padding-right: 2rem; max-width: 100%; }"
                "</style>"
            )
    else:
        with (
            ui.header()
            .style("justify-content: space-between; background-color: var(--color-bg-surface);")
            .classes("drop-shadow-md")
        ):
            with ui.element("div").style("display: flex; gap: 0px;"):
                ui.image(f"static/{settings.LOGO_TOPBAR}").classes(
                    "q-mr-sm cursor-pointer"
                ).style("height: 30px; width: 30px;").on(
                    "click", lambda: ui.navigate.to("/home")
                )
                ui.label(settings.TOPBAR_TEXT + header_text).classes(
                    "text-h6 cursor-pointer"
                ).on("click", lambda: ui.navigate.to("/home"))

            with ui.element("div").style("display: flex; gap: 0px;"):
                if is_admin:
                    with ui.button(
                        icon="settings",
                        on_click=lambda: ui.navigate.to("/admin"),
                    ).props("flat color=red"):
                        ui.tooltip("Admin settings")

                if is_bofh:
                    with ui.button(
                        icon="health_and_safety",
                        on_click=lambda: ui.navigate.to("/health"),
                    ).props("flat color=red"):
                        ui.tooltip("System status")
                    with ui.button(
                        icon="analytics",
                        on_click=lambda: ui.navigate.to("/admin/analytics"),
                    ).props("flat color=red"):
                        ui.tooltip("Page view statistics")
                with ui.button(
                    icon="home",
                    on_click=lambda: ui.navigate.to("/home"),
                ).props("flat", remove="color"):
                    ui.tooltip("Home")
                with ui.button(
                    icon="person",
                    on_click=lambda: ui.navigate.to("/user"),
                ).props("flat", remove="color"):
                    ui.tooltip("User settings")
                with ui.button(
                    "Feedback",
                    icon="rate_review",
                    on_click=lambda: show_feedback_dialog(),
                ).props("flat", remove="color"):
                    ui.tooltip("Report a problem or suggest a feature")
                with ui.button(
                    icon=_dark_icon(app.storage.user.get("dark_mode", None)),
                ).props("flat", remove="color") as dark_btn:
                    ui.tooltip("Light / dark / auto")
                dark_btn.bind_icon_from(dark, "value", backward=_dark_icon)
                dark_btn.on("click", lambda: cycle_dark(dark_btn))
                with ui.button(
                    icon="help",
                    on_click=lambda: show_help_dialog(),
                ).props("flat", remove="color"):
                    ui.tooltip("Help")
                with ui.button(
                    icon="logout",
                    on_click=lambda: ui.run_javascript("window.top.location.href = '/logout'"),
                ).props("flat", remove="color"):
                    ui.tooltip("Logout")
                ui.add_head_html("<style>body {background-color: var(--color-bg-surface);}</style>")

    _show_announcement_banners()
    return dark


def add_timezone_to_timestamp(timestamp: str) -> str:
    """
    Convert a UTC timestamp to the user's local timezone.
    """
    user_timezone = app.storage.user.get("timezone", "UTC")
    utc_time = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S.%f")
    utc_time = pytz.utc.localize(utc_time)
    local_tz = pytz.timezone(user_timezone)
    local_time = utc_time.astimezone(local_tz)

    return local_time.strftime("%Y-%m-%d %H:%M")


async def jobs_get(*, include_uploads: bool = True) -> list | None:
    """
    Get the list of transcription jobs from the API.
    """
    jobs = []

    try:
        async with httpx.AsyncClient() as client:
            response = await client.request(
                "GET",
                f"{settings.API_URL}/api/v1/transcriber",
                headers=get_auth_header(),
                json={
                    "encryption_password": storage_decrypt(
                        app.storage.user.get("encryption_password"),
                    )
                },
            )
            response.raise_for_status()
    except httpx.HTTPError:
        if not include_uploads:
            return None
        from utils.background_upload import owner_queue
        from utils.upload_state import merge_rows
        return merge_rows([], owner_queue())

    # Get current time in user's timezone
    user_timezone = app.storage.user.get("timezone", "UTC")
    local_tz = pytz.timezone(user_timezone)
    current_time = datetime.now(local_tz)

    for idx, job in enumerate(response.json()["result"]["jobs"]):
        if job["status"] == "in_progress":
            job["status"] = "transcribing"
        elif job["status"] == "pending":
            job["status"] = "queued"

        deletion_date = add_timezone_to_timestamp(job["deletion_date"])
        created_at = add_timezone_to_timestamp(job["created_at"])
        updated_at = add_timezone_to_timestamp(job["updated_at"])

        # Check if deletion is approaching (within 24 hours)
        deletion_approaching = False
        if deletion_date:
            try:
                deletion_dt = datetime.strptime(deletion_date, "%Y-%m-%d %H:%M")
                deletion_dt = local_tz.localize(deletion_dt)
                time_until_deletion = deletion_dt - current_time
                # Default threshold: 24 hours
                deletion_approaching = time_until_deletion <= timedelta(hours=24)
            except (ValueError, AttributeError):
                pass

            deletion_date_display = deletion_date.split(" ")[0]
        else:
            deletion_date_display = "N/A"

        if job["status"] != "completed":
            job_type = ""
        elif job["output_format"] == "txt":
            job_type = "Transcript"
        elif job["output_format"] == "srt":
            job_type = "Subtitles"
        else:
            job_type = "Transcript"

        job_data = {
            "id": idx,
            "uuid": job["uuid"],
            "upload_id": job.get("external_id", ""),
            "filename": job["filename"],
            "created_at": created_at,
            "updated_at": updated_at,
            "deletion_date": deletion_date_display,
            "deletion_approaching": deletion_approaching,
            "language": job["language"].capitalize(),
            "status": job["status"].capitalize(),
            "model_type": job["model_type"].capitalize(),
            "output_format": job["output_format"].upper(),
            "job_type": job_type,
        }

        if job["status"] == "failed":
            reason = job.get("error") or "Upload or transcription failed."
            job_data["upload_error"] = reason if "upload the file again" in reason.lower() else reason + " Upload the file again to start a new job."
        jobs.append(job_data)

    # Sort jobs by created_at in descending order
    jobs.sort(key=lambda x: x["created_at"], reverse=True)

    from utils.background_upload import owner_queue
    from utils.upload_state import merge_rows
    return merge_rows(jobs, owner_queue()) if include_uploads else jobs


def table_click(event) -> None:
    """
    Handle the click event on the table rows.
    """

    status = event.args["status"].lower()
    uuid = event.args["uuid"]
    filename = event.args["filename"]
    model_type = event.args["model_type"]
    language = event.args["language"]
    output_format = event.args.get("output_format")

    if status != "completed":
        return

    if output_format == "TXT":
        ui.navigate.to(
            f"/srt?uuid={uuid}&filename={filename}&model={model_type}&language={language}&data_format=txt"
        )
    else:
        ui.navigate.to(
            f"/srt?uuid={uuid}&filename={filename}&model={model_type}&language={language}&data_format=srt"
        )


def table_upload(table) -> None:
    """The stable outer page owns the uploader across content navigation."""
    ui.run_javascript("window.parent.postMessage('scribe:upload', window.location.origin)")


def _default_transcription_language() -> str:
    """
    Return the language the transcribe dialog should preselect.

    Uses the user's effective default (user override -> realm/customer default
    -> system default) as resolved by the backend and returned from /me, falling
    back to the first option if it is missing or not a selectable language.
    """
    userdata = get_user_data() or {}
    language = userdata.get("default_transcription_language")

    if language in settings.WHISPER_LANGUAGES:
        return language

    # Backend default unavailable (e.g. /me failed): use the configured fallback,
    # guarding against it not being a selectable option.
    # NOTE: keep settings.DEFAULT_TRANSCRIPTION_LANGUAGE in sync with the
    # backend's DEFAULT_TRANSCRIPTION_LANGUAGE.
    if settings.DEFAULT_TRANSCRIPTION_LANGUAGE in settings.WHISPER_LANGUAGES:
        return settings.DEFAULT_TRANSCRIPTION_LANGUAGE

    return settings.WHISPER_LANGUAGES[0]


def table_delete(table: ui.table, *, on_deleted=None) -> None:
    """
    Handle the click event on the Delete button.
    """

    count = len(table.selected)

    with ui.dialog() as dialog:
        with ui.card():
            ui.label("Delete files").classes("text-h6")
            ui.label(
                f"{str(count)} files will be permanently deleted. This action cannot be undone."
            ).classes("text-subtitle2").style("margin-bottom: 10px;")

            with ui.row().classes("justify-between w-full"):
                ui.button("Cancel", on_click=lambda: dialog.close()).props("color=black")
                ui.button(
                    "Delete",
                    on_click=lambda: __delete_files(table, dialog, on_deleted=on_deleted),
                ).props("color=red")

        dialog.open()


async def __delete_files(table: ui.table, dialog: ui.dialog, *, on_deleted=None) -> None:
    selected = list(table.selected)
    total = len(selected)
    dialog.close()

    deleted = 0
    failed = 0
    deleted_ids = set()

    for row in selected:
        uuid = row["uuid"]
        if row.get("local_upload"):
            from utils.background_upload import owner_queue
            uploads = owner_queue()
            uploads[:] = [u for u in uploads if not (u.id == uuid and u.phase == "Failed")]
            deleted_ids.add(uuid)
            if on_deleted is not None:
                on_deleted(uuid)
            deleted += 1
            continue
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.delete(
                    f"{settings.API_URL}/api/v1/transcriber/{uuid}",
                    headers=get_auth_header(),
                )
                response.raise_for_status()
            from utils.background_upload import owner_queue
            uploads = owner_queue()
            uploads[:] = [u for u in uploads if u.backend_id != uuid]
            deleted_ids.add(uuid)
            if on_deleted is not None:
                on_deleted(uuid)
            deleted += 1
        except (httpx.HTTPStatusError, httpx.RequestError):
            failed += 1

    table.selected = []
    # Keep successful deletions removed without replacing the page cache with
    # a separate API snapshot. Failed deletions remain visible.
    table.update_rows([r for r in table.rows if r["uuid"] not in deleted_ids], clear_selection=True)

    if failed == 0:
        ui.notify(
            f"Successfully deleted {deleted} file{'s' if deleted != 1 else ''}",
            type="positive",
            position="top",
        )
    else:
        ui.notify(
            f"Deleted {deleted} of {total} files ({failed} failed)",
            type="warning",
            position="top",
        )


def table_bulk_export(table: ui.table) -> None:
    """
    Handle bulk export of selected completed jobs as a zip file.
    All selected jobs must be of the same type (output_format).
    """

    selected = table.selected
    if not selected:
        ui.notify("No files selected", type="warning", position="top")
        return

    completed = [r for r in selected if r.get("status") == "Completed"]
    if not completed:
        ui.notify("No already completed files selected", type="warning", position="top")
        return

    formats = set(r.get("output_format", "") for r in completed)
    if len(formats) > 1:
        ui.notify(
            "All selected files must be of the same type",
            type="warning",
            position="top",
        )
        return

    source_format = formats.pop()
    data_format = "srt" if source_format == "SRT" else "txt"

    # Show progress dialog while fetching
    with ui.dialog() as progress_dialog:
        with ui.card().classes("p-6 items-center").style(
            "min-width: 400px; background-color: var(--color-bg-surface);"
        ):
            ui.label("Preparing export...").classes("text-h6 mb-2")
            progress_label = ui.label(f"Fetching file 0 of {len(completed)}").classes(
                "text-body2 mb-2"
            )
            progress = ui.linear_progress(value=0, show_value=False).classes("w-full")

    progress_dialog.open()

    async def fetch_and_show():
        from utils.srt import SRTEditor

        editors = []
        for i, row in enumerate(completed):
            uuid = row["uuid"]
            filename = row["filename"]
            progress_label.set_text(
                f"Fetching file {i + 1} of {len(completed)}: {filename}"
            )
            progress.set_value((i + 1) / len(completed))
            try:
                fmt = "srt" if data_format == "srt" else "txt"
                async with httpx.AsyncClient() as client:
                    response = await client.request(
                        "GET",
                        f"{settings.API_URL}/api/v1/transcriber/{uuid}/result/{fmt}",
                        headers=get_auth_header(),
                        json={
                            "encryption_password": storage_decrypt(
                                app.storage.user.get("encryption_password"),
                            )
                        },
                    )
                    response.raise_for_status()
                data = response.json()

                editor = SRTEditor(uuid, data_format, filename)
                if data_format == "srt":
                    editor.parse_srt(data["result"])
                else:
                    editor.parse_txt(data["result"])
                editors.append((filename, editor))
            except httpx.HTTPError as e:
                progress_dialog.close()
                ui.notify(
                    f"Error fetching {filename}: {str(e)}",
                    type="negative",
                    position="top",
                )
                return

        progress_dialog.close()
        # Use the first editor to show the export dialog with all editors
        first_filename, first_editor = editors[0]
        first_editor.show_export_dialog(first_filename, bulk_editors=editors)

    ui.timer(0.1, fetch_and_show, once=True)
