from fastapi.testclient import TestClient

from tests.conftest import auth_headers, metrics_headers


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
    assert body["connectors"]["zoom"] == {
        "status": "unavailable",
        "label": "Zoom",
        "reason": "not_implemented",
    }
    assert body["slots"] == {"max": 2, "active": 0, "available": 2}


def test_tasks_probe_with_api_token(client: TestClient) -> None:
    response = client.get("/tasks", headers=auth_headers())
    assert response.status_code == 200
    assert response.json() == []
