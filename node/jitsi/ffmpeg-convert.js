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

export async function convertWavToMp3(
  wavPath,
  mp3Path,
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
    "libmp3lame",
    "-q:a",
    String(vbrQuality),
    "-ar",
    String(sampleRate),
    mp3Path,
  ];
  await runCommand("ffmpeg", args);
  if (!fs.existsSync(mp3Path)) {
    throw new Error(`ffmpeg did not produce ${mp3Path}`);
  }
}
