# icapture-worker

Внешний worker захвата аудио для [idigest-hub](https://github.com/dude75/idigest-hub). Бот join'ит видеоконференцию, пишет mixed audio и отдаёт артефакт MP3 (44.1 kHz, libmp3lame VBR) для transcribe pipeline.

English: [README.md](README.md)

## Что делает

1. Hub → `POST /capture` → бот в комнате, запись аудио.
2. Захват завершается по `POST /tasks/{id}/stop` от Hub или по **auto-finalize** (см. ниже).
3. Hub poll'ит `GET /tasks/{id}` до `status=success`.
4. Hub скачивает `GET /tasks/{id}/download` → transcribe.

**Auto-finalize** (тот же путь, что и graceful stop — `finalizing` → `success` с MP3):

- истёк `MAX_CAPTURE_DURATION_SEC`
- модератор **выгнал** бота из конференции (kick)
- бот **вышел** или потерял конференцию (disconnect, закрытие комнаты, закрытие страницы)

В логах: `browser capture auto-stop task=… reason=kicked|conference_left|…`.

Не входит в v1: live transcript, Zoom/Teams, режим Jibri. **PIN lobby** Jitsi — через поле `pin` в `POST /capture`, если на инстансе включён пароль на вход.

## Требования

- Python 3.12+
- **ffmpeg** в `PATH` (кодирование MP3)
- **Playwright Chromium** (join Jitsi в браузере + mix аудио); в Docker ставится при сборке; локально — см. установку

## Архитектура

```
Hub / curl  →  Python FastAPI (:8000)
               ├─ очередь задач, auth, metrics, SQLite
               └─ Playwright Chromium → Jitsi Meet (web-клиент)
```

- **Один процесс** — без Node sidecar; захват в `app/capture/` (`browser.py`, `engine.py`).
- **`node/jitsi/`** — legacy lib-jitsi-meet + wrtc; **не** подключён к `python -m app.serve`.

## Установка (один раз)

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m playwright install chromium chromium-headless-shell
# Linux (системные libs для Chromium):
# ./.venv/bin/python -m playwright install-deps chromium
cp .env.example .env
```

## Запуск (prod / manual E2E)

```bash
./.venv/bin/python -m app.serve
```

Проверка:

```bash
curl -s http://127.0.0.1:8000/health | jq
```

Пример `connectors` и `workers` (пул слотов для dispatch в idigest-hub):

```json
{
  "workers": { "max": 2, "active": 0, "available": 2 },
  "connectors": {
    "jitsi": { "status": "loaded", "label": "Jitsi Meet" },
    "zoom": { "status": "unavailable", "label": "Zoom", "reason": "not_implemented" }
  }
}
```

`workers.max` = `WORKERS`; `active` — принятые задачи (от POST /capture до terminal); `available` = `max - active` (с обрезкой).

Старт захвата:

```bash
curl -s -X POST http://127.0.0.1:8000/capture \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"connector":"jitsi","meeting_url":"https://meet.example.com/RoomName","pin":""}'
```

Бот заходит через web UI Jitsi (prejoin по возможности пропускается, микрофон muted). Ручной stop → `POST /tasks/{id}/stop` → download `.mp3`, либо auto-finalize после kick / таймаута.

Если `GET /health` → `jitsi.status: unavailable`, смотрите `reason` (часто нет Chromium — `playwright install` выше).

## `.env`

| Переменная | По умолчанию | Назначение |
|------------|--------------|------------|
| `API_TOKEN` | — | Bearer Hub → worker |
| `METRICS_TOKEN` | — | Bearer для Prometheus |
| `WORKERS` | `2` | Параллельные захваты в процессе |
| `WORKER_QUEUE_SIZE` | `0` | Доп. принятие в `queued` сверх `WORKERS` (0 — для idigest-hub) |
| `ENABLED_CONNECTORS` | `jitsi` | Какие коннекторы принимает инстанс |
| `MAX_CAPTURE_DURATION_SEC` | `14400` | Auto-stop 4ч (`0` = выкл.) |
| `CAPTURE_FINALIZE_TIMEOUT_SEC` | `120` | Лимит ожидания финализации после auto-stop |
| `TASK_TTL_SEC` | `3600` | Удаление завершённых задач и файлов артефактов |
| `PLAYWRIGHT_HEADLESS` | `true` | Headless Chromium; `false` — отладка с UI |
| `LOG_DIR` | `./data/logs` | Скриншоты при таймауте join |

## API

Auth: `Authorization: Bearer <API_TOKEN>`.

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/health` | Без token; `connectors`, `workers` (max/active/available) |
| POST | `/capture` | Старт захвата (**202**) |
| GET | `/tasks/{id}` | Poll статуса |
| POST | `/tasks/{id}/stop` | Graceful stop (**202**) |
| GET | `/tasks/{id}/download` | `.mp3` при `success` |
| DELETE | `/tasks/{id}` | Cancel без artifact |
| GET | `/metrics` | Prometheus (`METRICS_TOKEN`) |

После `TASK_TTL_SEC` завершённые задачи удаляются из БД и с диска — `GET /tasks/{id}` и download → **404** (`not_found`).

## Подключение к idigest-hub

Instance admin → Workers → type **`capture`** → `base_url` + `api_token` → probe → connectors.

Интеграция Hub — отдельный PR в idigest-hub.

## Метрики

```bash
curl -s -H "Authorization: Bearer $METRICS_TOKEN" http://127.0.0.1:8000/metrics
```

Подробнее: [docs/ru/operations/monitoring.md](docs/ru/operations/monitoring.md)

Grafana: [grafana/dashboards/icapture-worker.json](grafana/dashboards/icapture-worker.json)

Пример scrape Prometheus: [deploy/prometheus/scrape.example.yml](deploy/prometheus/scrape.example.yml)

## Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

Сервис `icapture-worker`: API + Playwright/Chromium в одном контейнере (порт 8000). Браузеры — на этапе сборки образа.

## Тесты

```bash
./.venv/bin/pytest
```
