/** Synthetic silent microphone for appearing in Jitsi participant list. */

const SAMPLE_RATE = 48_000;
const FRAME_SAMPLES = SAMPLE_RATE / 100; // 10 ms

let RTCAudioSource = null;

export function initSilentAudio(wrtc) {
  RTCAudioSource = wrtc.nonstandard?.RTCAudioSource;
}

export function createSilentMicStream() {
  if (!RTCAudioSource) {
    throw new Error("RTCAudioSource unavailable");
  }

  const source = new RTCAudioSource();
  const track = source.createTrack();
  const stream = new MediaStream([track]);
  const silence = new Int16Array(FRAME_SAMPLES);
  const pump = setInterval(() => {
    source.onData({
      samples: silence,
      sampleRate: SAMPLE_RATE,
      bitsPerSample: 16,
      channelCount: 1,
      numberOfFrames: FRAME_SAMPLES,
    });
  }, 10);

  return {
    stream,
    track,
    stop() {
      clearInterval(pump);
      try {
        track.stop();
      } catch (_err) {
        // best effort
      }
    },
  };
}
