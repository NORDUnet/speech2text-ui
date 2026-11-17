"""Serve static files without Starlette's disconnect watching."""
from starlette.types import ASGIApp, Receive, Scope, Send

STATIC_PREFIXES = ("/_nicegui/", "/static/")


class StaticFilesWithoutDisconnectWatch:
    """Let FileResponse send static files the way Starlette 0.x did.

    Starlette 1.x FileResponse watches for the client disconnecting when the
    server speaks ASGI spec < 2.4, which uvicorn does. When the browser drops a
    request, e.g. while the workspace iframe reloads, the watcher cancels the
    response after it has started, NiceGUI's BaseHTTPMiddlewares see no
    response, and every dropped file is logged as "No response returned".
    Declaring spec 2.4 for static files makes FileResponse send the file without
    the watcher; uvicorn drops what a disconnected client no longer reads.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"].startswith(STATIC_PREFIXES):
            scope = {**scope, "asgi": {**scope.get("asgi", {}), "spec_version": "2.4"}}
        await self.app(scope, receive, send)
