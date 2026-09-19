# Мониторинг icapture-worker

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|------------|--------------|------------|
| `METRICS_ENABLED` | `true` | Прикладные Prometheus-коллекторы |
| `METRICS_TOKEN` | — | Bearer для `GET /metrics`; пустой token → 401 |

## Endpoint метрик

```bash
curl -s -H "Authorization: Bearer $METRICS_TOKEN" http://127.0.0.1:8000/metrics
```

Префикс: `icapture_`

Основные метрики:

- `icapture_up`
- `icapture_capture_tasks_active`
- `icapture_capture_tasks_total{status=...}`
- `icapture_capture_slots_available`
- `icapture_capture_join_failures_total{reason=...}`
- `icapture_capture_finalize_seconds`
- `icapture_http_requests_total{method,route,status}`

Запросы к `/metrics` не попадают в `icapture_http_requests_total`.

## Scrape Prometheus

См. [deploy/prometheus/scrape.example.yml](../../../deploy/prometheus/scrape.example.yml).

Не публикуйте `/metrics` в публичный ingress без auth.

## Grafana

Импортируйте [grafana/dashboards/icapture-worker.json](../../../grafana/dashboards/icapture-worker.json).

При импорте выберите datasource Prometheus.
