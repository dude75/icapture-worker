"""Yandex Telemost guest join, meeting state, and disconnect detection."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from app.url_parser import InvalidMeetingUrl

logger = logging.getLogger("app")

TELEMOST_AUDIO_ONLY_INIT_JS = """
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
    const audioOnly = wantAudio
      ? {
          audio:
            constraints.audio === Object(constraints.audio) ? constraints.audio : true,
          video: false,
        }
      : { audio: false, video: false };
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

MEETING_STATE_JS = """
() => {
  const cap = window.__icapture;
  const text = document.body?.innerText ?? "";
  const testReady = document.querySelector('[data-testid="icapture-test-ready"]');
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
    Boolean(testReady) ||
    (!waiting &&
      !ended &&
      !prejoinConnect &&
      (leaveVisible || sinks > 0 || /\\/conference\\//i.test(location.pathname + location.href)));
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

KICK_CHECK_JS = """
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


def validate_telemost_meeting_url(meeting_url: str) -> str:
    raw = meeting_url.strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise InvalidMeetingUrl("url must use http or https")
    host = (parsed.hostname or "").lower()
    if host not in {"telemost.yandex.ru", "telemost.yandex.com"}:
        raise InvalidMeetingUrl(f"unexpected host {host!r}, expected telemost.yandex.ru")
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) >= 2 and parts[0] in {"j", "private-join"} and parts[1]:
        return raw
    raise InvalidMeetingUrl("path must be /j/<meeting_id> or /private-join/<meeting_id>")


def telemost_meeting_id(meeting_url: str) -> str:
    parts = [p for p in urlparse(meeting_url).path.strip("/").split("/") if p]
    if parts[0] in {"j", "private-join"}:
        return parts[1]
    return parts[-1]


def build_private_join_url(meeting_url: str) -> str:
    """Direct prejoin UI; mic/camera off — bot only captures remote audio."""
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


def telemost_origin(meeting_url: str) -> str:
    parsed = urlparse(meeting_url.strip())
    return f"{parsed.scheme}://{parsed.netloc}"


async def resolve_join_surface(page):
    for frame in page.frames:
        if "private-join" in frame.url:
            return frame
    return page.main_frame


async def _click_first(page, locator, *, timeout_ms: int = 800, force: bool = False) -> bool:
    try:
        target = locator.first
        if await target.is_visible(timeout=timeout_ms):
            await target.click(timeout=10_000, force=force)
            return True
    except Exception:
        pass
    return False


async def dismiss_overlays(page) -> None:
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


async def scroll_prejoin(page) -> None:
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


async def click_connect(surface) -> bool:
    with contextlib.suppress(Exception):
        result = await surface.evaluate(_CLICK_CONNECT_JS)
        if result.get("ok"):
            return True
    root = surface.page if hasattr(surface, "page") else surface
    locators = [
        root.get_by_role("button", name=_RE_CONNECT),
        root.get_by_text("Подключиться", exact=True),
        root.locator('button:has-text("Подключиться")'),
    ]
    for loc in locators:
        if await _click_first(root, loc, timeout_ms=1200):
            return True
    result = {}
    with contextlib.suppress(Exception):
        result = await surface.evaluate(_CLICK_CONNECT_JS) or {}
    if result.get("ok"):
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
        return True
    return False


async def join_step(page, display_name: str, *, connect: bool = True) -> None:
    surface = await resolve_join_surface(page)
    await dismiss_overlays(page)
    await scroll_prejoin(page)

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
        await click_connect(surface)


async def join_telemost(
    page,
    *,
    display_name: str,
    timeout_sec: float,
    log_dir: Path | None = None,
    task_id: str | None = None,
) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_sec
    connect_attempts = 0
    post_connect_ticks = 0
    while asyncio.get_event_loop().time() < deadline:
        if page.is_closed():
            raise TimeoutError("browser closed during telemost join")
        surface = await resolve_join_surface(page)
        state = await surface.evaluate(MEETING_STATE_JS)
        if state.get("waiting"):
            logger.info("telemost waiting room task=%s — admit bot in moderator UI", task_id)
        if state.get("inMeeting"):
            return
        if connect_attempts > 0 and not state.get("prejoinConnect") and not state.get("waiting"):
            post_connect_ticks += 1
            if post_connect_ticks >= 2:
                logger.info("telemost joined (post-connect) task=%s", task_id)
                return
        try_connect = state.get("prejoinConnect") and connect_attempts < 3
        if try_connect:
            connect_attempts += 1
        await join_step(page, display_name, connect=try_connect)
        await asyncio.sleep(1.0)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        tag = task_id or "join"
        path = log_dir / f"telemost-join-timeout-{tag}.png"
        with contextlib.suppress(Exception):
            await page.screenshot(path=str(path), full_page=True)
            logger.warning("telemost join timeout screenshot %s", path)
    raise TimeoutError("telemost conference join timeout")


async def wait_for_telemost_disconnect(page, poll_interval_sec: float = 1.0) -> str:
    was_in_meeting = False
    zero_sink_ticks = 0
    while True:
        if page.is_closed():
            return "page_closed"
        surface = await resolve_join_surface(page)
        kick = await surface.evaluate(KICK_CHECK_JS)
        if kick:
            return str(kick)
        meet = await surface.evaluate(MEETING_STATE_JS)
        in_call = bool(meet.get("inMeeting"))
        sinks_now = int(meet.get("sinks") or 0)
        if was_in_meeting and meet.get("prejoinConnect"):
            return "kicked"
        if was_in_meeting and meet.get("ended"):
            return "conference_ended"
        if was_in_meeting and sinks_now == 0:
            zero_sink_ticks += 1
            if zero_sink_ticks >= 3:
                return "conference_left"
        else:
            zero_sink_ticks = 0
        if was_in_meeting and not in_call and not meet.get("prejoinConnect"):
            return "conference_left"
        was_in_meeting = was_in_meeting or in_call
        await asyncio.sleep(poll_interval_sec)
