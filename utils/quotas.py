"""Shared quota statistics and BOFH configuration, displayed in hours."""
from decimal import Decimal, InvalidOperation

import httpx
from nicegui import background_tasks, ui
from utils.common import page_init, default_styles
from utils.settings import get_settings
from utils.token import get_admin_status, get_auth_header, get_bofh_status

settings = get_settings()


def hours_to_seconds(value):
    hours = Decimal(str(value))
    if not hours.is_finite() or hours < 0:
        raise ValueError("Enter a non-negative number of hours")
    seconds = hours * 3600
    if seconds != seconds.to_integral_value():
        raise ValueError("The limit must represent a whole number of seconds")
    return int(seconds)


def api_error(response):
    try:
        detail = response.json().get("detail", "Unable to save quota")
        return detail if isinstance(detail, str) else "Check the name, hours, and realm names"
    except ValueError:
        return "Unable to save quota"


def edit_quota(pool, refresh):
    with ui.dialog() as dialog, ui.card().classes("w-full").style("max-width: 650px"):
        ui.label("Edit shared quota" if pool else "Create shared quota").classes("text-h6")
        name = ui.input("Quota name", value=pool["name"] if pool else "").classes("w-full")
        unlimited = ui.checkbox("Unlimited", value=pool is not None and pool["quota_seconds"] is None)
        hours = ui.input("Monthly limit (hours; 0 blocks new submissions)",
                         value=str(Decimal(pool["quota_seconds"]) / 3600) if pool and pool["quota_seconds"] is not None else "0").classes("w-full")
        hours.bind_enabled_from(unlimited, "value", backward=lambda value: not value)
        realms = ui.textarea("Realms (comma-separated)", value=", ".join(pool["realms"]) if pool else "").classes("w-full")
        ui.label("Limit changes apply immediately this month and continue in future months. Existing reservations are honoured.").classes("text-sm")
        ui.label("Membership changes affect future submissions. Remove a realm from its old quota before assigning it here; existing jobs keep their original quota.").classes("text-sm")

        async def save():
            try:
                seconds = None if unlimited.value else hours_to_seconds(hours.value)
            except (InvalidOperation, ValueError) as exc:
                ui.notify(str(exc) if isinstance(exc, ValueError) else "Enter valid hours", type="negative")
                return
            payload = {"name": name.value, "quota_seconds": seconds,
                       "realms": [v.strip() for v in realms.value.split(",") if v.strip()]}
            save_button.disable()
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.request(
                        "PUT" if pool else "POST",
                        f"{settings.API_URL}/api/v1/admin/quotas" + (f"/{pool['id']}" if pool else ""),
                        json=payload, headers=get_auth_header(),
                    )
                if response.is_error:
                    ui.notify(api_error(response), type="negative")
                    return
                dialog.close()
                await refresh()
                ui.notify("Quota saved", type="positive")
            except httpx.HTTPError:
                ui.notify("Unable to reach the quota service", type="negative")
            finally:
                save_button.enable()

        with ui.row():
            ui.button("Cancel", on_click=dialog.close).props("flat")
            save_button = ui.button("Save", on_click=save)
    dialog.open()


def quota_statistics(manage=False):
    """Can be embedded in existing admin statistics as well as the quota page."""
    with ui.column().classes("w-full") as overview:
        ui.label("Shared monthly transcription quotas").classes("text-h5")
        ui.label("UTC calendar month · shared across the listed realms · REACH excluded").classes("text-sm")

        @ui.refreshable
        async def contents():
            container = ui.context.slot.parent
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.get(f"{settings.API_URL}/api/v1/admin/quotas", headers=get_auth_header())
                    response.raise_for_status()
                pools = response.json()["result"]
            except (httpx.HTTPError, ValueError, KeyError):
                if container.is_deleted:
                    return
                ui.label("Unable to load quota usage. Use Refresh to try again.").classes("text-negative")
                return
            if container.is_deleted:
                return
            if not pools:
                ui.label("No shared quotas assigned.")
            for pool in pools:
                with ui.card().classes("w-full"):
                    with ui.row().classes("w-full justify-between"):
                        ui.label(pool["name"]).classes("text-h6")
                        if manage and get_bofh_status():
                            ui.button("Edit", on_click=lambda p=pool: edit_quota(p, contents.refresh)).props("flat")
                    ui.label("Realms: " + (", ".join(pool["realms"]) or "None (existing jobs retain this quota)"))
                    limit = pool["quota_seconds"]
                    ui.label("Monthly limit: " + ("Unlimited" if limit is None else f"{limit / 3600:,.3f} hours"))
                    with ui.row().classes("gap-8"):
                        ui.label(f"Completed: {pool['used_seconds'] / 3600:,.3f} hours")
                        ui.label(f"Queued / in progress: {pool['reserved_seconds'] / 3600:,.3f} hours")
                        if limit is None:
                            ui.label("Remaining: Unlimited")
                        else:
                            hours, minutes = divmod(int(pool['remaining_seconds']) // 60, 60)
                            ui.label(f"Remaining: {hours}h {minutes:02d}m")
                    if limit is not None:
                        total = pool["used_seconds"] + pool["reserved_seconds"]
                        ratio = total / limit if limit else 1
                        with ui.linear_progress(value=min(ratio, 1), show_value=False, size="20px",
                                                color="negative" if ratio >= 1 else "primary").classes("w-full") as progress:
                            ui.label().classes("absolute-center text-sm text-white").bind_text_from(
                                progress, "value", backward=lambda value: f"{value:.0%}")
                        if total >= limit:
                            ui.label("No capacity for additional reservations.").classes("text-negative")
                    ui.label(f"Month: {pool['period_start'][:7]} · Resets: {pool['resets_at'][:10]} 00:00 UTC").classes("text-sm")

        with ui.row():
            ui.button("Refresh", on_click=contents.refresh).props("flat")
            if manage and get_bofh_status():
                ui.button("Create quota", on_click=lambda: edit_quota(None, contents.refresh))
        async def load_initial():
            # A surrounding refresh or navigation can delete this view before loading starts.
            if not overview.is_deleted:
                with overview:
                    await contents()

        background_tasks.create(load_initial(), name="load quota overview")


@ui.page("/admin/quotas")
def quotas_page():
    page_init(use_drawer=True)
    if not (get_admin_status() or get_bofh_status()):
        ui.navigate.to("/home")
        return
    ui.add_head_html(default_styles)
    quota_statistics(manage=True)
