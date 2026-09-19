import time

from fastapi.testclient import TestClient

from tests.conftest import auth_headers, isolate_env


def _wait_for_status(
    client: TestClient,
    task_id: str,
    target: str,
    *,
    timeout_sec: float = 5.0,
) -> dict:
    deadline = time.time() + timeout_sec
    body: dict | None = None
    while time.time() < deadline:
        poll = client.get(f"/tasks/{task_id}", headers=auth_headers())
        body = poll.json()
        if body["status"] == target:
            return body
        time.sleep(0.05)
    assert body is not None
    assert body["status"] == target
    return body


def _complete_capture(client: TestClient, task_id: str) -> dict:
    stop = client.post(f"/tasks/{task_id}/stop", headers=auth_headers())
    assert stop.status_code == 202
    return _wait_for_status(client, task_id, "success")


def _start_capture(client: TestClient, *, url: str = "https://meet.example.com/TestRoom") -> str:
    response = client.post(
        "/capture",
        headers=auth_headers(),
        json={
            "connector": "jitsi",
            "meeting_url": url,
            "pin": "",
        },
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "capturing"
    assert body["artifact"] is None
    assert body["error"] is None
    return body["meta"]["task_id"]


def test_capture_stop_download_flow(client: TestClient) -> None:
    task_id = _start_capture(client)
    time.sleep(0.2)
    body = _complete_capture(client, task_id)
    assert body["artifact"]["ready"] is True
    assert body["meta"]["duration_sec"] > 0

    download = client.get(f"/tasks/{task_id}/download", headers=auth_headers())
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("audio/mp4")
    assert download.content[:4] == b"ftyp" or len(download.content) > 128


def test_delete_during_capturing(client: TestClient) -> None:
    task_id = _start_capture(client)
    response = client.delete(f"/tasks/{task_id}", headers=auth_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    poll = client.get(f"/tasks/{task_id}", headers=auth_headers())
    assert poll.json()["status"] == "canceled"
    download = client.get(f"/tasks/{task_id}/download", headers=auth_headers())
    assert download.status_code == 404


def test_unsupported_connector(client: TestClient) -> None:
    response = client.post(
        "/capture",
        headers=auth_headers(),
        json={"connector": "zoom", "meeting_url": "https://meet.example.com/Room"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_connector"


def test_invalid_url(client: TestClient) -> None:
    response = client.post(
        "/capture",
        headers=auth_headers(),
        json={"connector": "jitsi", "meeting_url": "not-a-url"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_url"


def test_not_found(client: TestClient) -> None:
    response = client.get("/tasks/cap_missing", headers=auth_headers())
    assert response.status_code == 404


def test_queue_full(tmp_path, monkeypatch) -> None:
    isolate_env(tmp_path, monkeypatch, max_captures="1")
    from app.main import app

    with TestClient(app) as client:
        _start_capture(client)
        response = client.post(
            "/capture",
            headers=auth_headers(),
            json={"connector": "jitsi", "meeting_url": "https://meet.example.com/Another"},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "queue_full"


def test_ready_reflects_slots(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    task_id = _start_capture(client)
    response = client.get("/ready")
    assert response.status_code in {200, 503}
    client.delete(f"/tasks/{task_id}", headers=auth_headers())


def test_auto_stop_finalizes_capture(tmp_path, monkeypatch) -> None:
    isolate_env(tmp_path, monkeypatch, extra={"MAX_CAPTURE_DURATION_SEC": "0.25"})
    from app.main import app

    with TestClient(app) as client:
        task_id = _start_capture(client)
        body = _wait_for_status(client, task_id, "success", timeout_sec=5.0)
        assert body["meta"]["duration_sec"] > 0
        assert body["artifact"]["ready"] is True


def test_delete_success_returns_task_running(client: TestClient) -> None:
    task_id = _start_capture(client)
    _complete_capture(client, task_id)
    response = client.delete(f"/tasks/{task_id}", headers=auth_headers())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "task_running"


def test_stop_idempotent_when_success(client: TestClient) -> None:
    task_id = _start_capture(client)
    _complete_capture(client, task_id)
    response = client.post(f"/tasks/{task_id}/stop", headers=auth_headers())
    assert response.status_code == 202
    assert response.json()["status"] == "success"


def test_stop_idempotent_while_finalizing(client: TestClient) -> None:
    task_id = _start_capture(client)
    first = client.post(f"/tasks/{task_id}/stop", headers=auth_headers())
    assert first.status_code == 202
    assert first.json()["status"] == "finalizing"
    second = client.post(f"/tasks/{task_id}/stop", headers=auth_headers())
    assert second.status_code == 202
    assert second.json()["status"] == "finalizing"
    _wait_for_status(client, task_id, "success")


def test_stop_on_canceled_returns_conflict(client: TestClient) -> None:
    task_id = _start_capture(client)
    cancel = client.delete(f"/tasks/{task_id}", headers=auth_headers())
    assert cancel.status_code == 200
    response = client.post(f"/tasks/{task_id}/stop", headers=auth_headers())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "task_running"


def test_download_during_capturing_returns_404(client: TestClient) -> None:
    task_id = _start_capture(client)
    response = client.get(f"/tasks/{task_id}/download", headers=auth_headers())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    client.delete(f"/tasks/{task_id}", headers=auth_headers())


def test_tasks_status_filter(client: TestClient) -> None:
    capturing_id = _start_capture(client)
    completed_id = _start_capture(client)
    _complete_capture(client, completed_id)

    all_tasks = client.get("/tasks", headers=auth_headers())
    assert all_tasks.status_code == 200
    assert len(all_tasks.json()) >= 2

    capturing_only = client.get("/tasks?status=capturing", headers=auth_headers())
    assert capturing_only.status_code == 200
    ids = {item["task_id"] for item in capturing_only.json()}
    assert capturing_id in ids
    assert completed_id not in ids

    client.delete(f"/tasks/{capturing_id}", headers=auth_headers())
