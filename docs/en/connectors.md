# Capture connectors

The worker accepts `connector` and a full `meeting_url` in `POST /capture`. Task lifecycle is the same for all implemented connectors: `queued` → `joining` → `capturing` → `finalizing` → `success` (or `error` / `canceled`).

Enable connectors via `ENABLED_CONNECTORS` (comma-separated list).

## Jitsi Meet (`jitsi`)

- URL: `https://<host>/<room>` (last path segment is the room name).
- `pin` — Jitsi lobby password when required by the deployment.
- Join: web UI, prejoin skipped where possible, mic muted.
- Join timeout: `CONF_JOIN_TIMEOUT_SEC` (default 180s) — prejoin, moderator wait, lobby; otherwise `join_failed`.
- Auto-stop: JitsiMeetJS events, kick heuristics, `conference_left`, `MAX_CAPTURE_DURATION_SEC`.

## Yandex Telemost (`telemost`)

- URL: guest link `https://telemost.yandex.ru/j/<meeting_id>` or `https://telemost.yandex.ru/private-join/<meeting_id>`.
- `pin` is ignored.
- Join: navigates to `private-join` with `mic=off` and `camera=off`; only remote audio is captured.
- Waiting room: same `CONF_JOIN_TIMEOUT_SEC`; a moderator must admit the bot.
- Optional: `TELEMOST_STORAGE_STATE` — Playwright storage file for a Yandex session.
- Auto-stop: Telemost UI heuristics (kick / end / leave), sink drop, same finalize → MP3 as Jitsi.

## PCM and artifacts

For both Jitsi and Telemost:

1. In-page `browser_init.js` buffers Float32; `flushInt16Pcm()` returns int16 and **clears** the buffer.
2. The worker drains on `CAPTURE_PCM_SPOOL_INTERVAL_SEC` and appends to `{DATA_DIR}/tmp/{task_id}.pcm`.
3. On stop / auto-stop — final drain, ffmpeg → `{DATA_DIR}/artifacts/{task_id}.mp3`, then remove the temp `.pcm`.

## SPIKE (manual Telemost check)

```bash
export TELEMOST_MEETING_URL='https://telemost.yandex.ru/j/...'
export PLAYWRIGHT_HEADLESS=false
./.venv/bin/python scripts/spike_telemost_join.py
```

Not part of `pytest` (`spike` marker). Production path: `POST /capture` with `connector: telemost`.
