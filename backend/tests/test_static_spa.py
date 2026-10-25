"""Production assets never absorb API, authentication, or WebSocket routes."""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.websockets import WebSocketDisconnect

from backend.app import main


@pytest.fixture
def dist(tmp_path, monkeypatch):
    root = tmp_path / "frontend-dist"
    root.mkdir()
    (root / "index.html").write_text('<!doctype html><title>War Room</title><div id="root"></div>')
    (root / "assets").mkdir()
    (root / "assets/app-hashed123.js").write_text('console.log("war room");')
    monkeypatch.setattr(main, "FRONTEND_DIST", root)
    return root


def test_root_assets_head_and_html_navigation_work_without_auth_but_apis_stay_protected(
    settings, dist
):
    config = settings.model_copy(update={"api_token": SecretStr("static-operator")})
    with TestClient(main.create_app(config)) as client:
        response = client.get("/")
        assert response.status_code == 200 and "War Room" in response.text
        assert response.headers["cache-control"] == "no-cache"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert client.head("/").status_code == 200
        asset = client.get("/assets/app-hashed123.js")
        assert asset.status_code == 200 and "javascript" in asset.headers["content-type"]
        assert (
            client.get("/missions/a/deep/link", headers={"Accept": "text/html"}).text
            == response.text
        )
        assert client.get("/api/plugins").status_code == 401
        assert client.post("/api/missions/demo").status_code == 401
        assert (
            client.get(
                "/api/runtime", headers={"Authorization": "Bearer static-operator"}
            ).status_code
            == 200
        )
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/openapi.json").json()["paths"]["/api/missions/demo"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/does-not-exist",
        "/ws/does-not-exist",
        "/api",
        "/ws",
        "/assets/missing.js",
        "/assets/missing",
        "/missing.png",
        "/.env",
        "/.git/config",
        "/health/missing",
        "/api%5Cdoes-not-exist",
        "/ws%5Cdoes-not-exist",
        "/assets%5Cmissing",
        "/health%5Cmissing",
    ],
)
def test_reserved_missing_asset_and_hidden_paths_are_never_spa_navigation(settings, dist, path):
    with TestClient(main.create_app(settings)) as client:
        response = client.get(path, headers={"Accept": "text/html"})
        assert response.status_code == 404
        assert "War Room" not in response.text


def test_json_navigation_unknown_websockets_and_posts_do_not_receive_spa(settings, dist):
    with TestClient(main.create_app(settings)) as client:
        assert (
            client.get("/client-route", headers={"Accept": "application/json"}).status_code == 404
        )
        assert client.post("/client-route").status_code == 405
        with pytest.raises(WebSocketDisconnect) as error:
            with client.websocket_connect("/ws/not-a-route"):
                pass
        assert error.value.code == 1008


def test_static_containment_rejects_symlinked_files_and_traversal(settings, dist, tmp_path):
    private = tmp_path / "private.txt"
    private.write_text("outside-dist-secret")
    (dist / "leak.txt").symlink_to(private)
    with TestClient(main.create_app(settings)) as client:
        for path in ("/leak.txt", "/%2e%2e/private.txt", "/assets/%2e%2e/%2e%2e/private.txt"):
            response = client.get(path)
            assert response.status_code == 404
            assert "outside-dist-secret" not in response.text


def test_api_only_startup_when_dist_is_missing_or_incomplete(settings, tmp_path, monkeypatch):
    missing = tmp_path / "missing-dist"
    monkeypatch.setattr(main, "FRONTEND_DIST", missing)
    with TestClient(main.create_app(settings)) as client:
        assert client.get("/").status_code == 404
        assert client.get("/health").status_code == 200
    missing.mkdir()
    with TestClient(main.create_app(settings)) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/plugins").status_code == 200
