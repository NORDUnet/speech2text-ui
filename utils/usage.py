"""Best-effort UI counters. Only fixed categories and counts leave the UI."""
from collections import Counter
import httpx
from nicegui import ui
from utils.settings import get_settings
from utils.token import get_auth_header


def record(metric, count=1):
    # Local import of neither user data nor filenames. The request still uses
    # normal authentication, but that identity is never part of the payload.
    try:
        client = ui.context.client
        if not hasattr(client, "_usage_counts"):
            client._usage_counts = Counter()
            async def flush():
                counts = dict(client._usage_counts)
                client._usage_counts.clear()
                if not counts:
                    return
                try:
                    async with httpx.AsyncClient(timeout=3) as http:
                        await http.post(get_settings().API_URL + "/api/v1/analytics/usage",
                                        headers=get_auth_header(), json={"counters": counts})
                except Exception:
                    pass  # Never block editing or log payloads/authentication.
            with client:
                ui.timer(30, flush)
        client._usage_counts[metric] = min(10000, client._usage_counts[metric] + count)
    except Exception:
        pass


def render_statistics():
    """Mounted only inside the existing BOFH page; backend also enforces BOFH."""
    from utils.usage_labels import SECTIONS, SECTION_HELP
    with ui.card().classes("w-full no-shadow rounded-xl border p-5 gap-4"):
        ui.label("Feature usage").classes("text-xl font-medium")
        ui.label("Anonymous totals for completed weeks. Counts below five are hidden. "
                 "Collection starts with this release; counters are approximate.").classes("text-sm opacity-70")
        with ui.row().classes("items-center gap-3"):
            period = ui.select({1: "Last complete week", 4: "Last 4 complete weeks", 12: "Last 12 complete weeks"}, value=4).props("dense outlined")
            refresh = ui.button("Refresh", icon="refresh").props("flat no-caps", remove="color")
        results = ui.column().classes("w-full gap-4")
        async def load():
            refresh.disable()
            try:
                async with httpx.AsyncClient(timeout=10) as http:
                    response = await http.get(get_settings().API_URL + "/api/v1/admin/analytics/usage",
                        headers=get_auth_header(), params={"weeks": period.value})
                    response.raise_for_status()
                    data = response.json()["result"]
                results.clear()
                with results:
                    ui.label(f'{data["start"]} to {data["end"]} (end exclusive, UTC)').classes("text-xs opacity-60")
                    with ui.element("div").classes("w-full").style("display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:16px"):
                        for title, metrics in SECTIONS:
                            with ui.card().classes("w-full no-shadow border rounded-lg p-4 gap-2"):
                                with ui.row().classes("items-center gap-2"):
                                    ui.label(title).classes("font-medium")
                                    if title in SECTION_HELP:
                                        with ui.icon("info_outline", size="16px").classes("opacity-60 cursor-help").props('tabindex=0 aria-label="About these numbers"'):
                                            ui.tooltip(SECTION_HELP[title]).style("max-width: 280px; white-space: normal;")
                                for metric, label in metrics:
                                    with ui.row().classes("w-full justify-between items-center no-wrap"):
                                        ui.label(label).classes("text-sm")
                                        ui.label(str(data["counters"].get(metric, 0))).classes("text-sm font-medium")
                    ui.separator().classes("my-2")
                    ui.label("Groups and provisioning — now").classes("font-medium")
                    ui.label("Current totals across the service, regardless of the period selected above.").classes("text-sm opacity-70")
                    with ui.element("div").classes("w-full").style("display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:16px"):
                        for key, label, explanation in (
                            ("Groups currently created", "Total groups", "How many groups currently exist."),
                            ("Groups with transcription limits", "Groups with a monthly limit", "Groups with a limit on how much audio their members can transcribe each month."),
                            ("Enabled provisioning rules", "Enabled user provisioning rules", "Rules currently switched on to automatically manage user access or group membership."),
                        ):
                            with ui.card().classes("w-full no-shadow border rounded-lg p-4 gap-2"):
                                with ui.row().classes("items-center gap-2"):
                                    ui.label(label).classes("text-sm")
                                    with ui.icon("info_outline", size="16px").classes("opacity-60 cursor-help").props('tabindex=0 aria-label="About this number"'):
                                        ui.tooltip(explanation).style("max-width: 280px; white-space: normal;")
                                ui.label(str(data["current"].get(key, 0))).classes("text-xl font-medium")
            except Exception:
                results.clear()
                with results:
                    ui.label("Usage statistics are unavailable. Try Refresh.").classes("text-sm")
            finally:
                refresh.enable()
        refresh.on_click(load)
        period.on_value_change(load)
        ui.timer(0.1, load, once=True)
