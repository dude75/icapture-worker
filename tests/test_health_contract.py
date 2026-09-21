"""GET /health shape and workers pool (idigest-hub contract)."""

from fastapi.testclient import TestClient

from tests.conftest import auth_headers, isolate_env, start_capture


def test_health_has_no_slots_field(client: TestClient) -> None:
    body = client.get("/health").json()
    assert "slots" not in body
    assert "workers" in body


def test_health_workers_invariants(client: TestClient) -> None:
    body = client.get("/health").json()
    workers = body["workers"]
    assert workers["max"] >= workers["active"] >= 0
    assert workers["available"] >= 0
    assert workers["active"] + workers["available"] == workers["max"]


def test_health_connectors_catalog_keys(client: TestClient) -> None:
    connectors = client.get("/health").json()["connectors"]
    assert set(connectors) >= {"jitsi", "zoom", "meet"}
    for item in connectors.values():
        assert item["status"] in {"loaded", "unavailable"}
        assert item["label"]


def test_health_at_capacity_and_queue_full(tmp_path, monkeypatch) -> None:
    isolate_env(tmp_path, monkeypatch, workers="2", worker_queue_size="0")
    from app.main import app

    with TestClient(app) as client:
        start_capture(client, url="https://meet.example.com/RoomA")
        start_capture(client, url="https://meet.example.com/RoomB")
        workers = client.get("/health").json()["workers"]
        assert workers == {"max": 2, "active": 2, "available": 0}

        third = client.post(
            "/capture",
            headers=auth_headers(),
            json={"connector": "jitsi", "meeting_url": "https://meet.example.com/RoomC"},
        )
        assert third.status_code == 503
        assert third.json()["error"]["code"] == "queue_full"


def test_health_workers_released_after_cancel(client: TestClient) -> None:
    task_id = start_capture(client)
    assert client.get("/health").json()["workers"]["active"] == 1
    client.delete(f"/tasks/{task_id}", headers=auth_headers())
    assert client.get("/health").json()["workers"] == {
        "max": 2,
        "active": 0,
        "available": 2,
    }
