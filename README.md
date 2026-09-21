# icapture-worker

External capture worker for [idigest-hub](https://github.com/dude75/idigest-hub). Joins a video conference as a bot, records mixed audio, and returns an MP3 artifact (44.1 kHz, libmp3lame VBR) for the transcribe pipeline.

Russian: [README.ru.md](README.ru.md)

## What it does

1. Hub calls `POST /capture` → bot joins the room and records audio.
2. User stops capture → Hub calls `POST /tasks/{id}/stop` → worker finalizes `.mp3`.
3. Hub polls `GET /tasks/{id}` until `status=success`.
4. Hub downloads `GET /tasks/{id}/download` and continues with transcribe.

Not included in v1: lobby, live transcript, Zoom/Teams capture, browser automation (Puppeteer/Jibri).

## Requirements

- Python 3.12+
- Node.js 22+ (Jitsi engine sidecar)
- Native build tools for `wrtc` when running real Jitsi capture (see Node image)

## Architecture

```
Hub / curl  →  Python FastAPI (:8000)  →  Node Jitsi engine (:8001)  →  Jitsi Meet
               tasks, auth, metrics         join room, record audio
```

- **Python API** — HTTP for Hub, task lifecycle.
- **Node Jitsi engine** — the only part that talks to Jitsi (lib-jitsi-meet + wrtc).
- **“Node” in docs** = Node Jitsi engine (`npm start`), **not** the Jitsi server.

## Install (once)

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env
cd node/jitsi && npm install && cd ../..
```

## Run — real conference (prod / manual E2E)

**No stub flags.** Two terminals:

```bash
# Terminal 1 — Node Jitsi engine
cd node/jitsi && npm start

# Terminal 2 — Python API
./.venv/bin/python -m app.serve
```

Health check:

```bash
curl -s http://127.0.0.1:8000/health | jq
```

Example `connectors` (objects with `status`, `label`; `reason` only when `unavailable`):

```json
{
  "jitsi": { "status": "loaded", "label": "Jitsi Meet" },
  "zoom": { "status": "unavailable", "label": "Zoom", "reason": "not_implemented" }
}
```

Start capture:

```bash
curl -s -X POST http://127.0.0.1:8000/capture \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"connector":"jitsi","meeting_url":"https://meet.example.com/RoomName","pin":""}'
```

The bot joins the Jitsi room. Stop → `POST /tasks/{id}/stop` → download `.mp3`.

## What is stub

**Stub** (mock) — a mode where part of the system **pretends** to work without doing the real action.

| | Production | Stub |
|---|---|---|
| Join Jitsi | yes, bot in room | **no** |
| Audio | from conference | **sine / fake `.mp3`** |
| Node Jitsi engine | required | may be skipped |
| Use for | prod, manual E2E | **pytest, local API debugging** |

Two stub flags (do not confuse):

| Flag | Where | What it does |
|------|-------|--------------|
| `ICAPTURE_STUBS=1` | Python | Python **fakes** capture itself. Node is **not called** — no second terminal. |
| `JITSI_STUB=1` | Node (`npm start`) | Node **runs**, Python talks to it, but Node **does not join** Jitsi — writes test artifact only. |

**“Without Node”** in stub docs = Node Jitsi engine (`npm start`) is **not running**, because Python with `ICAPTURE_STUBS=1` never calls it. This is **not** the Jitsi server.

Do **not** use stub in production — Hub and users will not get a real meeting recording.

## Run — tests / development (stub)

| Mode | Python | Node | Purpose |
|------|--------|------|---------|
| **A** — pytest | `ICAPTURE_STUBS=1` | not needed | `./.venv/bin/pytest` |
| **B** — API without Node | `ICAPTURE_STUBS=1` | not needed | local HTTP pipeline check |
| **C** — Python↔Node wiring | no stub | `JITSI_STUB=1 npm start` | sidecar check without Jitsi |

**Mode B** (simplest dev):

```bash
ICAPTURE_STUBS=1 ./.venv/bin/python -m app.serve
# do not start Node
```

**Mode C** (if you need to test the Node sidecar):

```bash
# Terminal 1
cd node/jitsi && JITSI_STUB=1 npm start

# Terminal 2 — do not set ICAPTURE_STUBS
./.venv/bin/python -m app.serve
```

## `.env`

| Variable | Default | Meaning |
|----------|---------|---------|
| `API_TOKEN` | — | Bearer token for Hub → worker |
| `METRICS_TOKEN` | — | Bearer token for Prometheus scrape |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | HTTP port |
| `DATA_DIR` | `./data` | Artifacts and SQLite |
| `MAX_CONCURRENT_CAPTURES` | `2` | Parallel capture slots |
| `MAX_CAPTURE_DURATION_SEC` | `14400` | Auto-stop after 4h |
| `TASK_TTL_SEC` | `3600` | Purge finished tasks and artifact files after TTL |
| `DEFAULT_BOT_DISPLAY_NAME` | `Transcription Bot` | Bot name in room |
| `JITSI_ENGINE_URL` | `http://127.0.0.1:8001` | Node sidecar URL |
| `ICAPTURE_STUBS` | — | `1` = Python stub; Node not required (tests only) |

## API summary

Auth: `Authorization: Bearer <API_TOKEN>` on private endpoints.

| Method | Path | Notes |
|--------|------|-------|
| GET | `/health` | Public; `connectors` (status/label/reason) + `slots` |
| GET | `/ready` | 200 if free slot available |
| GET | `/tasks` | Probe/list tasks |
| POST | `/capture` | Start capture (**202**) |
| GET | `/tasks/{id}` | Poll status |
| POST | `/tasks/{id}/stop` | Graceful stop (**202**) |
| GET | `/tasks/{id}/download` | `.mp3` when `success` |
| DELETE | `/tasks/{id}` | Cancel without artifact |
| GET | `/metrics` | Prometheus (`METRICS_TOKEN`) |

Task statuses: `capturing` → `finalizing` → `success` | `error` | `canceled`.

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

Services: `icapture-worker` (API, port 8000) + `jitsi-engine` (Node, internal 8001).

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
