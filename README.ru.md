# icapture-worker

Внешний worker захвата аудио для [idigest-hub](https://github.com/dude75/idigest-hub). Бот join'ит видеоконференцию, пишет mixed audio и отдаёт артефакт AAC `.m4a` (44.1 kHz) для transcribe pipeline.

English: [README.md](README.md)

## Что делает

1. Hub → `POST /capture` → бот в комнате, запись аудио.
2. Stop → Hub → `POST /tasks/{id}/stop` → финализация `.m4a`.
3. Hub poll'ит `GET /tasks/{id}` до `status=success`.
4. Hub скачивает `GET /tasks/{id}/download` → transcribe.

Не входит в v1: lobby, live transcript, Zoom/Teams, Puppeteer/Jibri.

## Требования

- Python 3.12+
- **Node.js 22 LTS** (sidecar Jitsi engine; Node 25+ может ломать polyfill)
- `@roamhq/wrtc` вместо устаревшего `wrtc` (Apple Silicon)

### Node: типичная ошибка npm

```
node-pre-gyp: command not found
```

или 404 на `darwin-arm64` — старый пакет `wrtc` не поддерживает Mac M1/M2/M3.

**Fix:** обновить deps и переустановить:

```bash
cd node/jitsi
rm -rf node_modules package-lock.json
npm install
npm start
```

Проверка Node: `curl -s http://127.0.0.1:8001/health | jq` → `connectors.jitsi.status` = `"loaded"`.

## Архитектура

```
Hub / curl  →  Python FastAPI (:8000)  →  Node Jitsi engine (:8001)  →  Jitsi Meet
               задачи, auth, metrics       join комнаты, запись аудио
```

- **Python API** — принимает HTTP от Hub, управляет задачами.
- **Node Jitsi engine** — единственная часть, которая подключается к Jitsi (lib-jitsi-meet + wrtc).
- **«Node» в доке** = Node Jitsi engine (`npm start`), **не** Jitsi-сервер.

## Установка (один раз)

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env
cd node/jitsi && npm install && cd ../..
```

## Запуск — реальная конференция (prod / manual E2E)

**Без stub.** Два терминала:

```bash
# Терминал 1 — Node Jitsi engine
cd node/jitsi && npm start

# Терминал 2 — Python API
./.venv/bin/python -m app.serve
```

Проверка:

```bash
curl -s http://127.0.0.1:8000/health | jq
```

Пример `connectors` (объекты с `status`, `label`; `reason` только при `unavailable`):

```json
{
  "jitsi": { "status": "loaded", "label": "Jitsi Meet" },
  "zoom": { "status": "unavailable", "label": "Zoom", "reason": "not_implemented" }
}
```

Старт захвата:

```bash
curl -s -X POST http://127.0.0.1:8000/capture \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"connector":"jitsi","meeting_url":"https://meet.example.com/RoomName","pin":""}'
```

Бот появится в комнате Jitsi. Stop → `POST /tasks/{id}/stop` → download `.m4a`.

## Что такое stub

**Stub** (заглушка) — режим, в котором часть системы **имитирует** работу, не делая реального действия.

| | Боевой режим | Stub |
|---|---|---|
| Join в Jitsi | да, бот в комнате | **нет** |
| Аудио | с конференции | **синус / тишина в `.m4a`** |
| Node Jitsi engine | нужен | может не понадобиться |
| Для чего | prod, ручной E2E | **pytest, локальная отладка API** |

Два stub-флага (не путать):

| Флаг | Где | Что делает |
|------|-----|------------|
| `ICAPTURE_STUBS=1` | Python | Python **сам** притворяется, что идёт захват. Node **не вызывается** — второй терминал не нужен. |
| `JITSI_STUB=1` | Node (`npm start`) | Node **запущен**, Python с ним общается, но Node **не идёт** в Jitsi — только пишет тестовый артефакт. |

**«Без Node»** в контексте stub = Node Jitsi engine (`npm start`) **не запущен**, потому что Python при `ICAPTURE_STUBS=1` до него не обращается. Это **не** про Jitsi-сервер.

Stub **не использовать в production** — Hub и пользователи не получат реальную запись созвона.

## Запуск — тесты / разработка (stub)

| Режим | Python | Node | Зачем |
|-------|--------|------|-------|
| **A** — pytest | `ICAPTURE_STUBS=1` | не нужен | `./.venv/bin/pytest` |
| **B** — API без Node | `ICAPTURE_STUBS=1` | не нужен | проверить HTTP pipeline локально |
| **C** — связка Python↔Node | без stub | `JITSI_STUB=1 npm start` | проверить sidecar без Jitsi |

**Режим B** (самый простой для dev):

```bash
ICAPTURE_STUBS=1 ./.venv/bin/python -m app.serve
# Node не запускаем
```

**Режим C** (если нужно проверить Node sidecar):

```bash
# Терминал 1
cd node/jitsi && JITSI_STUB=1 npm start

# Терминал 2 — ICAPTURE_STUBS не ставить
./.venv/bin/python -m app.serve
```

## `.env`

| Переменная | По умолчанию | Назначение |
|------------|--------------|------------|
| `API_TOKEN` | — | Bearer Hub → worker |
| `METRICS_TOKEN` | — | Bearer для Prometheus |
| `MAX_CONCURRENT_CAPTURES` | `2` | Параллельные захваты |
| `MAX_CAPTURE_DURATION_SEC` | `14400` | Auto-stop 4ч |
| `TASK_TTL_SEC` | `3600` | Удаление завершённых задач и файлов артефактов |
| `JITSI_ENGINE_URL` | `http://127.0.0.1:8001` | URL Node sidecar |
| `ICAPTURE_STUBS` | — | `1` = stub в Python; Node не нужен (только тесты) |

## API

Auth: `Authorization: Bearer <API_TOKEN>`.

| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/health` | Без token; `connectors` (status/label/reason) + `slots` |
| GET | `/ready` | 200 если есть free slot |
| POST | `/capture` | Старт захвата (**202**) |
| GET | `/tasks/{id}` | Poll статуса |
| POST | `/tasks/{id}/stop` | Graceful stop (**202**) |
| GET | `/tasks/{id}/download` | `.m4a` при `success` |
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

## Docker Compose

```bash
docker compose up --build
```

## Тесты

```bash
./.venv/bin/pytest
```
