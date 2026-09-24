#!/usr/bin/env python3
"""
SPIKE: join Yandex Telemost via guest link and verify WebRTC audio capture (__icapture).

Not part of default pytest. Requires network + a live meeting URL.

Usage:
  export TELEMOST_MEETING_URL='https://telemost.yandex.ru/j/XXXXXXXXXXXX'
  export TELEMOST_DISPLAY_NAME='Transcription Bot'   # optional
  export PLAYWRIGHT_HEADLESS=false                   # recommended first run
  export SPIKE_JOIN_TIMEOUT_SEC=180                  # waiting room
  export SPIKE_DURATION_SEC=45                       # listen after join
  export TELEMOST_STORAGE_STATE=./data/telemost-auth.json  # optional Yandex session
  export SPIKE_WATCH=1                                 # visible browser + slow steps + hold open
  ./.venv/bin/python scripts/spike_telemost_join.py

Screenshots: LOG_DIR/spike-telemost-*.png (default ./data/logs).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_INIT_SCRIPT = _REPO_ROOT / "app" / "capture" / "browser_init.js"

from app.capture.playwright_media import (  # noqa: E402
    CHROMIUM_CAPTURE_ARGS,
    prepare_media_capture_scripts,
)

# Bot only captures remote audio — never publish camera/video.
_SPIKE_AUDIO_ONLY_INIT_JS = """
(() => {
  if (window.__icaptureTelemostAudioOnly) {
    return;
  }
  window.__icaptureTelemostAudioOnly = true;
  const media = navigator.mediaDevices;
  if (!media?.getUserMedia) {
    return;
  }
  const original = media.getUserMedia.bind(media);
  media.getUserMedia = async (constraints) => {
    const wantAudio = Boolean(constraints?.audio);
    const audioOnly = wantAudio ? { audio: constraints.audio === Object(constraints.audio) ? constraints.audio : true, video: false } : { audio: false, video: false };
    if (!wantAudio) {
      return new MediaStream([]);
    }
    try {
      return await original(audioOnly);
    } catch (_err) {
      const ctx = new AudioContext();
      const dest = ctx.createMediaStreamDestination();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      gain.gain.value = 0;
      osc.connect(gain);
      gain.connect(dest);
      osc.start();
      for (const track of dest.stream.getAudioTracks()) {
        track.enabled = false;
      }
      return dest.stream;
    }
  };
})();
"""

_RE_BROWSER = re.compile(
    r"(продолжить\s+в\s+браузере|continue\s+in\s+(?:the\s+)?browser)",
    re.I,
)
_RE_CANCEL_APP = re.compile(r"^(отмена|cancel)$", re.I)
_RE_CONNECT = re.compile(r"^подключиться$", re.I)
_RE_CONTINUE = re.compile(r"^продолжить$", re.I)
_RE_DISMISS_NEWS = re.compile(r"^звучит отлично$", re.I)
_RE_GOT_IT = re.compile(r"^понятно$", re.I)
_RE_NAME = re.compile(r"(имя|name|как\s+вас\s+зовут|your\s+name)", re.I)

_DRAIN_PCM_JS = """
() => {
  const cap = window.__icapture;
  if (!cap?.flushInt16Pcm) {
    return { pcm: [], samples: 0, sinks: 0 };
  }
  return {
    pcm: Array.from(cap.flushInt16Pcm()),
    samples: cap.sampleCount?.() ?? 0,
    sinks: cap.sinkCount?.() ?? 0,
  };
}
"""

_MEETING_STATE_JS = """
() => {
  const cap = window.__icapture;
  const text = document.body?.innerText ?? "";
  const waiting = /комнат[ае][\\s\\S]{0,40}ожидан|ожидайте[\\s\\S]{0,80}организатор|waiting\\s+room/i.test(
    text,
  );
  const ended = /встреча\\s+заверш|meeting\\s+ended|эта\\s+встреча\\s+оконч/i.test(text);
  const loginRequired = /войдите\\s+в\\s+аккаунт/i.test(text);
  let leaveVisible = false;
  let prejoinConnect = false;
  const labelOf = (el) =>
    (el.getAttribute("aria-label") || el.innerText || el.textContent || "")
      .replace(/\\s+/g, " ")
      .trim();
  const clickables = document.querySelectorAll(
    'button, a, [role="button"], [role="link"], span[tabindex]',
  );
  for (const el of clickables) {
    const label = labelOf(el);
    if (/^(выйти|leave|покинуть|отключиться|завершить звонок)$/i.test(label)) {
      leaveVisible = true;
    }
    if (/^подключиться$/i.test(label)) {
      prejoinConnect = true;
    }
  }
  if (!prejoinConnect && /\\bподключиться\\b/i.test(text)) {
    prejoinConnect = true;
  }
  const sinks = cap?.sinkCount?.() ?? 0;
  const inMeeting =
    !waiting &&
    !ended &&
    !prejoinConnect &&
    (leaveVisible || sinks > 0 || /\\/conference\\//i.test(location.pathname + location.href));
  return {
    waiting,
    ended,
    leaveVisible,
    prejoinConnect,
    inMeeting,
    loginRequired,
    sinks,
    url: location.href,
  };
}
"""

_KICK_CHECK_JS = """
() => {
  const text = document.body?.innerText ?? "";
  if (
    /\\b(исключен|исключён|исключили|удален из конференции|удалён из конференции|вы\\s+исключен|вы\\s+исключён|you have been removed|you have been kicked|removed from the conference|kicked|organizer removed you)\\b/i.test(
      text,
    )
  ) {
    return "kicked";
  }
  if (/больше\\s+не\\s+участ|вас\\s+отключ|access\\s+denied/i.test(text)) {
    return "kicked";
  }
  if (/встреча\\s+заверш|meeting\\s+ended|эта\\s+встреча\\s+оконч/i.test(text)) {
    return "conference_ended";
  }
  return null;
}
"""


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(_REPO_ROOT / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return float(raw)


async def _hold_for_inspection(*, watch: bool, hold_sec: float) -> None:
    if watch:
        print(
            "\nSPIKE_WATCH: окно браузера остаётся открытым. "
            "Посмотрите UI, при необходимости кликните вручную.",
            flush=True,
        )
    if hold_sec > 0:
        print(f"Ждём {hold_sec:.0f}s перед закрытием (SPIKE_HOLD_OPEN_SEC)...", flush=True)
        await asyncio.sleep(hold_sec)
        return
    if watch:
        if sys.stdin.isatty():
            await asyncio.to_thread(
                input,
                "Enter — закрыть браузер и выйти из spike: ",
            )
        else:
            fallback = _env_float("SPIKE_HOLD_OPEN_SEC", 120)
            print(f"нет интерактивного терминала — держим окно {fallback:.0f}s", flush=True)
            await asyncio.sleep(fallback)


def validate_telemost_url(meeting_url: str) -> str:
    raw = meeting_url.strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("url must use http or https")
    host = (parsed.hostname or "").lower()
    if host not in {"telemost.yandex.ru", "telemost.yandex.com"}:
        raise ValueError(f"unexpected host {host!r}, expected telemost.yandex.ru")
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) >= 2 and parts[0] == "j" and parts[1]:
        return raw
    if len(parts) >= 2 and parts[0] == "private-join" and parts[1]:
        return raw
    raise ValueError("path must be /j/<meeting_id> or /private-join/<meeting_id>")


def telemost_meeting_id(meeting_url: str) -> str:
    parts = [p for p in urlparse(meeting_url).path.strip("/").split("/") if p]
    if parts[0] in {"j", "private-join"}:
        return parts[1]
    return parts[-1]


def build_spike_private_join_url(meeting_url: str) -> str:
    """Open prejoin UI directly; mic on (Telemost may ask), camera off — we only capture remote audio."""
    meeting_id = telemost_meeting_id(meeting_url)
    params = (
        "noise_cancellation_type=disabled"
        "&virtual_background_type=none"
        "&show_my_video=0"
        "&hide_participants_video=0"
        "&call_type=conference"
        "&video_facing_mode=user"
        "&lang=ru"
        "&theme=void"
        "&sync_media_devices=1"
        "&mic=off"
        "&camera=off"
    )
    return f"https://telemost.yandex.ru/private-join/{meeting_id}?{params}"


async def _resolve_join_surface(page):
    """Frame or page where private-join UI lives."""
    for frame in page.frames:
        if "private-join" in frame.url:
            return frame
    return page.main_frame


async def _screenshot(page, log_dir: Path, tag: str) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"spike-telemost-{tag}-{int(time.time())}.png"
    await page.screenshot(path=str(path), full_page=True)
    print(f"screenshot {path}")
    return path


async def _click_first(
    page,
    locator,
    *,
    timeout_ms: int = 800,
    force: bool = False,
) -> bool:
    try:
        target = locator.first
        if await target.is_visible(timeout=timeout_ms):
            await target.click(timeout=10_000, force=force)
            return True
    except Exception:
        pass
    return False


async def _dismiss_overlays(page) -> None:
    news = [
        page.get_by_role("button", name=_RE_DISMISS_NEWS),
        page.get_by_text("Звучит отлично", exact=True),
        page.locator('button:has-text("Звучит отлично")'),
    ]
    for loc in news:
        if await _click_first(page, loc):
            await asyncio.sleep(0.5)
            break
    await _click_first(page, page.get_by_role("button", name=_RE_BROWSER))
    await _click_first(page, page.get_by_role("button", name=_RE_CANCEL_APP))
    await _click_first(page, page.get_by_role("button", name=_RE_GOT_IT))
    await _click_first(page, page.get_by_text("Понятно", exact=True))


_CLICK_CONNECT_JS = """
() => {
  const norm = (s) => (s || "").replace(/\\s+/g, " ").trim();
  for (const el of document.querySelectorAll('button, [role="button"], a[role="button"]')) {
    const text = norm(el.innerText);
    const aria = norm(el.getAttribute("aria-label"));
    if (!/^подключиться$/i.test(text) && !/^подключиться$/i.test(aria)) {
      continue;
    }
    el.scrollIntoView({ block: "center", inline: "center" });
    el.click();
    return { ok: true, tag: el.tagName, text, aria };
  }
  return { ok: false };
}
"""


async def _scroll_prejoin(page) -> None:
    with contextlib.suppress(Exception):
        await page.mouse.wheel(0, 400)
    with contextlib.suppress(Exception):
        await page.evaluate(
            """() => {
              const norm = (s) => (s || "").replace(/\\s+/g, " ").trim();
              const el = [...document.querySelectorAll(
                'button, a, [role="button"], [role="link"], span[tabindex]',
              )].find((n) => /^подключиться$/i.test(norm(n.innerText || n.textContent)));
              el?.scrollIntoView({ block: "center", inline: "center" });
            }"""
        )


async def _grant_spike_audio_permissions(context, origin: str) -> None:
    with contextlib.suppress(Exception):
        await context.grant_permissions(["microphone"], origin=origin)


async def _grant_spike_audio_cdp(page, origin: str) -> None:
    with contextlib.suppress(Exception):
        session = await page.context.new_cdp_session(page)
        await session.send(
            "Browser.grantPermissions",
            {"origin": origin, "permissions": ["audioCapture"]},
        )


async def _click_connect(surface) -> bool:
    with contextlib.suppress(Exception):
        result = await surface.evaluate(_CLICK_CONNECT_JS)
        if result.get("ok"):
            print(
                f"clicked Подключиться (dom) tag={result.get('tag')} "
                f"text={result.get('text')!r}",
                flush=True,
            )
            return True
    root = surface.page if hasattr(surface, "page") else surface
    locators = [
        root.get_by_role("button", name=_RE_CONNECT),
        root.get_by_text("Подключиться", exact=True),
        root.locator('button:has-text("Подключиться")'),
    ]
    for loc in locators:
        if await _click_first(root, loc, timeout_ms=1200):
            print("clicked Подключиться (playwright locator)", flush=True)
            return True
    result = {}
    with contextlib.suppress(Exception):
        result = await surface.evaluate(_CLICK_CONNECT_JS) or {}
    if result.get("ok"):
        print(
            f"clicked Подключиться (dom) tag={result.get('tag')} text={result.get('text')!r}",
            flush=True,
        )
        return True
    target = await surface.evaluate(
        """
        () => {
          const norm = (s) => (s || "").replace(/\\s+/g, " ").trim();
          const all = [...document.querySelectorAll("*")];
          for (const el of all) {
            const text = norm(el.textContent);
            if (text !== "Подключиться" && !/^подключиться$/i.test(norm(el.innerText))) {
              continue;
            }
            const node =
              el.closest('button,a,[role="button"],[role="link"]') || el;
            const r = node.getBoundingClientRect();
            if (r.width < 8 || r.height < 8) {
              continue;
            }
            return {
              x: r.x + r.width / 2,
              y: r.y + r.height / 2,
              tag: node.tagName,
            };
          }
          return null;
        }
        """
    )
    if target:
        await root.mouse.click(float(target["x"]), float(target["y"]))
        print(
            f"clicked Подключиться (mouse) tag={target.get('tag')} "
            f"@({target.get('x'):.0f},{target.get('y'):.0f})",
            flush=True,
        )
        return True
    hints = await surface.evaluate(
        """
        () => [...document.querySelectorAll("button,a,[role=button]")]
          .map(el => (el.innerText || el.getAttribute("aria-label") || "").trim())
          .filter(t => t.length > 0)
          .slice(0, 20)
        """
    )
    print(f"connect not clicked; visible controls: {hints!r}", flush=True)
    return False


async def _telemost_join_step(page, display_name: str, *, connect: bool = True) -> None:
    surface = await _resolve_join_surface(page)
    await _dismiss_overlays(page)
    await _scroll_prejoin(page)

    name_fields = [
        page.get_by_placeholder(_RE_NAME),
        page.get_by_label(_RE_NAME),
        page.locator('input[type="text"]'),
        page.locator('[contenteditable="true"]'),
    ]
    for loc in name_fields:
        try:
            field = loc.first
            if await field.is_visible(timeout=400):
                await field.click(timeout=3000)
                await field.fill(display_name)
                break
        except Exception:
            continue

    if not await _click_first(page, page.get_by_role("button", name=_RE_BROWSER)):
        await _click_first(page, page.get_by_role("button", name=_RE_CONTINUE))
    if connect:
        await _click_connect(surface)


async def run_spike() -> int:
    _load_dotenv()
    meeting_url = os.environ.get("TELEMOST_MEETING_URL", "").strip()
    if not meeting_url:
        print("Set TELEMOST_MEETING_URL to a full guest link.", file=sys.stderr)
        return 2

    try:
        meeting_url = validate_telemost_url(meeting_url)
    except ValueError as exc:
        print(f"invalid TELEMOST_MEETING_URL: {exc}", file=sys.stderr)
        return 2

    display_name = os.environ.get("TELEMOST_DISPLAY_NAME", "Transcription Bot").strip()
    log_dir = Path(os.environ.get("LOG_DIR", str(_REPO_ROOT / "data" / "logs")))
    join_timeout = float(os.environ.get("SPIKE_JOIN_TIMEOUT_SEC", "180"))
    listen_sec = float(os.environ.get("SPIKE_DURATION_SEC", "45"))
    watch = _env_bool("SPIKE_WATCH", False)
    record_mp3 = _env_bool("SPIKE_RECORD_MP3", True)
    headless = _env_bool("PLAYWRIGHT_HEADLESS", False if watch else True)
    if watch:
        headless = False
    slow_mo = int(_env_float("SPIKE_SLOW_MO_MS", 750 if watch else 0))
    hold_open = _env_float("SPIKE_HOLD_OPEN_SEC", 0 if watch else 0)
    use_cdp = _env_bool("TELEMOST_CDP_GRANT", True)
    gum_fallback = _env_bool("TELEMOST_GUM_FALLBACK", True)
    parsed_origin = f"{urlparse(meeting_url).scheme}://{urlparse(meeting_url).netloc}"

    if not _INIT_SCRIPT.is_file():
        print(f"missing {_INIT_SCRIPT}", file=sys.stderr)
        return 2

    from playwright.async_api import async_playwright

    init_script = _INIT_SCRIPT.read_text(encoding="utf-8")
    deadline = time.monotonic() + join_timeout
    joined = False
    max_sinks = 0
    total_pcm = 0
    stop_reason: str | None = None

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=headless,
            slow_mo=slow_mo,
            args=list(CHROMIUM_CAPTURE_ARGS),
        )
        context_kwargs: dict = {
            "ignore_https_errors": True,
            "viewport": {"width": 1280, "height": 720},
            "locale": "ru-RU",
        }
        storage = os.environ.get("TELEMOST_STORAGE_STATE", "").strip()
        if storage:
            path = Path(storage)
            if not path.is_file():
                print(f"TELEMOST_STORAGE_STATE not found: {path}", file=sys.stderr)
                return 2
            context_kwargs["storage_state"] = str(path)
        context = await browser.new_context(**context_kwargs)
        await context.add_init_script(_SPIKE_AUDIO_ONLY_INIT_JS)
        await prepare_media_capture_scripts(
            context,
            capture_init_js=init_script,
            gum_fallback=gum_fallback,
        )
        page = await context.new_page()
        await _grant_spike_audio_permissions(context, parsed_origin)
        if use_cdp:
            await _grant_spike_audio_cdp(page, parsed_origin)

        navigate_url = build_spike_private_join_url(meeting_url)
        print(
            f"goto {navigate_url} headless={headless} watch={watch} slow_mo={slow_mo}ms "
            f"cdp_grant={use_cdp} gum_fallback={gum_fallback}",
            flush=True,
        )
        await page.goto(navigate_url, wait_until="domcontentloaded", timeout=60_000)
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=45_000)
        await asyncio.sleep(2.0)
        await _screenshot(page, log_dir, "01-after-goto")

        login_note = False
        connect_attempts = 0
        post_connect_ticks = 0
        while time.monotonic() < deadline:
            if page.is_closed():
                print("browser closed", flush=True)
                break
            surface = await _resolve_join_surface(page)
            state = await surface.evaluate(_MEETING_STATE_JS)
            if watch:
                print(
                    f"state prejoin={state.get('prejoinConnect')} "
                    f"inMeeting={state.get('inMeeting')} waiting={state.get('waiting')} "
                    f"loginUpsell={state.get('loginRequired')}",
                    flush=True,
                )
            if state.get("loginRequired") and not login_note:
                login_note = True
                print(
                    "note: «Войдите в аккаунт» — часто апсell; ищем «Подключиться»",
                    flush=True,
                )
            if state.get("prejoinConnect"):
                print("step: overlays → имя → Подключиться", flush=True)
            if state.get("waiting"):
                print("waiting room — впустите бота в UI модератора", flush=True)
            if state.get("inMeeting"):
                joined = True
                break
            if connect_attempts > 0 and not state.get("prejoinConnect") and not state.get("waiting"):
                post_connect_ticks += 1
                if post_connect_ticks >= 2:
                    joined = True
                    print("joined (post-connect, prejoin cleared)", flush=True)
                    break
            try_connect = state.get("prejoinConnect") and connect_attempts < 3
            if try_connect:
                connect_attempts += 1
            await _telemost_join_step(page, display_name, connect=try_connect)
            await asyncio.sleep(1.5 if watch else 1.0)

        exit_code = 0
        if not joined:
            await _screenshot(page, log_dir, "join-timeout")
            print("SPIKE FAIL: join timeout (смотрите окно браузера)", file=sys.stderr)
            exit_code = 1
        else:
            surface = await _resolve_join_surface(page)
            await surface.evaluate(
                """async () => {
                  const cap = window.__icapture;
                  if (cap?.resume) await cap.resume();
                }"""
            )
            await _screenshot(page, log_dir, "02-joined")
            state = await surface.evaluate(_MEETING_STATE_JS)
            print(f"joined url={state.get('url')} sinks={state.get('sinks')}", flush=True)

            pcm_path: Path | None = None
            mp3_path: Path | None = None
            if record_mp3:
                pcm_path = log_dir / f"spike-telemost-{int(time.time())}.pcm"
                from app.artifacts import append_pcm_samples, convert_pcm_to_mp3

            listen_deadline = time.monotonic() + listen_sec
            print(f"recording audio {listen_sec:.0f}s → spike MP3", flush=True)

            async def _finalize_spike_mp3() -> None:
                if pcm_path is None or not record_mp3:
                    return
                if not page.is_closed():
                    with contextlib.suppress(Exception):
                        surface = await _resolve_join_surface(page)
                        drained = await surface.evaluate(_DRAIN_PCM_JS)
                        pcm_list = drained.get("pcm") or []
                        if pcm_list:
                            await asyncio.to_thread(append_pcm_samples, pcm_path, pcm_list)
                if pcm_path.is_file() and pcm_path.stat().st_size > 0:
                    out = pcm_path.with_suffix(".mp3")
                    await asyncio.to_thread(convert_pcm_to_mp3, pcm_path, out)
                    if not _env_bool("SPIKE_KEEP_PCM", False):
                        pcm_path.unlink(missing_ok=True)
                    print(f"SPIKE artifact: {out}", flush=True)
                else:
                    print("SPIKE: no PCM written (speak in meeting or check sinks)", flush=True)

            was_in_meeting = True
            zero_sink_ticks = 0
            try:
                while time.monotonic() < listen_deadline:
                    if page.is_closed():
                        stop_reason = "page_closed"
                        break
                    surface = await _resolve_join_surface(page)
                    kick = await surface.evaluate(_KICK_CHECK_JS)
                    if kick:
                        stop_reason = str(kick)
                        print(f"auto-stop: {stop_reason}", flush=True)
                        break
                    meet = await surface.evaluate(_MEETING_STATE_JS)
                    in_call = bool(meet.get("inMeeting"))
                    sinks_now = int(meet.get("sinks") or 0)
                    if was_in_meeting and meet.get("prejoinConnect"):
                        stop_reason = "kicked"
                        print("auto-stop: kicked (back on prejoin)", flush=True)
                        break
                    if was_in_meeting and meet.get("ended"):
                        stop_reason = "conference_ended"
                        print(f"auto-stop: {stop_reason}", flush=True)
                        break
                    if max_sinks > 0 and sinks_now == 0:
                        zero_sink_ticks += 1
                        if zero_sink_ticks >= 3:
                            stop_reason = "conference_left"
                            print("auto-stop: conference_left (sinks dropped)", flush=True)
                            break
                    else:
                        zero_sink_ticks = 0
                    if was_in_meeting and not in_call and not meet.get("prejoinConnect"):
                        stop_reason = "conference_left"
                        print(f"auto-stop: {stop_reason}", flush=True)
                        break
                    was_in_meeting = was_in_meeting or in_call
                    drained = await surface.evaluate(_DRAIN_PCM_JS)
                    pcm_list = drained.get("pcm") or []
                    pcm_len = len(pcm_list)
                    total_pcm += pcm_len
                    sinks = int(drained.get("sinks") or 0)
                    max_sinks = max(max_sinks, sinks)
                    if pcm_path is not None and pcm_list:
                        await asyncio.to_thread(append_pcm_samples, pcm_path, pcm_list)
                    if pcm_len or sinks:
                        print(
                            f"capture sinks={sinks} pcm={pcm_len} total_pcm={total_pcm}",
                            flush=True,
                        )
                    await asyncio.sleep(1.0)
            finally:
                await _screenshot(page, log_dir, "03-after-listen")
                await _finalize_spike_mp3()

        if watch or hold_open > 0 or exit_code != 0:
            await _hold_for_inspection(watch=watch or exit_code != 0, hold_sec=hold_open)

        await browser.close()

    print("--- spike summary ---")
    print(
        f"joined={joined} max_sinks={max_sinks} total_pcm_samples={total_pcm} "
        f"stop_reason={stop_reason or ('listen_timeout' if joined else 'join_failed')}",
    )
    if joined and max_sinks > 0:
        print("SPIKE OK: in meeting and remote WebRTC tracks attached")
        return 0 if exit_code == 0 else exit_code
    if joined and total_pcm > 0:
        print(
            "SPIKE PARTIAL: in meeting, PCM moving but sinkCount=0 — "
            "verify remote audio (speak in room) or inspect capture path",
        )
        return 0 if exit_code == 0 else exit_code
    if joined:
        print("SPIKE PARTIAL: joined UI detected but no PCM/sinks yet")
        return 0 if exit_code == 0 else exit_code
    return exit_code


def main() -> None:
    raise SystemExit(asyncio.run(run_spike()))


if __name__ == "__main__":
    main()
