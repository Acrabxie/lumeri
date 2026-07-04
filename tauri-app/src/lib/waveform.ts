// Client-side audio peak extraction for the timeline waveform overlay.
// Fetches the media bytes (blob: / same-origin recommended), decodes with
// WebAudio, then reduces the first channel into `samples` normalized 0..1 peak
// buckets. Decoding is heavy, so results are cached per (src, samples).

const peakCache = new Map<string, Promise<number[]>>();

let sharedCtx: AudioContext | null = null;
function audioCtx(): AudioContext {
  if (!sharedCtx) {
    const Ctor: typeof AudioContext =
      window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    sharedCtx = new Ctor();
  }
  return sharedCtx;
}

export function extractWaveform(src: string, samples = 400): Promise<number[]> {
  const key = `${src}|${samples}`;
  const cached = peakCache.get(key);
  if (cached) return cached;
  const job = runWaveform(src, samples).catch(() => [] as number[]);
  peakCache.set(key, job);
  return job;
}

async function runWaveform(src: string, samples: number): Promise<number[]> {
  const res = await fetch(src);
  if (!res.ok) throw new Error(`waveform fetch ${res.status}`);
  const buf = await res.arrayBuffer();
  const decoded = await audioCtx().decodeAudioData(buf.slice(0));

  const channel = decoded.getChannelData(0);
  const bucketSize = Math.max(1, Math.floor(channel.length / samples));
  const peaks: number[] = new Array(samples).fill(0);

  let max = 0.0001;
  for (let i = 0; i < samples; i++) {
    const start = i * bucketSize;
    const end = Math.min(start + bucketSize, channel.length);
    let peak = 0;
    for (let j = start; j < end; j++) {
      const v = Math.abs(channel[j]);
      if (v > peak) peak = v;
    }
    peaks[i] = peak;
    if (peak > max) max = peak;
  }

  // Normalize to 0..1 against the loudest bucket.
  for (let i = 0; i < samples; i++) peaks[i] = peaks[i] / max;
  return peaks;
}
