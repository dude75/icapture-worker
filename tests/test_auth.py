from fastapi.testclient import TestClient

from tests.conftest import auth_headers, metrics_headers


def test_capture_requires_api_token(client: TestClient) -> None:
    response = client.post(
        "/capture",
        json={"connector": "jitsi", "meeting_url": "https://meet.example.com/Room"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_tasks_requires_api_token(client: TestClient) -> None:
    response = client.get("/tasks")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_metrics_requires_metrics_token(client: TestClient) -> None:
    response = client.get("/metrics")
    assert response.status_code == 401


def test_metrics_accepts_metrics_token(client: TestClient) -> None:
    response = client.get("/metrics", headers=metrics_headers())
    assert response.status_code == 200
    assert "icapture_up" in response.text


def test_health_is_public(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["connectors"]["jitsi"] == {
        "status": "loaded",
        "label": "Jitsi Meet",
    }
    assert "reason" not in body["connectors"]["jitsi"]
    assert body["connectors"]["zoom"]["status"] == "unavailable"
    assert body["connectors"]["zoom"]["reason"] in {"not_implemented", "disabled"}
    assert body["workers"] == {"max": 2, "active": 0, "available": 2}


def test_tasks_probe_with_api_token(client: TestClient) -> None:
    response = client.get("/tasks", headers=auth_headers())
    assert response.status_code == 200
    assert response.json() == []


def test_health_workers_reflect_pending_capture(client: TestClient) -> None:
    response = client.post(
        "/capture",
        headers=auth_headers(),
        json={"connector": "jitsi", "meeting_url": "https://meet.example.com/Room"},
    )
    assert response.status_code == 202
    health = client.get("/health").json()
    assert health["workers"] == {"max": 2, "active": 1, "available": 1}
