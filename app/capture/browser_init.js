(function () {
  if (window.__icapture) {
    return;
  }
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  if (!AudioCtx) {
    return;
  }
  const ctx = new AudioCtx({ sampleRate: 48000 });
  void ctx.resume();
  const mixGain = ctx.createGain();
  mixGain.gain.value = 1;
  const dest = ctx.createMediaStreamDestination();
  mixGain.connect(dest);

  const connected = new Set();
  const chunks = [];
  let totalSamples = 0;

  function detachTrack(track) {
    if (!track) {
      return;
    }
    connected.delete(track.id);
  }

  function attachTrack(track) {
    if (!track || track.kind !== "audio" || connected.has(track.id)) {
      return;
    }
    connected.add(track.id);
    track.addEventListener("ended", () => detachTrack(track));
    try {
      const src = ctx.createMediaStreamSource(new MediaStream([track]));
      src.connect(mixGain);
    } catch (_err) {
      connected.delete(track.id);
    }
  }

  const NativePC = window.RTCPeerConnection;
  if (typeof NativePC === "function") {
    function WrappedPC(...args) {
      const pc = new NativePC(...args);
      pc.addEventListener("track", (event) => attachTrack(event.track));
      return pc;
    }
    WrappedPC.prototype = NativePC.prototype;
    Object.setPrototypeOf(WrappedPC, NativePC);
    window.RTCPeerConnection = WrappedPC;
  }

  const tap = ctx.createScriptProcessor(4096, 1, 1);
  const tapSrc = ctx.createMediaStreamSource(dest.stream);
  tapSrc.connect(tap);
  const silent = ctx.createGain();
  silent.gain.value = 0;
  tap.connect(silent);
  silent.connect(ctx.destination);

  tap.onaudioprocess = (event) => {
    const input = event.inputBuffer.getChannelData(0);
    if (!input.length) {
      return;
    }
    const copy = new Float32Array(input.length);
    copy.set(input);
    chunks.push(copy);
    totalSamples += copy.length;
  };

  window.__icapture = {
    resume() {
      return ctx.resume();
    },
    sinkCount() {
      return connected.size;
    },
    sampleCount() {
      return totalSamples;
    },
    flushInt16Pcm() {
      const out = new Int16Array(totalSamples);
      let offset = 0;
      for (const chunk of chunks) {
        for (let i = 0; i < chunk.length; i++) {
          const sample = Math.max(-1, Math.min(1, chunk[i]));
          out[offset++] = sample < 0 ? sample * 32768 : sample * 32767;
        }
      }
      chunks.length = 0;
      totalSamples = 0;
      return out;
    },
  };
})();
