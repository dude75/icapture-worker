/** Capture PCM + artifact output settings (keep in sync with Python config). */

/** WebRTC/Opus decode rate — capture at full quality, no downsample here. */
export const PCM_SAMPLE_RATE = 48000;

/** AAC output in .m4a for Hub/transcribe. */
export const ARTIFACT_SAMPLE_RATE = 44100;
export const ARTIFACT_EXT = "m4a";

export const FFMPEG_AAC_VBR_QUALITY = Number.parseInt(
  String(process.env.FFMPEG_AAC_VBR_QUALITY || "2"),
  10,
);
