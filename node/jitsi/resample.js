/** Resample PCM to target rate with basic anti-aliasing on decimation. */

export function downmixToMono(samples, channelCount) {
  if (channelCount <= 1) {
    return samples;
  }
  const frames = Math.floor(samples.length / channelCount);
  const mono = new Int16Array(frames);
  for (let i = 0; i < frames; i += 1) {
    let sum = 0;
    for (let c = 0; c < channelCount; c += 1) {
      sum += samples[i * channelCount + c];
    }
    mono[i] = Math.round(sum / channelCount);
  }
  return mono;
}

function decimateInteger(mono, factor) {
  const outLen = Math.floor(mono.length / factor);
  const out = new Int16Array(outLen);
  for (let i = 0; i < outLen; i += 1) {
    let sum = 0;
    for (let j = 0; j < factor; j += 1) {
      sum += mono[i * factor + j] ?? 0;
    }
    out[i] = Math.round(sum / factor);
  }
  return out;
}

function resampleGeneric(mono, sampleRate, targetRate) {
  const ratio = sampleRate / targetRate;
  const outLen = Math.max(1, Math.floor(mono.length / ratio));
  const out = new Int16Array(outLen);
  for (let i = 0; i < outLen; i += 1) {
    const srcPos = i * ratio;
    const idx = Math.floor(srcPos);
    const frac = srcPos - idx;
    const s0 = mono[idx] ?? 0;
    const s1 = mono[idx + 1] ?? s0;
    const s2 = mono[idx + 2] ?? s1;
    const s3 = mono[idx + 3] ?? s2;
    const a = s1 - s0;
    const b = s2 - s1;
    const c = s3 - s2;
    const sample = s1 + 0.5 * a + b - 0.5 * c + frac * (c + 0.5 * a - 1.5 * b + 0.5 * c);
    out[i] = Math.round(sample);
  }
  return out;
}

export function resampleToTarget(mono, sampleRate, targetRate) {
  if (sampleRate === targetRate) {
    return mono;
  }
  if (sampleRate > targetRate && Number.isInteger(sampleRate / targetRate)) {
    return decimateInteger(mono, sampleRate / targetRate);
  }
  return resampleGeneric(mono, sampleRate, targetRate);
}

export function resampleToMono(samples, sampleRate, channelCount, targetRate) {
  const mono = downmixToMono(samples, channelCount);
  return resampleToTarget(mono, sampleRate, targetRate);
}
