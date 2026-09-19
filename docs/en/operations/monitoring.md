# icapture-worker monitoring

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `METRICS_ENABLED` | `true` | Application Prometheus collectors |
| `METRICS_TOKEN` | — | Bearer token for `GET /metrics`; empty token → 401 |

## Metrics endpoint

```bash
curl -s -H "Authorization: Bearer $METRICS_TOKEN" http://127.0.0.1:8000/metrics
```

Prefix: `icapture_`

Key metrics:

- `icapture_up`
- `icapture_capture_tasks_active`
- `icapture_capture_tasks_total{status=...}`
- `icapture_capture_slots_available`
- `icapture_capture_join_failures_total{reason=...}`
- `icapture_capture_finalize_seconds`
- `icapture_http_requests_total{method,route,status}`

`/metrics` requests are excluded from `icapture_http_requests_total`.

## Prometheus scrape

See [deploy/prometheus/scrape.example.yml](../../../deploy/prometheus/scrape.example.yml).

Do not expose `/metrics` on a public ingress without authentication.

## Grafana

Import [grafana/dashboards/icapture-worker.json](../../../grafana/dashboards/icapture-worker.json).

Select your Prometheus datasource when prompted.
