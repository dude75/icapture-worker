import express from "express";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  cancelAllCaptures,
  cancelCapture,
  connectorHealth,
  listSessions,
  startCapture,
  stopCapture,
} from "./capture.js";

const app = express();
app.use(express.json({ limit: "1mb" }));

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const HOST = process.env.HOST || "127.0.0.1";
const PORT = Number(process.env.PORT || 8001);
const DATA_DIR = process.env.DATA_DIR || path.join(ROOT, "data");

app.get("/health", (_req, res) => {
  const jitsi = connectorHealth();
  res.json({
    status: "ok",
    connectors: {
      jitsi: {
        status: jitsi.status,
        label: "Jitsi Meet",
        reason: jitsi.reason || null,
      },
    },
  });
});

app.get("/capture/sessions", (_req, res) => {
  res.json({ task_ids: listSessions() });
});

app.post("/capture/start", async (req, res) => {
  const { task_id: taskId, meeting_host: meetingHost, meeting_room: meetingRoom } = req.body || {};
  if (!taskId || !meetingHost || !meetingRoom) {
    return res.status(400).json({
      error: { reason: "invalid_request", message: "task_id, meeting_host, meeting_room required" },
    });
  }
  try {
    await startCapture({
      taskId,
      meetingHost,
      meetingRoom,
      displayName: req.body.display_name,
      pin: req.body.pin || "",
      jwt: req.body.jwt || null,
      dataDir: DATA_DIR,
    });
    return res.status(200).json({ status: "capturing", task_id: taskId });
  } catch (err) {
    return res.status(422).json({
      error: { reason: "join_failed", message: String(err.message || err) },
    });
  }
});

app.post("/capture/stop", async (req, res) => {
  const taskId = req.body?.task_id;
  if (!taskId) {
    return res.status(400).json({
      error: { reason: "invalid_request", message: "task_id required" },
    });
  }
  try {
    const artifactPath = await stopCapture({ taskId, dataDir: DATA_DIR });
    return res.status(200).json({ status: "success", artifact_path: artifactPath });
  } catch (err) {
    return res.status(422).json({
      error: { reason: "finalize_failed", message: String(err.message || err) },
    });
  }
});

app.post("/capture/cancel", async (req, res) => {
  const taskId = req.body?.task_id;
  if (!taskId) {
    return res.status(400).json({
      error: { reason: "invalid_request", message: "task_id required" },
    });
  }
  await cancelCapture({ taskId });
  return res.status(200).json({ status: "canceled", task_id: taskId });
});

process.on("unhandledRejection", (err) => {
  console.error("unhandledRejection:", err);
});

let shuttingDown = false;
async function shutdown(signal) {
  if (shuttingDown) {
    return;
  }
  shuttingDown = true;
  console.log(`shutting down jitsi engine (${signal})`);
  await cancelAllCaptures();
  process.exit(0);
}
process.on("SIGINT", () => {
  void shutdown("SIGINT");
});
process.on("SIGTERM", () => {
  void shutdown("SIGTERM");
});

app.listen(PORT, HOST, () => {
  console.log(`icapture jitsi engine listening on http://${HOST}:${PORT}`);
});
