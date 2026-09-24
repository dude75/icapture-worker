"""Playwright Chromium launch + media permissions (Jitsi today; Telemost spike/connector)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("app")

# Same flags as BrowserCaptureSession (Jitsi). Telemost uses the same stack + CDP grant.
CHROMIUM_CAPTURE_ARGS: list[str] = [
    "--use-fake-device-for-media-stream",
    "--use-fake-ui-for-media-stream",
    "--autoplay-policy=no-user-gesture-required",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]

# If fake devices / permission prompt fail, return muted dummy tracks so prejoin can continue
# (guest flow: user may deny mic in UI and still click «Подключиться»).
GETUSERMEDIA_FALLBACK_INIT_JS = """
(() => {
  if (window.__icaptureGumFallback) {
    return;
  }
  window.__icaptureGumFallback = true;
  const media = navigator.mediaDevices;
  if (!media?.getUserMedia) {
    return;
  }
  const original = media.getUserMedia.bind(media);
  media.getUserMedia = async (constraints) => {
    try {
      return await original(constraints);
    } catch (_err) {
      const tracks = [];
      const c = constraints || {};
      if (c.audio) {
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
          tracks.push(track);
        }
      }
      if (c.video) {
        const canvas = document.createElement("canvas");
        canvas.width = 640;
        canvas.height = 480;
        const stream = canvas.captureStream(1);
        for (const track of stream.getVideoTracks()) {
          track.enabled = false;
          tracks.push(track);
        }
      }
      if (!tracks.length) {
        throw _err;
      }
      return new MediaStream(tracks);
    }
  };
})();
"""


async def grant_origin_media_permissions(context: Any, origin: str) -> None:
    """Playwright context grant (origin-scoped)."""
    try:
        await context.grant_permissions(["microphone", "camera"], origin=origin)
    except Exception as exc:
        logger.warning("context.grant_permissions failed origin=%s: %s", origin, exc)


async def grant_origin_media_permissions_cdp(page: Any, origin: str) -> None:
    """CDP Browser.grantPermissions — helps Telemost when prompts / fake devices misbehave."""
    try:
        session = await page.context.new_cdp_session(page)
        await session.send(
            "Browser.grantPermissions",
            {
                "origin": origin,
                "permissions": ["audioCapture", "videoCapture"],
            },
        )
    except Exception as exc:
        logger.warning("CDP Browser.grantPermissions failed origin=%s: %s", origin, exc)


async def prepare_media_capture_scripts(
    context: Any,
    *,
    capture_init_js: str,
    gum_fallback: bool = True,
) -> None:
    if gum_fallback:
        await context.add_init_script(GETUSERMEDIA_FALLBACK_INIT_JS)
    await context.add_init_script(capture_init_js)
