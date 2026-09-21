/** Capture PCM + artifact output settings (keep in sync with Python config). */

/** WebRTC/Opus decode rate — capture at full quality, no downsample here. */
export const PCM_SAMPLE_RATE = 48000;

/** MP3 output for Hub/transcribe (libmp3lame VBR). */
export const ARTIFACT_SAMPLE_RATE = 44100;
export const ARTIFACT_EXT = "mp3";

export const FFMPEG_MP3_VBR_QUALITY = Number.parseInt(
  String(process.env.FFMPEG_MP3_VBR_QUALITY || "2"),
  10,
);
