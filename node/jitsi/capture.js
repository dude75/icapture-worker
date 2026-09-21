import "./browser-polyfill.js";
import fs from "node:fs";
import path from "node:path";
import { loadJitsiMeetJS } from "./load-jitsi.js";
import { installStropheGlobals } from "./load-strophe.js";
import { resolveJitsiConfig } from "./resolve-config.js";
import {
  ARTIFACT_EXT,
  ARTIFACT_SAMPLE_RATE,
  FFMPEG_MP3_VBR_QUALITY,
} from "./capture-config.js";
import { ConferenceAudioRecorder } from "./audio-recorder.js";
import { convertWavToMp3 } from "./ffmpeg-convert.js";
import { writePcmWav, writeSilenceWav, writeStubWav } from "./wav.js";
import { installWebRtcGlobals } from "./webrtc.js";

const STUB_MODE = ["1", "true", "yes"].includes(String(process.env.JITSI_STUB || "").toLowerCase());

/** @type {Map<string, Promise<string>>} */
const finalizing = new Map();

let wrtcAvailable = false;
let loadError = null;
let JitsiMeetJS = null;

try {
  if (!STUB_MODE) {
    const wrtc = await import("@roamhq/wrtc");
    installWebRtcGlobals(wrtc.default || wrtc);
    installStropheGlobals();
    JitsiMeetJS = loadJitsiMeetJS();
    wrtcAvailable = true;
  }
} catch (err) {
  wrtcAvailable = false;
  loadError = err;
}

export function connectorHealth() {
  if (STUB_MODE) {
    return { status: "loaded", reason: "stub_mode" };
  }
  if (wrtcAvailable && JitsiMeetJS) {
    return { status: "loaded" };
  }
  const reason = loadError ? String(loadError.message || loadError) : "wrtc_or_lib_not_loaded";
  return { status: "unavailable", reason };
}

const sessions = new Map();

export function listSessions() {
  return [...sessions.keys()];
}

function formatConnectionError(error, jwt) {
  const code = String(error || "");
  if (
    code.includes("passwordRequired") ||
    code.includes("no-auth-mech") ||
    (code.includes("otherError") && !jwt)
  ) {
    return "connection failed: server requires JWT (pass jwt in POST /capture)";
  }
  return `connection failed: ${code}`;
}

function promiseWithTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      setTimeout(() => reject(new Error(label)), ms);
    }),
  ]);
}

async function waitUntil(check, ms, label) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (check()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error(label);
}

function artifactPath(dataDir, taskId) {
  return path.join(dataDir, "artifacts", `${taskId}.${ARTIFACT_EXT}`);
}

function tempWavPath(dataDir, taskId) {
  return path.join(dataDir, "artifacts", `${taskId}.pcm.wav`);
}

async function finalizeArtifact(wavPath, artifactPathOut) {
  await convertWavToMp3(wavPath, artifactPathOut, {
    sampleRate: ARTIFACT_SAMPLE_RATE,
    vbrQuality: FFMPEG_MP3_VBR_QUALITY,
  });
  fs.unlinkSync(wavPath);
}

function getNativePeerConnection(room) {
  return room?.room?.session?.peerconnection?.peerconnection ?? null;
}

function attachConferenceRecorder(room, recorder, JitsiMeetJS) {
  const onTrackAdded = (track) => {
    recorder.attachJitsiTrack(track);
  };
  room.on(JitsiMeetJS.events.conference.TRACK_ADDED, onTrackAdded);
  for (const participant of room.getParticipants()) {
    for (const track of participant.getTracks()) {
      recorder.attachJitsiTrack(track);
    }
  }

  const peerConnection = getNativePeerConnection(room);
  const rescanReceivers = recorder.attachPeerConnection(peerConnection);
  const rescanTimer = setInterval(() => {
    rescanReceivers?.();
    for (const participant of room.getParticipants()) {
      for (const track of participant.getTracks()) {
        recorder.attachJitsiTrack(track);
      }
    }
  }, 5000);

  return { onTrackAdded, rescanTimer, peerConnection };
}

function writeConferenceWav(wavPath, recorder, durationSec) {
  const pcm = recorder.toInt16Clamped(durationSec);
  if (pcm.length > 0) {
    writePcmWav(wavPath, pcm);
    return;
  }
  writeSilenceWav(wavPath, durationSec);
}

function scheduleAutoFinalize(session, reason) {
  if (session.canceled || session.autoFinalized) {
    return;
  }
  session.autoFinalized = true;
  console.log(`[capture] auto-finalize ${session.taskId}: ${reason}`);
  void stopCapture({ taskId: session.taskId, dataDir: session.dataDir }).catch((err) => {
    console.error(`[capture] auto-finalize failed ${session.taskId}:`, err);
  });
}

function attachDisconnectHandlers(session, room, connection) {
  const events = JitsiMeetJS.events;
  const onDisconnect = (reason) => scheduleAutoFinalize(session, reason);

  room.on(events.conference.CONFERENCE_LEFT, () => onDisconnect("conference_left"));
  room.on(events.conference.KICKED, () => onDisconnect("kicked"));
  room.on(events.conference.CONFERENCE_FAILED, () => onDisconnect("conference_failed"));
  connection.addEventListener(events.connection.CONNECTION_FAILED, () =>
    onDisconnect("connection_failed"),
  );
  connection.addEventListener(events.connection.CONNECTION_DISCONNECTED, () =>
    onDisconnect("connection_disconnected"),
  );
}

export async function startCapture({
  taskId,
  meetingHost,
  meetingRoom,
  displayName,
  pin,
  jwt,
  dataDir,
}) {
  if (sessions.has(taskId)) {
    throw new Error("session already active");
  }

  const startedAt = Date.now();
  const session = {
    taskId,
    meetingHost,
    meetingRoom,
    displayName,
    pin,
    jwt,
    dataDir,
    startedAt,
    canceled: false,
    autoFinalized: false,
    connection: null,
  };
  sessions.set(taskId, session);

  if (STUB_MODE || !wrtcAvailable) {
    return;
  }

  try {
    await joinJitsiRoom(session);
  } catch (err) {
    sessions.delete(taskId);
    throw err;
  }
}

async function joinJitsiRoom(session) {
  const { meetingHost, meetingRoom, displayName, pin, jwt } = session;
  const remote = await resolveJitsiConfig(meetingHost);
  const hosts = {
    domain: remote.domain,
    muc: remote.muc,
  };
  if (remote.anonymousdomain) {
    hosts.anonymousdomain = remote.anonymousdomain;
  }
  if (remote.authdomain) {
    hosts.authdomain = remote.authdomain;
  }

  const options = {
    hosts,
    // lib-jitsi-meet@1.0.6 uses `bosh`; wss:// selects WebSocket in Strophe.
    bosh: remote.websocket,
    clientNode: "http://jitsi.org/jitsimeet",
  };

  JitsiMeetJS.init({
    disableAudioLevels: true,
    disableThirdPartyRequests: true,
  });
  const connection = new JitsiMeetJS.JitsiConnection(null, jwt || null, options);

  await promiseWithTimeout(
    new Promise((resolve, reject) => {
      connection.addEventListener(
        JitsiMeetJS.events.connection.CONNECTION_ESTABLISHED,
        () => resolve(),
      );
      connection.addEventListener(
        JitsiMeetJS.events.connection.CONNECTION_FAILED,
        (error) => reject(new Error(formatConnectionError(error, jwt))),
      );
      connection.connect();
    }),
    30_000,
    "connection timeout",
  );

  const room = connection.initJitsiConference(meetingRoom.toLowerCase(), {
    openBridgeChannel: true,
  });

  if (pin) {
    room.setPassword(pin);
  }

  await promiseWithTimeout(
    new Promise((resolve, reject) => {
      room.on(JitsiMeetJS.events.conference.CONFERENCE_JOINED, () => resolve());
      room.on(JitsiMeetJS.events.conference.CONFERENCE_FAILED, (error) =>
        reject(new Error(`conference failed: ${error}`)),
      );
      room.setDisplayName(displayName || "Transcription Bot");
      room.join();
    }),
    30_000,
    "conference join timeout",
  );

  await waitUntil(() => Boolean(room.room?.session), 30_000, "jingle session timeout");

  const recorder = new ConferenceAudioRecorder();
  recorder.start();
  const onTrackAdded = attachConferenceRecorder(room, recorder, JitsiMeetJS);

  const localTracks = await JitsiMeetJS.createLocalTracks({
    devices: ["audio"],
    micDeviceId: null,
  });
  for (const track of localTracks) {
    track.startMuted = true;
    await room.addTrack(track);
  }

  attachDisconnectHandlers(session, room, connection);

  session.connection = {
    connection,
    room,
    localTracks,
    recorder,
    trackHooks: onTrackAdded,
  };
}

async function finalizeSession(session, dataDir) {
  const durationSec = Math.max((Date.now() - session.startedAt) / 1000, 0.1);
  const outPath = artifactPath(dataDir, session.taskId);

  const recorder = session.connection?.recorder;
  const captureStats = recorder
    ? { sinks: recorder.sinkCount(), samples: recorder.sampleCount() }
    : null;
  if (session.connection) {
    try {
      if (recorder) {
        await new Promise((resolve) => setTimeout(resolve, 150));
        recorder.stop();
      }
      if (session.connection.trackHooks?.rescanTimer) {
        clearInterval(session.connection.trackHooks.rescanTimer);
      }
      if (session.connection.trackHooks?.onTrackAdded) {
        session.connection.room.off(
          JitsiMeetJS.events.conference.TRACK_ADDED,
          session.connection.trackHooks.onTrackAdded,
        );
      }
      for (const track of session.connection.localTracks || []) {
        try {
          await session.connection.room.removeTrack(track);
        } catch (_err) {
          // best effort
        }
        track.dispose();
      }
      session.connection.room.leave();
      session.connection.connection.disconnect();
    } catch (_err) {
      // best effort
    }
  }

  const wavPath = tempWavPath(dataDir, session.taskId);
  if (STUB_MODE || !wrtcAvailable) {
    writeStubWav(wavPath, durationSec);
  } else if (recorder) {
    console.log(
      `[capture] ${session.taskId} sinks=${captureStats?.sinks ?? 0} samples=${captureStats?.samples ?? 0}`,
    );
    writeConferenceWav(wavPath, recorder, durationSec);
  } else {
    writeSilenceWav(wavPath, durationSec);
  }

  await finalizeArtifact(wavPath, outPath);
  return outPath;
}

export async function stopCapture({ taskId, dataDir }) {
  const inFlight = finalizing.get(taskId);
  if (inFlight) {
    return inFlight;
  }

  const session = sessions.get(taskId);
  const dir = dataDir ?? session?.dataDir;
  if (!dir) {
    throw new Error("dataDir required");
  }
  const outPath = artifactPath(dir, taskId);

  if (!session || session.canceled) {
    if (fs.existsSync(outPath)) {
      return outPath;
    }
    throw new Error("session not active");
  }

  const promise = finalizeSession(session, dir);
  finalizing.set(taskId, promise);
  try {
    return await promise;
  } finally {
    finalizing.delete(taskId);
    sessions.delete(taskId);
  }
}

export async function cancelAllCaptures() {
  for (const taskId of [...sessions.keys()]) {
    await cancelCapture({ taskId });
  }
}

export async function cancelCapture({ taskId }) {
  const session = sessions.get(taskId);
  if (!session) {
    return;
  }
  session.canceled = true;
  sessions.delete(taskId);
  if (session.connection) {
    try {
      if (session.connection.recorder) {
        session.connection.recorder.stop();
      }
      if (session.connection.trackHooks?.rescanTimer) {
        clearInterval(session.connection.trackHooks.rescanTimer);
      }
      if (session.connection.trackHooks?.onTrackAdded) {
        session.connection.room.off(
          JitsiMeetJS.events.conference.TRACK_ADDED,
          session.connection.trackHooks.onTrackAdded,
        );
      }
      for (const track of session.connection.localTracks || []) {
        try {
          await session.connection.room.removeTrack(track);
        } catch (_err) {
          // best effort
        }
        track.dispose();
      }
      session.connection.room.leave();
      session.connection.connection.disconnect();
    } catch (_err) {
      // best effort
    }
  }
}
