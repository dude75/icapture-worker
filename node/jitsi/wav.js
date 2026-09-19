import fs from "node:fs";
import path from "node:path";
import { PCM_SAMPLE_RATE } from "./capture-config.js";

export const SAMPLE_RATE = PCM_SAMPLE_RATE;
const CHANNELS = 1;
const SAMPLE_WIDTH = 2;

export function writeStubWav(filePath, durationSec, frequencyHz = 440) {
  const frameCount = Math.max(Math.floor(durationSec * SAMPLE_RATE), SAMPLE_RATE / 10);
  const dataSize = frameCount * SAMPLE_WIDTH;
  const buffer = Buffer.alloc(44 + dataSize);

  buffer.write("RIFF", 0);
  buffer.writeUInt32LE(36 + dataSize, 4);
  buffer.write("WAVE", 8);
  buffer.write("fmt ", 12);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(CHANNELS, 22);
  buffer.writeUInt32LE(SAMPLE_RATE, 24);
  buffer.writeUInt32LE(SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH, 28);
  buffer.writeUInt16LE(CHANNELS * SAMPLE_WIDTH, 32);
  buffer.writeUInt16LE(SAMPLE_WIDTH * 8, 34);
  buffer.write("data", 36);
  buffer.writeUInt32LE(dataSize, 40);

  for (let i = 0; i < frameCount; i += 1) {
    const sample = Math.floor(16000 * Math.sin((2 * Math.PI * frequencyHz * i) / SAMPLE_RATE));
    buffer.writeInt16LE(sample, 44 + i * SAMPLE_WIDTH);
  }

  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, buffer);
}

export function writePcmWav(filePath, samples, sampleRate = SAMPLE_RATE) {
  const frameCount = samples.length;
  const dataSize = frameCount * SAMPLE_WIDTH;
  const buffer = Buffer.alloc(44 + dataSize);

  buffer.write("RIFF", 0);
  buffer.writeUInt32LE(36 + dataSize, 4);
  buffer.write("WAVE", 8);
  buffer.write("fmt ", 12);
  buffer.writeUInt32LE(16, 16);
  buffer.writeUInt16LE(1, 20);
  buffer.writeUInt16LE(CHANNELS, 22);
  buffer.writeUInt32LE(sampleRate, 24);
  buffer.writeUInt32LE(sampleRate * CHANNELS * SAMPLE_WIDTH, 28);
  buffer.writeUInt16LE(CHANNELS * SAMPLE_WIDTH, 32);
  buffer.writeUInt16LE(SAMPLE_WIDTH * 8, 34);
  buffer.write("data", 36);
  buffer.writeUInt32LE(dataSize, 40);

  for (let i = 0; i < frameCount; i += 1) {
    buffer.writeInt16LE(samples[i], 44 + i * SAMPLE_WIDTH);
  }

  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, buffer);
}

export function writeSilenceWav(filePath, durationSec, sampleRate = SAMPLE_RATE) {
  const frameCount = Math.max(Math.floor(durationSec * sampleRate), sampleRate / 10);
  writePcmWav(filePath, new Int16Array(frameCount), sampleRate);
}
