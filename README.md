# icapture-worker

External capture worker for [idigest-hub](https://github.com/dude75/idigest-hub). Joins a video conference as a bot, records mixed audio, and returns an MP3 artifact (44.1 kHz, libmp3lame VBR) for the transcribe pipeline.

Russian: [README.ru.md](README.ru.md)

## What it does

1. Hub calls `POST /capture` → bot joins the room and records audio.
2. Capture ends when the Hub calls `POST /tasks/{id}/stop`, or when the worker **auto-finalizes** (see below).
3. Hub polls `GET /tasks/{id}` until `status=success`.
4. Hub downloads `GET /tasks/{id}/download` and continues with transcribe.

**Auto-finalize** (same path as graceful stop — task moves to `finalizing` → `success` with an MP3):

- `MAX_CAPTURE_DURATION_SEC` elapsed
- Moderator **kick** from the conference
- Bot **leaves** or loses the conference (disconnect, room closed, page closed)

Look for log lines: `browser capture auto-stop task=… reason=kicked|conference_left|…`.

Not included in v1: live transcript, Zoom/Teams capture, Jibri/recording-bridge mode. Jitsi **lobby PIN** is supported via `pin` in `POST /capture` when the deployment uses a password lobby.

## Requirements

- Python 3.12+
- **ffmpeg** on `PATH` (MP3 encode)
- **Playwright Chromium** (Jitsi join + in-page audio mix); installed in Docker at build time; for local dev see Install

## Architecture

```
Hub / curl  →  Python FastAPI (:8000)
               ├─ task queue, auth, metrics, SQLite
               └─ Playwright Chromium → Jitsi Meet (web client)
```

- **Single process** — capture lives under `app/capture/` (`browser.py`, `engine.py`).

## Install (once)

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/python -m playwright install chromium chromium-headless-shell
# Linux only (system libs for Chromium):
# ./.venv/bin/python -m playwright install-deps chromium
cp .env.example .env
```

## Run — real conference (prod / manual E2E)

```bash
./.venv/bin/python -m app.serve
```

Health check:

```bash
curl -s http://127.0.0.1:8000/health | jq
```

Example `connectors` (objects with `status`, `label`; `reason` only when `unavailable`) and `workers` (in-process pool for idigest-hub dispatch):

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

`workers.max` = `WORKERS`; `active` = pending captures (from accept until terminal); `available` = `max - active` (capped).

Start capture:

```bash
curl -s -X POST http://127.0.0.1:8000/capture \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"connector":"jitsi","meeting_url":"https://meet.example.com/RoomName","pin":"","display_name":"Transcription Bot"}'
```

Optional body fields: `pin` (lobby password), `display_name` (defaults to `DEFAULT_BOT_DISPLAY_NAME`). Field `jwt` is accepted for API compatibility but not used by the Playwright Jitsi path yet.

The bot joins the Jitsi room via the web UI (prejoin skipped where possible, mic muted). Stop manually → `POST /tasks/{id}/stop` → download `.mp3`, or wait for auto-finalize after kick / timeout.

If `GET /health` shows `jitsi.status: unavailable`, check `reason` (often missing Chromium — run `playwright install` as above).

## `.env`

| Variable | Default | Meaning |
|----------|---------|---------|
| `API_TOKEN` | — | Bearer token for Hub → worker |
| `METRICS_TOKEN` | — | Bearer token for Prometheus scrape |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | HTTP port |
| `DATA_DIR` | `./data` | Artifacts and SQLite |
| `WORKERS` | `2` | In-process parallel captures |
| `WORKER_QUEUE_SIZE` | `0` | Extra `queued` accepts beyond `WORKERS` (0 recommended for idigest-hub) |
| `ENABLED_CONNECTORS` | `jitsi` | Connectors this instance accepts |
| `MAX_CAPTURE_DURATION_SEC` | `14400` | Auto-stop after 4h (`0` = disabled) |
| `CAPTURE_FINALIZE_TIMEOUT_SEC` | `120` | Max wait after auto-stop before giving up on finalize |
| `TASK_TTL_SEC` | `3600` | Purge finished tasks and artifact files after TTL |
| `DEFAULT_BOT_DISPLAY_NAME` | `Transcription Bot` | Bot name in room |
| `PLAYWRIGHT_HEADLESS` | `true` | Headless Chromium; set `false` for local UI debugging |
| `LOG_DIR` | `./data/logs` | Join debug screenshots on timeout |
| `SQLITE_PATH` | `./data/tasks.db` | Task store (under `DATA_DIR` in Docker) |
| `LOG_LEVEL` | `info` | App log level |
| `METRICS_ENABLED` | `true` | Application Prometheus collectors |
| `ARTIFACT_SAMPLE_RATE` | `44100` | MP3 pipeline sample rate (fixed at 44100) |
| `FFMPEG_MP3_VBR_QUALITY` | `2` | libmp3lame VBR quality (`0` best … `9` worst) |

## API summary

Auth: `Authorization: Bearer <API_TOKEN>` on private endpoints.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/health` | Public; `connectors`, `workers` (max/active/available) |
| GET | `/tasks` | Probe/list tasks |
| POST | `/capture` | Start capture (**202**) |
| GET | `/tasks/{id}` | Poll status |
| POST | `/tasks/{id}/stop` | Graceful stop (**202**) |
| GET | `/tasks/{id}/download` | `.mp3` when `success` |
| DELETE | `/tasks/{id}` | Cancel without artifact |
| GET | `/metrics` | Prometheus (`METRICS_TOKEN`) |

Task statuses: `queued` → `joining` → `capturing` → `finalizing` → `success` | `error` | `canceled` (`queued` only when `WORKER_QUEUE_SIZE` > 0 and all worker slots are busy).

## Attach to idigest-hub

Instance admin → Workers → type **`capture`** → register `base_url` + `api_token` → probe `/health` → select connectors (`jitsi`).

Hub integration is implemented separately in idigest-hub.

## Metrics

```bash
curl -s -H "Authorization: Bearer $METRICS_TOKEN" http://127.0.0.1:8000/metrics
```

Details: [docs/en/operations/monitoring.md](docs/en/operations/monitoring.md)

Grafana: import [grafana/dashboards/icapture-worker.json](grafana/dashboards/icapture-worker.json)

Prometheus scrape example: [deploy/prometheus/scrape.example.yml](deploy/prometheus/scrape.example.yml)

## Docker Compose

```bash
cp .env.example .env
docker compose up --build
```

Service: `icapture-worker` (API + Playwright/Chromium in one container). The app listens on **8000 inside the container**; on the host, publish via `HOST_PORT` or `PORT` in `.env` (e.g. `HOST_PORT=9000` → `http://localhost:9000`). Do not raise in-container `PORT` just for an external port — port mapping and nginx/upstream will return **502**. Browsers are installed at image build time (`playwright install-deps` + `chromium` + `chromium-headless-shell`).

The image sets `WORKERS=2` and `WORKER_QUEUE_SIZE=0` unless overridden in `.env`.

## Typical errors

| HTTP | `error.code` | When |
|------|--------------|------|
| 401 | `unauthorized` | Bad/missing token |
| 503 | `queue_full` | No free capture slots |
| 400 | `unsupported_connector` | Unknown connector |
| 400 | `invalid_url` | Malformed meeting URL |
| 422 | `join_failed` | Cannot join room |
| 404 | `not_found` | Unknown task |
| 409 | `task_running` | Conflicting operation |

After `TASK_TTL_SEC`, finished tasks are removed from the DB and disk — `GET /tasks/{id}` and download return **404** (`not_found`).

## Tests

```bash
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pytest
```
