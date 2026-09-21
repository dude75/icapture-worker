# icapture-worker monitoring

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `METRICS_ENABLED` | `true` | Application Prometheus collectors |
| `METRICS_TOKEN` | — | Bearer token for `GET /metrics`; empty token → 401 |
| `WORKERS` | `2` | Parallel capture slots (see `icapture_worker_slots`) |
| `WORKER_QUEUE_SIZE` | `0` | Max `queued` tasks beyond `WORKERS`; queue full when pending ≥ `WORKERS + WORKER_QUEUE_SIZE` |

## Metrics endpoint

```bash
curl -s -H "Authorization: Bearer $METRICS_TOKEN" http://127.0.0.1:8000/metrics
```

Prefix: `icapture_`

During capture, disconnects (kick, leave, timeout) trigger auto-finalize; watch app logs for `browser capture auto-stop` and task transitions to `finalizing` / `success`.

### Runtime gauges (scrape time)

- `icapture_up` — process serves `/metrics`
- `icapture_capture_tasks_active` — tasks in `queued`, `capturing`, or `finalizing`
- `icapture_capture_tasks_queued` — tasks waiting for a worker slot
- `icapture_worker_slots` — configured `WORKERS`

### Counters and histograms

- `icapture_capture_task_transitions_total{status=...}` — status changes (`queued`, `capturing`, `finalizing`, `success`, `error`, `canceled`, …)
- `icapture_capture_join_failures_total{reason=...}` — failed room joins
- `icapture_capture_finalize_seconds` — finalize latency (stop → MP3 ready)
- `icapture_http_requests_total{method,route,status}` — API traffic by route template
- `icapture_http_request_duration_seconds{method,route}` — API latency

Also exported: `icapture_info` (version), plus standard `process_*`, `python_*`, and GC collectors.

`/metrics` requests are excluded from `icapture_http_requests_total` and `icapture_http_request_duration_seconds`.

## Prometheus scrape

See [deploy/prometheus/scrape.example.yml](../../../deploy/prometheus/scrape.example.yml).

`authorization.credentials` is the raw token; Prometheus sends `Authorization: Bearer …`.

Do not expose `/metrics` on a public ingress without authentication.

## Grafana

Import [grafana/dashboards/icapture-worker.json](../../../grafana/dashboards/icapture-worker.json).

Select your Prometheus datasource when prompted. Panels use `$job` and `$instance` variables from `icapture_up`.
