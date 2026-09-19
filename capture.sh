#!/usr/bin/env bash
# E2E capture test against local icapture-worker.
# Usage: ./capture.sh [meeting_url] [pin] [jwt]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# Legacy: older runs downloaded into data/ root; keep artifacts/ only.
rm -f "${ROOT}/data/capture-"*.wav 2>/dev/null || true

if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  source .env
fi

API_TOKEN="${API_TOKEN:-change-me}"
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
MEETING_URL="${1:-https://meet.realweb.ru/123}"
PIN="${2:-}"
JWT="${3:-}"
NONINTERACTIVE="${NONINTERACTIVE:-0}"
TALK_SEC="${TALK_SEC:-10}"

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required" >&2
  exit 1
fi

TASK_ID=""
CAPTURE_FINISHED=0
CLEANUP_DONE=0

cancel_capture_task() {
  if [[ "$CLEANUP_DONE" == "1" || -z "$TASK_ID" || "$CAPTURE_FINISHED" == "1" ]]; then
    return 0
  fi
  CLEANUP_DONE=1
  echo >&2
  echo "== cleanup: cancel task $TASK_ID ==" >&2
  curl -sS -X DELETE "$BASE_URL/tasks/$TASK_ID" \
    -H "Authorization: Bearer $API_TOKEN" 2>/dev/null | jq . >&2 || true
  rm -f "${ROOT}/data/artifacts/${TASK_ID}.m4a" "${ROOT}/data/artifacts/${TASK_ID}.pcm.wav"
}

on_interrupt() {
  cancel_capture_task
  exit 130
}

trap on_interrupt INT TERM

build_payload() {
  jq -n \
    --arg url "$MEETING_URL" \
    --arg pin "$PIN" \
    --arg jwt "$JWT" \
    '{
      connector: "jitsi",
      meeting_url: $url,
      pin: $pin,
      jwt: (if $jwt == "" then null else $jwt end),
      display_name: "Transcription Bot"
    }'
}

echo "== health =="
curl -fsS "$BASE_URL/health" | jq .
echo

echo "== start capture: $MEETING_URL =="
PAYLOAD=$(build_payload)
HTTP_CODE=$(curl -sS -o /tmp/icapture_resp.json -w "%{http_code}" \
  -X POST "$BASE_URL/capture" \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

cat /tmp/icapture_resp.json | jq .
if [[ "$HTTP_CODE" != "202" ]]; then
  echo "capture failed: HTTP $HTTP_CODE" >&2
  exit 1
fi

RESP=$(cat /tmp/icapture_resp.json)
TASK_ID=$(echo "$RESP" | jq -r '.meta.task_id')
STATUS=$(echo "$RESP" | jq -r '.status')

if [[ "$STATUS" == "error" ]] || echo "$RESP" | jq -e '.error' >/dev/null 2>&1; then
  echo "capture returned error status" >&2
  exit 1
fi

echo
echo "task_id=$TASK_ID status=$STATUS"

echo "== verify task in GET /tasks =="
if ! curl -fsS "$BASE_URL/tasks" -H "Authorization: Bearer $API_TOKEN" | jq -e --arg id "$TASK_ID" '.[] | select(.task_id == $id)' >/dev/null; then
  echo "task $TASK_ID not found in GET /tasks" >&2
  exit 1
fi
curl -fsS "$BASE_URL/tasks" -H "Authorization: Bearer $API_TOKEN" | jq --arg id "$TASK_ID" '.[] | select(.task_id == $id)'

poll_task_status() {
  curl -fsS "$BASE_URL/tasks/$TASK_ID" -H "Authorization: Bearer $API_TOKEN"
}

PSTATUS="capturing"
if [[ "$NONINTERACTIVE" == "1" ]]; then
  echo "recording up to ${TALK_SEC}s (poll every 2s; kick → success when artifact ready)..."
  DEADLINE=$(($(date +%s) + TALK_SEC))
  while [[ $(date +%s) -lt $DEADLINE ]]; do
    POLL=$(poll_task_status)
    PSTATUS=$(echo "$POLL" | jq -r '.status')
    echo "status=$PSTATUS"
    if [[ "$PSTATUS" == "success" || "$PSTATUS" == "error" ]]; then
      echo "$POLL" | jq .
      break
    fi
    sleep 2
  done
else
  echo "Join and talk. Enter = stop. Kick/auto-finish detected via poll (Ctrl+C cancels)..."
  while [[ "$PSTATUS" != "success" && "$PSTATUS" != "error" ]]; do
    if read -r -t 2 _; then
      break
    fi
    POLL=$(poll_task_status)
    PSTATUS=$(echo "$POLL" | jq -r '.status')
    if [[ "$PSTATUS" == "success" || "$PSTATUS" == "error" ]]; then
      echo "status=$PSTATUS (session ended)"
      echo "$POLL" | jq .
      break
    fi
  done
fi

if [[ "$PSTATUS" != "success" && "$PSTATUS" != "error" ]]; then
  echo "== stop =="
  curl -fsS -X POST "$BASE_URL/tasks/$TASK_ID/stop" \
    -H "Authorization: Bearer $API_TOKEN" | jq .

  echo "== poll until success =="
  for _ in $(seq 1 60); do
    POLL=$(poll_task_status)
    PSTATUS=$(echo "$POLL" | jq -r '.status')
    echo "status=$PSTATUS"
    if [[ "$PSTATUS" == "success" || "$PSTATUS" == "error" ]]; then
      echo "$POLL" | jq .
      break
    fi
    sleep 1
  done
fi

if [[ "$PSTATUS" != "success" ]]; then
  echo "task did not succeed" >&2
  exit 1
fi

OUT="${ROOT}/data/artifacts/${TASK_ID}.m4a"
echo "== artifact at $OUT =="
if [[ ! -f "$OUT" ]]; then
  echo "artifact missing, downloading via API..." >&2
  curl -fsS "$BASE_URL/tasks/$TASK_ID/download" \
    -H "Authorization: Bearer $API_TOKEN" \
    -o "$OUT"
fi
ls -lh "$OUT"
CAPTURE_FINISHED=1
trap - INT TERM
echo "done"
