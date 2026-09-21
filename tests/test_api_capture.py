import time

from fastapi.testclient import TestClient

from tests.conftest import auth_headers, complete_capture, isolate_env, start_capture, wait_for_task_status


def test_capture_stop_download_flow(client: TestClient) -> None:
    task_id = start_capture(client)
    body = complete_capture(client, task_id)
    assert body["artifact"]["ready"] is True
    assert body["meta"]["duration_sec"] > 0

    download = client.get(f"/tasks/{task_id}/download", headers=auth_headers())
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("audio/mpeg")
    content = download.content
    content_length = download.headers.get("content-length")
    if content_length is not None:
        assert int(content_length) == len(content)
    assert content[:3] == b"ID3" or (
        len(content) >= 2 and content[0] == 0xFF and (content[1] & 0xE0) == 0xE0
    )


def test_delete_during_capturing(client: TestClient) -> None:
    task_id = start_capture(client)
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
    isolate_env(tmp_path, monkeypatch, workers="1", worker_queue_size="0")
    from app.main import app

    with TestClient(app) as client:
        start_capture(client)
        response = client.post(
            "/capture",
            headers=auth_headers(),
            json={"connector": "jitsi", "meeting_url": "https://meet.example.com/Another"},
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "queue_full"


def test_auto_stop_finalizes_capture(tmp_path, monkeypatch) -> None:
    isolate_env(tmp_path, monkeypatch, extra={"MAX_CAPTURE_DURATION_SEC": "1.0"})
    from app.main import app

    with TestClient(app) as client:
        task_id = start_capture(client)
        body = wait_for_task_status(client, task_id, "success", timeout_sec=5.0)
        assert body["meta"]["duration_sec"] > 0
        assert body["artifact"]["ready"] is True


def test_delete_success_returns_task_running(client: TestClient) -> None:
    task_id = start_capture(client)
    complete_capture(client, task_id)
    response = client.delete(f"/tasks/{task_id}", headers=auth_headers())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "task_running"


def test_stop_idempotent_when_success(client: TestClient) -> None:
    task_id = start_capture(client)
    complete_capture(client, task_id)
    response = client.post(f"/tasks/{task_id}/stop", headers=auth_headers())
    assert response.status_code == 202
    assert response.json()["status"] == "success"


def test_stop_idempotent_while_finalizing(client: TestClient) -> None:
    task_id = start_capture(client)
    time.sleep(0.75)
    first = client.post(f"/tasks/{task_id}/stop", headers=auth_headers())
    assert first.status_code == 202
    assert first.json()["status"] == "finalizing"
    second = client.post(f"/tasks/{task_id}/stop", headers=auth_headers())
    assert second.status_code == 202
    assert second.json()["status"] == "finalizing"
    wait_for_task_status(client, task_id, "success")


def test_stop_on_canceled_returns_conflict(client: TestClient) -> None:
    task_id = start_capture(client)
    cancel = client.delete(f"/tasks/{task_id}", headers=auth_headers())
    assert cancel.status_code == 200
    response = client.post(f"/tasks/{task_id}/stop", headers=auth_headers())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "task_running"


def test_download_during_capturing_returns_404(client: TestClient) -> None:
    task_id = start_capture(client)
    response = client.get(f"/tasks/{task_id}/download", headers=auth_headers())
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    client.delete(f"/tasks/{task_id}", headers=auth_headers())


def test_tasks_status_filter(client: TestClient) -> None:
    capturing_id = start_capture(client)
    completed_id = start_capture(client, url="https://meet.example.com/OtherRoom")
    complete_capture(client, completed_id)

    all_tasks = client.get("/tasks", headers=auth_headers())
    assert all_tasks.status_code == 200
    assert len(all_tasks.json()) >= 2

    capturing_only = client.get("/tasks?status=capturing", headers=auth_headers())
    assert capturing_only.status_code == 200
    ids = {item["task_id"] for item in capturing_only.json()}
    assert capturing_id in ids
    assert completed_id not in ids

    client.delete(f"/tasks/{capturing_id}", headers=auth_headers())
