import { spawn } from "node:child_process";
import fs from "node:fs";

function runCommand(command, args) {
  return new Promise((resolve, reject) => {
    const proc = spawn(command, args, { stdio: ["ignore", "pipe", "pipe"] });
    let stderr = "";
    proc.stderr.on("data", (chunk) => {
      stderr += chunk.toString();
    });
    proc.on("error", reject);
    proc.on("close", (code) => {
      if (code === 0) {
        resolve();
        return;
      }
      reject(new Error(`${command} exited ${code}: ${stderr.trim()}`));
    });
  });
}

export async function convertWavToM4a(
  wavPath,
  m4aPath,
  { sampleRate, vbrQuality } = {},
) {
  if (!fs.existsSync(wavPath)) {
    throw new Error(`source wav missing: ${wavPath}`);
  }
  const args = [
    "-y",
    "-i",
    wavPath,
    "-c:a",
    "aac",
    "-q:a",
    String(vbrQuality),
    "-ar",
    String(sampleRate),
    "-movflags",
    "+faststart",
    m4aPath,
  ];
  await runCommand("ffmpeg", args);
  if (!fs.existsSync(m4aPath)) {
    throw new Error(`ffmpeg did not produce ${m4aPath}`);
  }
}
