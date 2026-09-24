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

- **Один процесс** — захват в `app/capture/` (`browser.py`, `telemost.py`, `engine.py`).

Коннекторы (Jitsi, Telemost): [docs/ru/connectors.md](docs/ru/connectors.md).

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
    "zoom": { "status": "unavailable", "label": "Zoom", "reason": "not_implemented" },
    "meet": { "status": "unavailable", "label": "Google Meet", "reason": "not_implemented" }
  }
}
```

`workers.max` = `WORKERS`; `active` — принятые задачи (от POST /capture до terminal); `available` = `max - active` (с обрезкой).

Старт захвата:

```bash
curl -s -X POST http://127.0.0.1:8000/capture \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"connector":"jitsi","meeting_url":"https://meet.example.com/RoomName","pin":"","display_name":"Transcription Bot"}'
```

Опционально в теле: `pin` (пароль lobby), `display_name` (по умолчанию `DEFAULT_BOT_DISPLAY_NAME`). Поле `jwt` принимается для совместимости API, но в Playwright-пути Jitsi пока не используется.

Бот заходит через web UI Jitsi (prejoin по возможности пропускается, микрофон muted). Ручной stop → `POST /tasks/{id}/stop` → download `.mp3`, либо auto-finalize после kick / таймаута.

**Yandex Telemost** — тот же lifecycle задач (`POST /capture` … `stop` / auto-finalize). В `.env`: `ENABLED_CONNECTORS=jitsi,telemost`. Тело:

```bash
curl -s -X POST http://127.0.0.1:8000/capture \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"connector":"telemost","meeting_url":"https://telemost.yandex.ru/j/XXXXXXXXXXXX","display_name":"Transcription Bot"}'
```

Гостевая ссылка `/j/<id>` или `/private-join/<id>`. Поле `pin` не используется. PCM пишется так же, как у Jitsi: периодический drain из `__icapture` → append в `{task_id}.pcm` → MP3 при finalize.

Если `GET /health` → `jitsi.status: unavailable`, смотрите `reason` (часто нет Chromium — `playwright install` выше).

## `.env`

| Переменная | По умолчанию | Назначение |
|------------|--------------|------------|
| `API_TOKEN` | — | Bearer Hub → worker |
| `METRICS_TOKEN` | — | Bearer для Prometheus |
| `HOST` | `0.0.0.0` | Адрес bind |
| `PORT` | `8000` | HTTP-порт |
| `DATA_DIR` | `./data` | Артефакты и SQLite |
| `WORKERS` | `2` | Параллельные захваты в процессе |
| `WORKER_QUEUE_SIZE` | `0` | Доп. принятие в `queued` сверх `WORKERS` (0 — для idigest-hub) |
| `ENABLED_CONNECTORS` | `jitsi` | Какие коннекторы принимает инстанс |
| `MAX_CAPTURE_DURATION_SEC` | `14400` | Auto-stop 4ч (`0` = выкл.) |
| `CAPTURE_FINALIZE_TIMEOUT_SEC` | `120` | Лимит ожидания финализации после auto-stop |
| `TASK_TTL_SEC` | `3600` | Удаление завершённых задач и файлов артефактов |
| `DEFAULT_BOT_DISPLAY_NAME` | `Transcription Bot` | Имя бота в комнате |
| `PLAYWRIGHT_HEADLESS` | `true` | Headless Chromium; `false` — отладка с UI |
| `LOG_DIR` | `./data/logs` | Скриншоты при таймауте join |
| `SQLITE_PATH` | `./data/tasks.db` | Хранилище задач (в Docker — под `DATA_DIR`) |
| `LOG_LEVEL` | `info` | Уровень логов |
| `METRICS_ENABLED` | `true` | Прикладные Prometheus-коллекторы |
| `ARTIFACT_SAMPLE_RATE` | `44100` | Sample rate MP3 (только 44100) |
| `FFMPEG_MP3_VBR_QUALITY` | `2` | Качество libmp3lame VBR (`0` лучше … `9` хуже) |
| `TELEMOST_JOIN_TIMEOUT_SEC` | `180` | Таймаут join Telemost (комната ожидания) |
| `TELEMOST_STORAGE_STATE` | — | Playwright storage (опционально, Yandex session) |
| `TELEMOST_CDP_GRANT` | `true` | CDP `audioCapture` для Telemost |
| `TELEMOST_GUM_FALLBACK` | `true` | Dummy tracks при отказе getUserMedia |

## API

Auth: `Authorization: Bearer <API_TOKEN>`.

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/health` | Без token; `connectors`, `workers` (max/active/available) |
| GET | `/tasks` | Список задач (probe) |
| POST | `/capture` | Старт захвата (**202**) |
| GET | `/tasks/{id}` | Poll статуса |
| POST | `/tasks/{id}/stop` | Graceful stop (**202**) |
| GET | `/tasks/{id}/download` | `.mp3` при `success` |
| DELETE | `/tasks/{id}` | Cancel без artifact |
| GET | `/metrics` | Prometheus (`METRICS_TOKEN`) |

Статусы задачи: `queued` → `joining` → `capturing` → `finalizing` → `success` | `error` | `canceled` (`queued` — только при `WORKER_QUEUE_SIZE` > 0 и занятых слотах).

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

Сервис `icapture-worker`: API + Playwright/Chromium в одном контейнере. `PORT` в `.env` — порт приложения и проброс Compose (`PORT:PORT`, по умолчанию 8000). Браузеры — на этапе сборки образа.

В образе по умолчанию `WORKERS=2` и `WORKER_QUEUE_SIZE=0`, если не переопределить в `.env`.

## Типичные ошибки

| HTTP | `error.code` | Когда |
|------|--------------|-------|
| 401 | `unauthorized` | Неверный или отсутствующий token |
| 503 | `queue_full` | Нет свободных слотов захвата |
| 400 | `unsupported_connector` | Неизвестный connector |
| 400 | `invalid_url` | Некорректный URL встречи |
| 422 | `join_failed` | Не удалось зайти в комнату |
| 404 | `not_found` | Неизвестная задача |
| 409 | `task_running` | Конфликт операции |

## Репозиторий

- **GitLab (основной):** [gitlab.it.realweb.ru/dude75/icapture-worker](https://gitlab.it.realweb.ru/dude75/icapture-worker) — эталонная копия для деплоя в Realweb.
- **GitHub (опциональное зеркало):** [github.com/dude75/icapture-worker](https://github.com/dude75/icapture-worker) — публичная копия для ссылок и внешних читателей; для запуска worker не обязательна. Зеркало синхронизируется по возможности и может отставать от GitLab.

Та же схема, что у [itranscribe-worker](https://github.com/dude75/itranscribe-worker) и [isummarize-worker](https://github.com/dude75/isummarize-worker).

## SPIKE: Yandex Telemost (ручная проверка)

Дублирует production-путь для отладки UI; основной поток — `connector: telemost` и `POST /capture`. Не входит в `pytest`.

```bash
export TELEMOST_MEETING_URL='https://telemost.yandex.ru/j/XXXXXXXXXXXX'
export PLAYWRIGHT_HEADLESS=false
./.venv/bin/python scripts/spike_telemost_join.py
```

Скриншоты: `LOG_DIR/spike-telemost-*.png`. Если включена комната ожидания — впустите бота; таймаут join: `SPIKE_JOIN_TIMEOUT_SEC` (по умолчанию 180).

Как Jitsi: Chromium с fake media flags; для Телемоста дополнительно CDP `Browser.grantPermissions` и fallback `getUserMedia` (`TELEMOST_CDP_GRANT`, `TELEMOST_GUM_FALLBACK`, по умолчанию включены). Jitsi-путь в worker пока без CDP — только flags + `grant_permissions`.

## Тесты

```bash
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pytest
```
