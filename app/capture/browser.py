"""Jitsi (and future web meetings) capture via Playwright + in-page audio mix."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from app.artifacts import (
    append_pcm_samples,
    artifact_path,
    convert_pcm_to_mp3,
    temp_pcm_path,
)
from app.config import Settings

logger = logging.getLogger("app")

_INIT_SCRIPT_PATH = Path(__file__).with_name("browser_init.js")

# Installed after join; polled from wait_for_disconnect().
_DISCONNECT_WATCH_JS = """
() => {
  if (window.__icaptureDisconnect) {
    return;
  }
  const state = { reason: null, wasJoined: false };
  window.__icaptureDisconnect = state;

  const setReason = (reason) => {
    if (state.reason) {
      return;
    }
    state.reason = reason;
  };

  const conferenceEvents = () => {
    const fromLib = window.JitsiMeetJS?.events?.conference;
    if (fromLib) {
      return fromLib;
    }
    return {
      KICKED: "conference.kicked",
      CONFERENCE_LEFT: "conference.left",
      CONFERENCE_FAILED: "conference.failed",
    };
  };

  const getRoom = () => {
    try {
      const conf = window.APP?.conference;
      if (typeof conf?.getConference === "function") {
        return conf.getConference();
      }
      if (conf?._room) {
        return conf._room;
      }
      if (conf?.room) {
        return conf.room;
      }
    } catch (_err) {
      // ignore
    }
    try {
      return (
        window.APP?.store?.getState?.()?.["features/base/conference"]
          ?.conference ?? null
      );
    } catch (_err) {
      return null;
    }
  };

  const attachRoom = (room) => {
    if (!room || room.__icaptureDisconnectHook || typeof room.on !== "function") {
      return;
    }
    room.__icaptureDisconnectHook = true;
    const ev = conferenceEvents();
    room.on(ev.KICKED, () => setReason("kicked"));
    room.on(ev.CONFERENCE_LEFT, () => setReason("conference_left"));
    room.on(ev.CONFERENCE_FAILED, () => setReason("conference_failed"));
  };

  const inMeeting = () => {
    try {
      if (window.APP?.conference?.isJoined?.() === true) {
        return true;
      }
    } catch (_err) {
      // ignore
    }
    return Boolean(
      document.querySelector('button[aria-label*="Leave the meeting"]') ||
        document.querySelector('[data-testid="icapture-test-ready"]'),
    );
  };

  const tick = () => {
    if (state.reason) {
      return;
    }
    const joined = inMeeting();
    if (joined) {
      state.wasJoined = true;
    } else if (state.wasJoined) {
      setReason("conference_left");
    }
    attachRoom(getRoom());
    const text = document.body?.innerText ?? "";
    if (
      /\\b(kicked|removed from the conference|you have been removed|you have been kicked|исключен|исключён|удален из конференции|удалён из конференции)\\b/i.test(
        text,
      )
    ) {
      setReason("kicked");
    }
  };

  tick();
  window.__icaptureDisconnectTimer = setInterval(tick, 1000);
}
"""

_READ_DISCONNECT_REASON_JS = "() => window.__icaptureDisconnect?.reason ?? null"

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


def build_meeting_url(meeting_host: str, meeting_room: str, display_name: str) -> str:
    room = quote(meeting_room)
    name = quote(display_name or "Transcription Bot")
    hash_cfg = "&".join(
        [
            "config.prejoinPageEnabled=false",
            "config.startWithAudioMuted=true",
            "config.startWithVideoMuted=true",
            f"userInfo.displayName={name}",
        ]
    )
    return f"https://{meeting_host}/{room}#{hash_cfg}"


def _load_init_script() -> str:
    return _INIT_SCRIPT_PATH.read_text(encoding="utf-8")


@dataclass
class BrowserCaptureSession:
    settings: Settings
    task_id: str
    slot: int
    meeting_host: str
    meeting_room: str
    display_name: str
    pin: str

    _playwright: object | None = None
    _browser: object | None = None
    _context: object | None = None
    _page: object | None = None
    _pcm_path: Path | None = field(default=None, init=False, repr=False)
    _spool_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _spooled_samples: int = field(default=0, init=False, repr=False)

    async def open_and_join(self) -> None:
        from playwright.async_api import async_playwright

        headless = self.settings.PLAYWRIGHT_HEADLESS
        self._playwright = await async_playwright().start()
        chromium = self._playwright.chromium  # type: ignore[union-attr]
        self._browser = await chromium.launch(
            headless=headless,
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        self._context = await self._browser.new_context(  # type: ignore[union-attr]
            permissions=["microphone", "camera"],
            ignore_https_errors=True,
            viewport={"width": 1280, "height": 720},
            locale="en-US",
        )
        await self._context.add_init_script(_load_init_script())  # type: ignore[union-attr]
        self._page = await self._context.new_page()  # type: ignore[union-attr]
        url = build_meeting_url(self.meeting_host, self.meeting_room, self.display_name)
        await self._page.goto(url, wait_until="domcontentloaded", timeout=60_000)  # type: ignore[union-attr]
        await self._join_conference(self._page)
        await self._ensure_mic_muted(self._page)
        await self._page.evaluate(
            """async () => {
              const cap = window.__icapture;
              if (cap?.resume) {
                await cap.resume();
              }
            }"""
        )
        self._pcm_path = temp_pcm_path(self.settings.DATA_DIR, self.task_id)
        self._pcm_path.parent.mkdir(parents=True, exist_ok=True)
        self._spool_task = asyncio.create_task(self._pcm_spool_loop())
        logger.info(
            "browser capture joined %s/%s task=%s slot=%s",
            self.meeting_host,
            self.meeting_room,
            self.task_id,
            self.slot,
        )

    async def wait_for_disconnect(self, poll_interval_sec: float = 1.0) -> str:
        if not self._page:
            raise RuntimeError("browser page not open")
        page = self._page
        await page.evaluate(_DISCONNECT_WATCH_JS)  # type: ignore[union-attr]
        while True:
            if page.is_closed():  # type: ignore[union-attr]
                return "page_closed"
            reason = await page.evaluate(_READ_DISCONNECT_REASON_JS)  # type: ignore[union-attr]
            if reason:
                return str(reason)
            await asyncio.sleep(poll_interval_sec)

    async def _join_conference(self, page, timeout_ms: int = 90_000) -> None:
        """Prejoin UI varies by deployment (e.g. meet.realweb.ru uses aria labels, not testids)."""
        name = self.display_name or "Transcription Bot"
        deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
        while asyncio.get_event_loop().time() < deadline:
            if await self._in_meeting(page):
                return
            await self._fill_lobby_pin(page)
            await self._fill_prejoin_name(page, name)
            # Do not spam Join — repeated clicks make Jitsi join then immediately leave.
            if await self._prejoin_visible(page):
                await self._ensure_mic_muted(page)
                await self._click_prejoin_join(page)
            await asyncio.sleep(1.0)
        await self._save_join_debug(page, "timeout")
        raise TimeoutError("browser conference join timeout")

    async def _prejoin_visible(self, page) -> bool:
        try:
            join = page.get_by_role(
                "button", name=re.compile(r"^(join meeting|join without audio)$", re.I)
            )
            if await join.first.is_visible(timeout=400):
                return True
        except Exception:
            pass
        try:
            field = page.get_by_placeholder(re.compile(r"enter your name", re.I))
            return await field.first.is_visible(timeout=400)
        except Exception:
            return False

    async def _in_meeting(self, page) -> bool:
        stats = await page.evaluate(
            """() => {
              let joined = false;
              try {
                joined = window.APP?.conference?.isJoined?.() === true;
              } catch (_err) {
                joined = false;
              }
              const leave = document.querySelector(
                'button[aria-label*="Leave the meeting"]'
              );
              const testReady = document.querySelector(
                '[data-testid="icapture-test-ready"]'
              );
              return {
                sinks: window.__icapture?.sinkCount?.() ?? 0,
                inMeeting: Boolean(joined || leave || testReady),
              };
            }"""
        )
        return bool(stats.get("inMeeting"))

    async def _save_join_debug(self, page, tag: str) -> None:
        log_dir = Path(self.settings.LOG_DIR)
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"join-{tag}-{self.task_id}.png"
        try:
            await page.screenshot(path=str(path), full_page=True)
            logger.warning("join debug screenshot %s", path)
        except Exception as exc:
            logger.warning("join debug screenshot failed: %s", exc)

    async def _fill_lobby_pin(self, page) -> None:
        if not self.pin:
            return
        for selector in (
            'input[data-testid="lobby.passwordInput"]',
            'input[type="password"]',
        ):
            field = page.locator(selector).first
            try:
                if await field.is_visible(timeout=400):
                    await field.fill(self.pin)
                    return
            except Exception:
                continue

    async def _fill_prejoin_name(self, page, name: str) -> None:
        candidates = [
            page.get_by_placeholder(re.compile(r"enter your name", re.I)),
            page.get_by_label(re.compile(r"your name", re.I)),
            page.locator('input[data-testid="prejoin.display-name"]'),
            page.locator("#premeeting-name-input"),
        ]
        for loc in candidates:
            try:
                field = loc.first
                if await field.is_visible(timeout=400):
                    await field.fill(name)
                    return
            except Exception:
                continue

    async def _ensure_mic_muted(self, page) -> None:
        """Bot must not send audio — click Mute if the track is live."""
        for _ in range(4):
            try:
                unmute = page.get_by_role(
                    "button", name=re.compile(r"^unmute microphone$", re.I)
                )
                if await unmute.first.is_visible(timeout=300):
                    return
            except Exception:
                pass
            try:
                mute = page.get_by_role(
                    "button", name=re.compile(r"^mute microphone$", re.I)
                )
                if await mute.first.is_visible(timeout=300):
                    await mute.first.click(timeout=3000)
                    await asyncio.sleep(0.25)
                    continue
            except Exception:
                pass
            try:
                toggled = page.locator('[data-testid="toolbar-button-mute"]').first
                if await toggled.is_visible(timeout=300):
                    pressed = await toggled.get_attribute("aria-pressed")
                    if pressed == "false":
                        await toggled.click(timeout=3000)
                        await asyncio.sleep(0.25)
                        continue
                    return
            except Exception:
                break

    async def _click_prejoin_join(self, page) -> None:
        candidates = [
            page.get_by_role("button", name=re.compile(r"^join meeting$", re.I)),
            page.get_by_role("button", name=re.compile(r"join without audio", re.I)),
            page.locator('button[data-testid="prejoin.joinMeeting"]'),
            page.locator('button[data-testid="lobby.joinButton"]'),
            page.locator("#prejoin-join-button"),
        ]
        for loc in candidates:
            try:
                btn = loc.first
                if await btn.is_visible(timeout=400):
                    await btn.click(timeout=10_000)
                    return
            except Exception:
                continue

    async def _pcm_spool_loop(self) -> None:
        interval = self.settings.CAPTURE_PCM_SPOOL_INTERVAL_SEC
        while True:
            await asyncio.sleep(interval)
            try:
                await self._spool_pcm_chunk()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._page is None:
                    return
                logger.warning("pcm spool failed task=%s: %s", self.task_id, exc)

    async def _spool_pcm_chunk(self) -> int:
        if not self._page or self._pcm_path is None:
            return 0
        drained = await self._page.evaluate(_DRAIN_PCM_JS)  # type: ignore[union-attr]
        pcm_list = drained.get("pcm") or []
        if pcm_list:
            await asyncio.to_thread(append_pcm_samples, self._pcm_path, pcm_list)
            self._spooled_samples += len(pcm_list)
        return len(pcm_list)

    async def _stop_pcm_spool(self) -> None:
        task = self._spool_task
        self._spool_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def finalize_to_mp3(self) -> Path:
        if self._pcm_path is None:
            raise RuntimeError("browser capture not started")
        await self._stop_pcm_spool()
        sinks = 0
        final_pcm: list[int] = []
        if self._page:
            drained = await self._page.evaluate(_DRAIN_PCM_JS)  # type: ignore[union-attr]
            sinks = int(drained.get("sinks") or 0)
            final_pcm = drained.get("pcm") or []
            if final_pcm:
                await asyncio.to_thread(append_pcm_samples, self._pcm_path, final_pcm)
                self._spooled_samples += len(final_pcm)
        logger.info(
            "browser capture finalize task=%s sinks=%s spooled=%s final_pcm=%s",
            self.task_id,
            sinks,
            self._spooled_samples,
            len(final_pcm),
        )
        pcm_path = self._pcm_path
        mp3_path = artifact_path(self.settings.DATA_DIR, self.task_id)
        if not pcm_path.is_file() or pcm_path.stat().st_size == 0:
            raise ValueError("no remote audio captured")
        await asyncio.to_thread(convert_pcm_to_mp3, pcm_path, mp3_path)
        pcm_path.unlink(missing_ok=True)
        return mp3_path

    async def close(self) -> None:
        await self._stop_pcm_spool()
        page = self._page
        context = self._context
        browser = self._browser
        playwright = self._playwright
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        try:
            if context:
                await context.close()  # type: ignore[union-attr]
        except Exception as exc:
            logger.debug("context close: %s", exc)
        try:
            if browser:
                await browser.close()  # type: ignore[union-attr]
        except Exception as exc:
            logger.debug("browser close: %s", exc)
        try:
            if playwright:
                await playwright.stop()  # type: ignore[union-attr]
        except Exception as exc:
            logger.debug("playwright stop: %s", exc)
        del page


async def check_browser_ready() -> tuple[bool, str | None]:
    """Match real capture: headless launch needs chromium + headless_shell."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return False, "playwright_not_installed"

    from app.config import get_settings

    headless = get_settings().PLAYWRIGHT_HEADLESS
    playwright = None
    browser = None
    try:
        playwright = await async_playwright().start()
        chromium = playwright.chromium
        browser = await chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
    except Exception as exc:
        msg = str(exc).splitlines()[0] if str(exc) else "browser_launch_failed"
        if "Executable doesn't exist" in str(exc) or "playwright install" in str(exc):
            return False, "run: .venv/bin/python -m playwright install chromium chromium-headless-shell"
        return False, msg
    finally:
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()
    return True, None
