from fastapi.testclient import TestClient

from tests.conftest import metrics_headers


def test_metrics_excludes_self_from_http_counter(client: TestClient) -> None:
    response = client.get("/metrics", headers=metrics_headers())
    assert response.status_code == 200
    text = response.text
    assert "icapture_up" in text
    assert "icapture_capture_slots_available" in text
    assert 'route="/metrics"' not in text or 'icapture_http_requests_total{method="GET",route="/metrics"' not in text
