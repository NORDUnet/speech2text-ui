from types import SimpleNamespace

import httpx
import pytest
from nicegui import ui
from nicegui.testing.user_simulation import user_simulation
from utils import quotas


@pytest.mark.asyncio
@pytest.mark.parametrize("bofh", [False, True])
async def test_quota_dashboard_permissions_and_editing(monkeypatch, bofh):
    pool = {"id": 1, "name": "Shared", "realms": ["a.example", "b.example"],
            "quota_seconds": 7200, "used_seconds": 3600, "reserved_seconds": 900,
            "remaining_seconds": 2700, "period_start": "2026-09-01T00:00:00Z", "resets_at": "2026-10-01T00:00:00Z"}
    writes = []

    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url, **kwargs):
            return httpx.Response(200, json={"result": [pool]}, request=httpx.Request("GET", "http://test/quotas"))
        async def request(self, method, url, **kwargs):
            writes.append(kwargs["json"])
            return httpx.Response(200, json={"result": {"id": 1}})

    monkeypatch.setattr(quotas, "httpx", SimpleNamespace(AsyncClient=Client, HTTPError=httpx.HTTPError))
    monkeypatch.setattr(quotas, "get_auth_header", lambda: {})
    monkeypatch.setattr(quotas, "get_bofh_status", lambda: bofh)
    async with user_simulation() as user:
        @ui.page("/test-quotas")
        def test_page():
            quotas.quota_statistics(manage=True)
        await user.open("/test-quotas")
        await user.should_see("Completed: 1.000 hours")
        await user.should_see("Queued / in progress: 0.250 hours")
        await user.should_see("Remaining: 0.750 hours")
        if not bofh:
            await user.should_not_see("Edit")
            await user.should_not_see("Create quota")
        else:
            user.find("Edit").click()
            await user.should_see("Edit shared quota")
            user.find("Save").click()
            await user.should_see("Quota saved")
            assert writes == [{"name": "Shared", "quota_seconds": 7200, "realms": ["a.example", "b.example"]}]
