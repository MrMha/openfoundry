import pytest
from app import app, create_app
from fastapi.testclient import TestClient


def test_create_get_list_and_insertion_order() -> None:
    client = TestClient(create_app())
    r1 = client.post("/notes", json={"title": "  First note  "})
    r2 = client.post("/notes", json={"title": "Second note"})
    assert r1.status_code == 201 and r1.json() == {"id": 1, "title": "First note"}
    assert r2.status_code == 201 and r2.json() == {"id": 2, "title": "Second note"}
    assert client.get("/notes").json() == [
        {"id": 1, "title": "First note"},
        {"id": 2, "title": "Second note"},
    ]
    assert client.get("/notes/1").json() == {"id": 1, "title": "First note"}


@pytest.mark.parametrize(
    "payload", [{}, {"title": ""}, {"title": "   "}, {"title": "x" * 81}, {"title": None}]
)
def test_post_rejects_invalid_titles(payload: dict) -> None:
    client = TestClient(create_app())
    assert client.post("/notes", json=payload).status_code == 422


def test_boundary_80_char_title_accepted() -> None:
    client = TestClient(create_app())
    title = "a" * 80
    resp = client.post("/notes", json={"title": title})
    assert resp.status_code == 201 and resp.json()["title"] == title


def test_delete_lifecycle_and_id_monotonicity() -> None:
    client = TestClient(create_app())
    client.post("/notes", json={"title": "A"})
    client.post("/notes", json={"title": "B"})
    d1 = client.delete("/notes/1")
    assert d1.status_code == 204 and d1.content == b""
    assert client.delete("/notes/1").status_code == 404
    assert client.get("/notes/1").status_code == 404
    r3 = client.post("/notes", json={"title": "C"})
    assert r3.status_code == 201 and r3.json()["id"] == 3
    assert [n["id"] for n in client.get("/notes").json()] == [2, 3]


@pytest.mark.parametrize("bad_id", ["0", "-1", "abc"])
def test_non_positive_or_non_int_id_returns_422(bad_id: str) -> None:
    client = TestClient(create_app())
    assert client.get(f"/notes/{bad_id}").status_code == 422
    assert client.delete(f"/notes/{bad_id}").status_code == 422


def test_create_app_factory_isolation_and_module_app() -> None:
    c1, c2, c_mod = TestClient(create_app()), TestClient(create_app()), TestClient(app)
    c1.post("/notes", json={"title": "Only in c1"})
    assert c2.get("/notes").json() == []
    assert c_mod.get("/notes").status_code == 200
