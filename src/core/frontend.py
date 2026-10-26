"""Serves the built client (frontend/dist) from the backend, with a fallback for client routes.

Only active when the image contains a build (dist/index.html); otherwise the backend behaves as
an API only. The client is plain static files with relative API URLs, so it can later move to
its own server without changes (docs/FRONTEND_GIT_WORKFLOW.md).

    /assets/*               hashed build files, cached for a year
    /<file>.<ext>           other files at the root of dist (favicon, robots.txt), or 404
    any other GET / HEAD    index.html (no-cache), so deep links like /documents load the client
    API and docs paths      never served by the client: an unknown /api path stays a JSON 404
"""

from pathlib import Path

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.core.exceptions import http_error_handler

logger = structlog.get_logger(__name__)

# First path segments that belong to the backend.
RESERVED_SEGMENTS = frozenset({"api", "health", "docs", "redoc", "openapi.json", "assets"})

IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"


class _HashedAssets(StaticFiles):
    """Vite puts a content hash into every file name under assets/, so they never change."""

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = IMMUTABLE
        return response


def mount_frontend(app: FastAPI, dist_dir: str | Path) -> bool:
    """Serve the client from dist_dir. Returns False (and changes nothing) without a build."""
    dist = Path(dist_dir).resolve()
    index = dist / "index.html"
    if not index.is_file():
        logger.info("frontend_disabled", reason="no build", dist_dir=str(dist))
        return False

    app.mount("/assets", _HashedAssets(directory=dist / "assets", check_dir=False), name="assets")

    async def client_or_not_found(request: Request, exc: StarletteHTTPException) -> Response:
        path = request.url.path.lstrip("/")
        if (
            exc.status_code != 404
            or request.method not in ("GET", "HEAD")
            or path.split("/", 1)[0] in RESERVED_SEGMENTS
        ):
            return await http_error_handler(request, exc)
        if "." in path.rsplit("/", 1)[-1]:
            # A file: serve it if dist has it. A missing file is a 404, never index.html, or a
            # stale tab would get HTML instead of the script it asked for.
            candidate = (dist / path).resolve()
            if candidate.is_relative_to(dist) and candidate.is_file():
                return FileResponse(candidate, headers={"Cache-Control": NO_CACHE})
            return await http_error_handler(request, exc)
        return FileResponse(index, headers={"Cache-Control": NO_CACHE})

    # Unmatched routes end as a 404; for client routes that 404 becomes the client.
    app.add_exception_handler(StarletteHTTPException, client_or_not_found)
    logger.info("frontend_enabled", dist_dir=str(dist))
    return True
