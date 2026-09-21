# Мониторинг icapture-worker

## Переменные окружения

| Переменная | По умолчанию | Назначение |
|------------|--------------|------------|
| `METRICS_ENABLED` | `true` | Прикладные Prometheus-коллекторы |
| `METRICS_TOKEN` | — | Bearer для `GET /metrics`; пустой token → 401 |
| `WORKERS` | `2` | Параллельные захваты (см. `icapture_worker_slots`) |
| `WORKER_QUEUE_SIZE` | `0` | Макс. задач в `queued` сверх `WORKERS`; очередь полна, когда pending ≥ `WORKERS + WORKER_QUEUE_SIZE` |

## Endpoint метрик

```bash
curl -s -H "Authorization: Bearer $METRICS_TOKEN" http://127.0.0.1:8000/metrics
```

Префикс: `icapture_`

При kick, выходе из комнаты или таймауте захват завершается автоматически (auto-finalize); в логах приложения ищите `browser capture auto-stop` и переход задачи в `finalizing` / `success`.

### Gauge при scrape

- `icapture_up` — процесс отдаёт `/metrics`
- `icapture_capture_tasks_active` — задачи в `queued`, `capturing` или `finalizing`
- `icapture_capture_tasks_queued` — задачи в очереди на слот
- `icapture_worker_slots` — значение `WORKERS`

### Счётчики и гистограммы

- `icapture_capture_task_transitions_total{status=...}` — смена статуса (`queued`, `capturing`, `finalizing`, `success`, `error`, `canceled`, …)
- `icapture_capture_join_failures_total{reason=...}` — неудачный join в комнату
- `icapture_capture_finalize_seconds` — задержка finalize (stop → готов MP3)
- `icapture_http_requests_total{method,route,status}` — HTTP API по шаблону route
- `icapture_http_request_duration_seconds{method,route}` — латентность API

Дополнительно: `icapture_info` (версия), стандартные `process_*`, `python_*` и GC-коллекторы.

Запросы к `/metrics` не попадают в `icapture_http_requests_total` и `icapture_http_request_duration_seconds`.

## Scrape Prometheus

См. [deploy/prometheus/scrape.example.yml](../../../deploy/prometheus/scrape.example.yml).

В `authorization.credentials` указывается только token; Prometheus шлёт `Authorization: Bearer …`.

Не публикуйте `/metrics` в публичный ingress без auth.

## Grafana

Импортируйте [grafana/dashboards/icapture-worker.json](../../../grafana/dashboards/icapture-worker.json).

При импорте выберите datasource Prometheus. Панели используют переменные `$job` и `$instance` из `icapture_up`.
