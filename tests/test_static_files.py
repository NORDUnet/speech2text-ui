"""Static files are served without Starlette's disconnect watcher."""
import asyncio

from utils.static_files import StaticFilesWithoutDisconnectWatch


def served_scope(path, scope_type="http"):
    seen = {}

    async def app(scope, receive, send):
        seen.update(scope)

    scope = {"type": scope_type, "path": path, "asgi": {"version": "3.0", "spec_version": "2.3"}}
    asyncio.run(StaticFilesWithoutDisconnectWatch(app)(scope, None, None))
    return seen


def test_static_files_declare_spec_2_4():
    for path in ("/static/files-table.css", "/_nicegui/4.0.0/components/abc/table.js"):
        assert served_scope(path)["asgi"] == {"version": "3.0", "spec_version": "2.4"}


def test_pages_and_websockets_are_unchanged():
    assert served_scope("/home")["asgi"]["spec_version"] == "2.3"
    assert served_scope("/_nicegui_ws/socket.io/")["asgi"]["spec_version"] == "2.3"
    assert served_scope("/static/x.css", "websocket")["asgi"]["spec_version"] == "2.3"
