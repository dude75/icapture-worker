/** Mix remote conference audio into mono PCM using RTCAudioSink. */

import { PCM_SAMPLE_RATE } from "./capture-config.js";
import { resampleToMono } from "./resample.js";

export class ConferenceAudioRecorder {
  constructor(sampleRate = PCM_SAMPLE_RATE) {
    this.sampleRate = sampleRate;
    this.startedAt = null;
    this.mix = new Int32Array(0);
    this.sinks = new Map();
  }

  start() {
    this.startedAt = Date.now();
  }

  attachJitsiTrack(jitsiTrack, attempt = 0) {
    if (!jitsiTrack || jitsiTrack.isLocal?.()) {
      return;
    }
    if (jitsiTrack.getType?.() !== "audio" && !jitsiTrack.isAudioTrack?.()) {
      return;
    }
    const mediaTrack =
      jitsiTrack.getTrack?.() ||
      jitsiTrack.getOriginalStream?.()?.getAudioTracks?.()[0];
    if (!mediaTrack || mediaTrack.kind !== "audio") {
      if (attempt < 15) {
        setTimeout(() => this.attachJitsiTrack(jitsiTrack, attempt + 1), 200);
      }
      return;
    }
    this.attachMediaTrack(mediaTrack);
  }

  attachMediaTrack(mediaTrack) {
    if (!mediaTrack || mediaTrack.kind !== "audio" || this.sinks.has(mediaTrack.id)) {
      return;
    }

    const RTCAudioSink = globalThis.nonstandard?.RTCAudioSink;
    if (!RTCAudioSink) {
      return;
    }

    const sink = new RTCAudioSink(mediaTrack);
    const trackId = mediaTrack.id;

    const onData = (data) => {
      if (!data?.samples?.length) {
        return;
      }
      if (this.startedAt === null) {
        this.startedAt = Date.now();
      }
      const channelCount = data.channelCount || 1;
      const resampled = resampleToMono(
        data.samples,
        data.sampleRate,
        channelCount,
        this.sampleRate,
      );
      const elapsedSec = (Date.now() - this.startedAt) / 1000;
      const endSample = Math.floor(elapsedSec * this.sampleRate);
      const offset = Math.max(0, endSample - resampled.length);
      this._mixAt(offset, resampled);
    };

    sink.ondata = onData;
    this.sinks.set(trackId, { sink, mediaTrack });
  }

  attachPeerConnection(peerConnection) {
    if (!peerConnection) {
      return;
    }
    const attachFromReceivers = () => {
      for (const receiver of peerConnection.getReceivers?.() || []) {
        if (receiver.track?.kind === "audio") {
          this.attachMediaTrack(receiver.track);
        }
      }
      for (const stream of peerConnection.getRemoteStreams?.() || []) {
        for (const track of stream.getAudioTracks?.() || []) {
          this.attachMediaTrack(track);
        }
      }
    };

    attachFromReceivers();
    peerConnection.addEventListener?.("track", (event) => {
      if (event.track?.kind === "audio") {
        this.attachMediaTrack(event.track);
      }
    });
    return attachFromReceivers;
  }

  _mixAt(offset, chunk) {
    const end = offset + chunk.length;
    if (this.mix.length < end) {
      const grown = new Int32Array(Math.max(end, this.mix.length * 2 || this.sampleRate * 10));
      grown.set(this.mix);
      this.mix = grown;
    }
    for (let i = 0; i < chunk.length; i += 1) {
      this.mix[offset + i] += chunk[i];
    }
  }

  stop() {
    for (const { sink } of this.sinks.values()) {
      try {
        sink.stop();
      } catch (_err) {
        // best effort
      }
    }
    this.sinks.clear();
  }

  toInt16Clamped(durationSec) {
    const sessionSamples = Math.max(Math.floor(durationSec * this.sampleRate), 0);
    const writtenSamples = this.mix.length;
    const frameCount = Math.max(sessionSamples, writtenSamples);
    const out = new Int16Array(frameCount);
    for (let i = 0; i < Math.min(writtenSamples, frameCount); i += 1) {
      const value = this.mix[i];
      out[i] = Math.max(-32768, Math.min(32767, value));
    }
    return out;
  }

  sampleCount() {
    return this.mix.length;
  }

  sinkCount() {
    return this.sinks.size;
  }
}
