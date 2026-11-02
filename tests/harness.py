"""The gateway's route registry, reduced to what these tests need.

The gateway serves an app's ``hooks.routes`` records under
``/api/apps/<app>/`` through ONE catch-all route: an exact path first, then a
``{param}`` pattern whose parameters match one segment each, and it calls
``handler(request, ctx)``. :func:`mount` does the same on a test application,
so the route tests drive the records :func:`backend.routes.register_routes`
really returns, with the same matching rules (a ``{tail:.*}`` is not one).
An app handler that raises becomes the registry's 502, as in the gateway.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from aiohttp import web

from backend import _host, routes

_PARAM_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _compile(path: str) -> re.Pattern[str]:
    parts: list[str] = []
    last = 0
    for match in _PARAM_RE.finditer(path):
        parts.append(re.escape(path[last : match.start()]))
        parts.append(f"(?P<{match.group(1)}>[^/]+)")
        last = match.end()
    parts.append(re.escape(path[last:]))
    return re.compile("^" + "".join(parts) + "$")


def mount(app: web.Application, ctx: Any | None = None) -> list[Any]:
    """Register the app's records on *app* behind a registry-shaped catch-all."""
    ctx = ctx if ctx is not None else _host.bound()
    records = routes.register_routes(ctx)
    table = [(r.method.upper(), r.path, _compile(r.path), r.handler) for r in records]

    async def dispatch(request: web.Request) -> web.StreamResponse:
        path = "/" + request.match_info.get("path", "")
        candidates = [t for t in table if t[0] == request.method]
        chosen = None
        for method, raw, _pattern, handler in candidates:
            if raw == path:
                chosen = (handler, {})
                break
        if chosen is None:
            for method, raw, pattern, handler in candidates:
                m = pattern.match(path)
                if m:
                    chosen = (handler, m.groupdict())
                    break
        if chosen is None:
            return web.json_response({"error": "not found", "code": "not_found"}, status=404)
        handler, params = chosen
        for name, value in params.items():
            request.match_info[name] = value
        try:
            return await handler(request, ctx)
        except (web.HTTPException, asyncio.CancelledError):
            raise
        except Exception:
            return web.json_response(
                {"error": "app route failed", "code": "app_route_failed"}, status=502
            )

    app.router.add_route("*", "/api/apps/pptx-maker/{path:.*}", dispatch)
    return records
