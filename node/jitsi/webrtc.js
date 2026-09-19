import { initSilentAudio } from "./silent-audio.js";

/** Install WebRTC globals for lib-jitsi-meet in Node. */

function installPlanBStreamPolyfill(pc, MediaStream) {
  const remoteStreamsById = new Map();

  const emitAddStream = (stream) => {
    if (!stream?.id || remoteStreamsById.has(stream.id)) {
      return;
    }
    remoteStreamsById.set(stream.id, stream);
    const handler = pc.onaddstream;
    if (typeof handler === "function") {
      handler({ stream, type: "addstream", target: pc });
    }
  };

  const emitRemoveStream = (stream) => {
    if (!stream?.id || !remoteStreamsById.has(stream.id)) {
      return;
    }
    remoteStreamsById.delete(stream.id);
    const handler = pc.onremovestream;
    if (typeof handler === "function") {
      handler({ stream, type: "removestream", target: pc });
    }
  };

  pc.addEventListener("track", (event) => {
    const track = event.track;
    if (!track) {
      return;
    }

    let streams = event.streams?.length ? [...event.streams] : [];
    if (!streams.length) {
      const existing = [...remoteStreamsById.values()].find((stream) =>
        stream.getTracks?.().some((item) => item.id === track.id),
      );
      streams = [existing || new MediaStream([track])];
    }

    for (const stream of streams) {
      if (remoteStreamsById.has(stream.id)) {
        const onAddTrack = stream.onaddtrack;
        if (typeof onAddTrack === "function") {
          onAddTrack({ track, target: stream, type: "addtrack" });
        }
      } else {
        emitAddStream(stream);
      }
    }

    track.addEventListener?.("ended", () => {
      const parent = [...remoteStreamsById.values()].find((stream) =>
        stream.getTracks?.().some((item) => item.id === track.id),
      );
      if (parent && parent.getTracks?.().every((item) => item.readyState === "ended")) {
        emitRemoveStream(parent);
      }
    });
  });

  if (!pc.getRemoteStreams) {
    pc.getRemoteStreams = () => [...remoteStreamsById.values()];
  }
}

/** Install WebRTC globals for lib-jitsi-meet in Node. */
export function installWebRtcGlobals(wrtc) {
  const BaseRTCPeerConnection = wrtc.RTCPeerConnection;
  const RTCSessionDescription = wrtc.RTCSessionDescription;
  const RTCIceCandidate = wrtc.RTCIceCandidate;
  const MediaStream = wrtc.MediaStream;
  const MediaStreamTrack = wrtc.MediaStreamTrack;

  function RTCPeerConnection(config, ...rest) {
    const rtcConfig = {
      ...(config || {}),
      sdpSemantics: config?.sdpSemantics || "plan-b",
    };
    const pc = new BaseRTCPeerConnection(rtcConfig, ...rest);
    installPlanBStreamPolyfill(pc, MediaStream);
    return pc;
  }
  RTCPeerConnection.prototype = BaseRTCPeerConnection.prototype;

  globalThis.RTCPeerConnection = RTCPeerConnection;
  globalThis.RTCSessionDescription = RTCSessionDescription;
  globalThis.RTCIceCandidate = RTCIceCandidate;
  globalThis.MediaStream = MediaStream;
  globalThis.MediaStreamTrack = MediaStreamTrack;

  globalThis.webkitRTCPeerConnection = RTCPeerConnection;
  globalThis.webkitMediaStream = MediaStream;
  globalThis.webkitMediaStreamTrack = MediaStreamTrack;

  if (!RTCPeerConnection.prototype.addStream) {
    RTCPeerConnection.prototype.addStream = function addStream(stream) {
      for (const track of stream.getTracks()) {
        this.addTrack(track, stream);
      }
    };
  }
  if (!RTCPeerConnection.prototype.removeStream) {
    RTCPeerConnection.prototype.removeStream = function removeStream(stream) {
      for (const track of stream.getTracks()) {
        const sender = this.getSenders().find((item) => item.track === track);
        if (sender) {
          this.removeTrack(sender);
        }
      }
    };
  }
  if (!RTCPeerConnection.prototype.getLocalStreams) {
    RTCPeerConnection.prototype.getLocalStreams = function getLocalStreams() {
      const streams = new Map();
      for (const sender of this.getSenders()) {
        if (sender.track) {
          streams.set(sender.track.id, new MediaStream([sender.track]));
        }
      }
      return [...streams.values()];
    };
  }

  globalThis.navigator.mozGetUserMedia = undefined;

  if (wrtc.nonstandard?.RTCAudioSource || wrtc.nonstandard?.RTCAudioSink) {
    globalThis.nonstandard = {
      ...(wrtc.nonstandard.RTCAudioSource
        ? { RTCAudioSource: wrtc.nonstandard.RTCAudioSource }
        : {}),
      ...(wrtc.nonstandard.RTCAudioSink
        ? { RTCAudioSink: wrtc.nonstandard.RTCAudioSink }
        : {}),
    };
    if (wrtc.nonstandard.RTCAudioSource) {
      initSilentAudio(wrtc);
    }
  }

  const getUserMediaImpl = async (constraints = {}) => {
    const wantsAudio = Boolean(constraints.audio);
    const wantsVideo = Boolean(constraints.video);
    if (wantsVideo) {
      throw new Error("video capture not available in capture bot");
    }
    if (!wantsAudio) {
      throw new Error("audio required for capture bot media");
    }
    const { createSilentMicStream } = await import("./silent-audio.js");
    return createSilentMicStream().stream;
  };

  const mediaDevices = globalThis.navigator.mediaDevices;
  Object.defineProperty(mediaDevices, "getUserMedia", {
    configurable: true,
    writable: true,
    value: getUserMediaImpl,
  });
  globalThis.navigator.webkitGetUserMedia = (constraints, ok, err) => {
    getUserMediaImpl(constraints).then(ok, err);
  };

  // @roamhq/wrtc treats arguments[0] as a MediaStreamTrack selector, not a callback.
  const originalGetStats = RTCPeerConnection.prototype.getStats;
  RTCPeerConnection.prototype.getStats = function patchedGetStats(...args) {
    const callback = args.find((arg) => typeof arg === "function");
    const legacyStats = { result: () => [] };
    try {
      const statsPromise = originalGetStats.call(this);
      if (callback) {
        Promise.resolve(statsPromise)
          .then(() => callback(legacyStats))
          .catch(() => callback(legacyStats));
        return;
      }
      return statsPromise;
    } catch (_err) {
      if (callback) {
        callback(legacyStats);
        return;
      }
      return Promise.resolve(new Map());
    }
  };
}
