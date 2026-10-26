"""The built client is served at / with a fallback for client routes (src/core/frontend.py)."""

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from src.core.config import Settings, get_settings
from src.main import create_app

INDEX = "<!doctype html><div id=app></div>"


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(INDEX)
    (tmp_path / "assets" / "index-Bex2cwN4.js").write_text("console.log('app')")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    (tmp_path.parent / "secret.txt").write_text("outside of dist")
    return tmp_path


async def client_for(dist_dir: Path) -> AsyncClient:
    settings = Settings(**{**get_settings().model_dump(), "FRONTEND_DIST_DIR": str(dist_dir)})
    return AsyncClient(transport=ASGITransport(app=create_app(settings)), base_url="http://test")


@pytest.mark.parametrize("path", ["/", "/documents", "/forum/posts/0b9f2c1e", "/readings/"])
async def test_client_routes_load_the_client_without_caching(dist: Path, path: str) -> None:
    async with await client_for(dist) as client:
        response = await client.get(path)

    assert response.status_code == 200
    assert response.text == INDEX
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"


async def test_hashed_assets_are_cached_for_a_year_and_root_files_are_served(dist: Path) -> None:
    async with await client_for(dist) as client:
        script = await client.get("/assets/index-Bex2cwN4.js")
        icon = await client.get("/favicon.svg")
        head = await client.head("/documents")

    assert script.status_code == 200 and script.text == "console.log('app')"
    assert script.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert icon.status_code == 200 and icon.text == "<svg/>"
    assert head.status_code == 200


@pytest.mark.parametrize(
    "path",
    [
        "/assets/index-OLDHASH.js",  # a stale tab must not get HTML instead of a script
        "/missing.png",
        "/..%2Fsecret.txt",
        "/%2e%2e/secret.txt",
    ],
)
async def test_missing_files_are_a_404_never_the_client(dist: Path, path: str) -> None:
    async with await client_for(dist) as client:
        response = await client.get(path)

    assert response.status_code == 404
    assert "outside of dist" not in response.text
    assert response.text != INDEX


async def test_api_paths_and_other_methods_keep_json_errors(dist: Path) -> None:
    async with await client_for(dist) as client:
        unknown_api = await client.get("/api/v1/unknown")
        post = await client.post("/documents")
        health = await client.get("/health")
        docs = await client.get("/docs")

    assert unknown_api.status_code == 404
    assert unknown_api.json()["error_code"] == "NOT_FOUND"
    assert post.status_code == 404 and post.json()["error_code"] == "NOT_FOUND"
    assert health.status_code == 200 and health.json()["status"] == "ok"
    assert docs.status_code == 200 and "swagger" in docs.text.lower()


async def test_without_a_build_the_backend_is_api_only(tmp_path: Path) -> None:
    async with await client_for(tmp_path / "no-dist") as client:
        response = await client.get("/")

    assert response.status_code == 404
    assert response.json()["error_code"] == "NOT_FOUND"
