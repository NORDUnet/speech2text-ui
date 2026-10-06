"""Exercise index startup without importing main.py and starting the server."""
import ast
import secrets
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Request


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["connection_deleted", "timezone_deleted", "timeout_deleted", "connected", "timeout"])
async def test_index_stops_when_client_is_deleted(stage):
    tree = ast.parse((Path(__file__).parents[1] / "main.py").read_text())
    index = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "index")
    index.decorator_list = []
    content = SimpleNamespace(is_deleted=False)

    async def connected():
        if stage == "connection_deleted":
            content.is_deleted = True

    async def timezone(*args, **kwargs):
        assert not content.is_deleted, "JavaScript must not be sent to a deleted client"
        if stage in {"timezone_deleted", "timeout_deleted"}:
            content.is_deleted = True
        if stage in {"timeout_deleted", "timeout"}:
            raise TimeoutError
        return "Europe/Copenhagen"

    class RenderStarted(Exception):
        """Stop before rendering the rest of the login page."""

    storage = SimpleNamespace(browser={"_scribe_bk": "existing"}, user={})
    ui = SimpleNamespace(
        context=SimpleNamespace(client=SimpleNamespace(content=content, connected=connected)),
        add_head_html=Mock(), run_javascript=AsyncMock(side_effect=timezone),
        dark_mode=Mock(side_effect=RenderStarted),
    )
    scope = {"Request": Request, "ui": ui, "app": SimpleNamespace(storage=storage),
             "default_styles": "", "secrets": secrets}
    exec(compile(ast.Module(body=[index], type_ignores=[]), "main.py", "exec"), scope)
    request = SimpleNamespace(query_params={"token": "access-token", "refresh_token": "refresh-token"})

    if stage in {"connected", "timeout"}:
        with pytest.raises(RenderStarted):
            await scope["index"](request)
        assert storage.user["timezone"] == ("UTC" if stage == "timeout" else "Europe/Copenhagen")
        ui.dark_mode.assert_called_once()
    else:
        await scope["index"](request)
        assert "timezone" not in storage.user
        ui.dark_mode.assert_not_called()

    if stage == "connection_deleted":
        ui.run_javascript.assert_not_called()
        assert storage.user == {}
    else:
        ui.run_javascript.assert_awaited_once()
        assert storage.user["token"] == "access-token"
        assert storage.user["refresh_token"] == "refresh-token"
