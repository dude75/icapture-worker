# Коннекторы захвата

Worker принимает `connector` и полный `meeting_url` в `POST /capture`. Жизненный цикл задачи одинаков для всех реализованных коннекторов: `queued` → `joining` → `capturing` → `finalizing` → `success` (или `error` / `canceled`).

Включение: `ENABLED_CONNECTORS` (через запятую, без пробелов или с пробелами после запятой — trim).

## Jitsi Meet (`jitsi`)

- URL: `https://<host>/<room>` (последний сегмент path — имя комнаты).
- `pin` — пароль lobby Jitsi, если включён на инстансе.
- Join: web UI, prejoin по возможности, микрофон muted.
- Join timeout: `CONF_JOIN_TIMEOUT_SEC` (по умолчанию 180 с) — prejoin, ожидание модератора, lobby; иначе `join_failed`.
- Auto-stop: события JitsiMeetJS, kick по тексту страницы, `conference_left`, `MAX_CAPTURE_DURATION_SEC`.

## Yandex Telemost (`telemost`)

- URL: гостевая ссылка `https://telemost.yandex.ru/j/<meeting_id>` или `https://telemost.yandex.ru/private-join/<meeting_id>`.
- `pin` не используется.
- Join: переход на `private-join` с `mic=off` и `camera=off`; бот только пишет удалённое аудио.
- Комната ожидания: тот же `CONF_JOIN_TIMEOUT_SEC`; модератор должен впустить бота.
- Опционально: `TELEMOST_STORAGE_STATE` — файл Playwright storage для сессии Yandex.
- Auto-stop: kick / end / leave по UI Telemost, падение `sinkCount`, те же finalize и MP3, что у Jitsi.

## PCM и артефакт

Для Jitsi и Telemost:

1. В странице `browser_init.js` копит Float32; `flushInt16Pcm()` отдаёт int16 и **очищает** буфер.
2. Worker каждые `CAPTURE_PCM_SPOOL_INTERVAL_SEC` снимает PCM и дописывает `{DATA_DIR}/tmp/{task_id}.pcm`.
3. При stop / auto-stop — последний drain, ffmpeg → `{DATA_DIR}/artifacts/{task_id}.mp3`, временный `.pcm` удаляется.

## SPIKE (ручная проверка Telemost)

```bash
export TELEMOST_MEETING_URL='https://telemost.yandex.ru/j/...'
export PLAYWRIGHT_HEADLESS=false
./.venv/bin/python scripts/spike_telemost_join.py
```

Не входит в `pytest` (маркер `spike`). Production-путь — `POST /capture` с `connector: telemost`.
