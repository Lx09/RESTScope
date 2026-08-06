"""Serve the built observer interface and its read-only event stream.

This Module adapts :class:`restscope.observability.LiveRunObserver` to three
loopback GET routes: a complete snapshot, cursor-based Server-Sent Events, and
the versioned frontend assets. Starlette and Uvicorn stay optional and are
imported only when UI hosting is explicitly enabled.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

from pathlib import Path
from threading import RLock, Thread
from typing import Any


LOGGER = logging.getLogger(__name__)
STATIC_ROOT = Path(__file__).resolve().parent / "static"
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class _SecurityHeadersMiddleware:
    """Append viewer security headers without buffering an SSE response body."""

    def __init__(self, app: Any) -> None:
        """Wrap one ASGI application supplied by Starlette."""
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """Add headers to HTTP response starts and pass all other messages through."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                existing = list(message.get("headers", []))
                blocked = {name.lower().encode("latin-1") for name in _SECURITY_HEADERS}
                kept = [(name, value) for name, value in existing if name.lower() not in blocked]
                kept.extend(
                    (name.encode("latin-1"), value.encode("latin-1"))
                    for name, value in _SECURITY_HEADERS.items()
                )
                message = {**message, "headers": kept}
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


class UIService:
    """Own one background Uvicorn thread and expose its loopback URL."""

    def __init__(self, *, observer: Any, port: int, static_root: Path = STATIC_ROOT) -> None:
        """Store dependencies without opening a socket until :meth:`start`."""
        self.observer = observer
        self.port = port
        self.static_root = static_root
        self.url = f"http://127.0.0.1:{port}"
        self._server: Any | None = None
        self._thread: Thread | None = None
        self._lock = RLock()

    def start(self, *, timeout_seconds: float = 3.0) -> bool:
        """Start Uvicorn and report readiness without raising into App startup."""
        try:
            import uvicorn

            app = build_ui_app(self.observer, static_root=self.static_root)
            server = uvicorn.Server(
                uvicorn.Config(
                    app,
                    host="127.0.0.1",
                    port=self.port,
                    log_level="warning",
                    access_log=False,
                )
            )
        except Exception as exc:
            LOGGER.warning("RESTScope UI initialization failed: %s", type(exc).__name__)
            return False

        with self._lock:
            self._server = server
            self._thread = Thread(
                target=self._run_server,
                name="restscope-live-ui",
                daemon=True,
            )
            self._thread.start()

        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            if bool(getattr(server, "started", False)):
                return True
            thread = self._thread
            if thread is None or not thread.is_alive():
                break
            time.sleep(0.01)
        self.close()
        LOGGER.warning("RESTScope UI could not bind loopback port %s", self.port)
        return False

    def close(self) -> None:
        """Request bounded server shutdown and release the owned thread."""
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is not None:
            server.should_exit = True
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)

    def _run_server(self) -> None:
        """Contain Uvicorn exits and startup exceptions inside its daemon thread."""
        try:
            assert self._server is not None
            self._server.run()
        except BaseException as exc:
            LOGGER.warning("RESTScope UI server stopped: %s", type(exc).__name__)


def start_ui_service(*, observer: Any, port: int) -> UIService | None:
    """Build and start one service, returning ``None`` on any optional failure."""
    service = UIService(observer=observer, port=port)
    return service if service.start() else None


def build_ui_app(observer: Any, *, static_root: Path = STATIC_ROOT) -> Any:
    """Create the read-only Starlette adapter used by production and tests.

    Args:
        observer: Deep current-run Module supplying snapshots and cursor waits.
        static_root: Directory containing committed Vite build output.

    Returns:
        A Starlette application with GET-only snapshot, SSE, and static routes.

    Raises:
        ImportError: The optional ``ui`` dependency group is not installed.
        RuntimeError: The committed frontend assets are missing.
    """
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import FileResponse, JSONResponse, StreamingResponse
    from starlette.routing import Mount, Route
    from starlette.staticfiles import StaticFiles
    from starlette.middleware import Middleware

    index_file = static_root / "index.html"
    assets_dir = static_root / "assets"
    if not index_file.is_file() or not assets_dir.is_dir():
        raise RuntimeError("RESTScope UI build assets are missing")

    async def snapshot(_request: Request) -> JSONResponse:
        return JSONResponse(observer.snapshot())

    async def events(request: Request) -> StreamingResponse:
        cursor = _read_cursor(request)

        async def generate():
            nonlocal cursor
            while True:
                if await request.is_disconnected():
                    return
                changes = await asyncio.to_thread(observer.wait_after, cursor, 15.0)
                if not changes:
                    yield ": heartbeat\n\n"
                    continue
                for change in changes:
                    cursor = int(change["cursor"])
                    payload = json.dumps(
                        change["data"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    yield (
                        f"id: {cursor}\n"
                        f"event: {change['type']}\n"
                        f"data: {payload}\n\n"
                    )

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    async def index(_request: Request) -> FileResponse:
        return FileResponse(index_file, media_type="text/html")

    app = Starlette(
        routes=[
            Route("/api/v1/run", snapshot, methods=["GET"]),
            Route("/api/v1/events", events, methods=["GET"]),
            Mount("/assets", StaticFiles(directory=assets_dir), name="assets"),
            Route("/", index, methods=["GET"]),
        ],
        middleware=[Middleware(_SecurityHeadersMiddleware)],
    )

    return app


def _read_cursor(request: Any) -> int:
    """Use the newest valid snapshot or standard SSE reconnect cursor."""
    values = (
        request.query_params.get("after"),
        request.headers.get("last-event-id"),
    )
    parsed: list[int] = []
    for raw in values:
        try:
            parsed.append(max(0, int(raw)))
        except (TypeError, ValueError):
            continue
    return max(parsed, default=0)
