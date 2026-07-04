// Client-side filmstrip extraction. Seeks a (preferably same-origin / blob:)
// video URL at evenly spaced points and snapshots each frame to a data URL, so
// the timeline can render a CapCut-style frame strip across a clip.
//
// Canvas tainting: drawImage of a cross-origin <video> without CORS produces a
// tainted canvas that throws on toDataURL. Callers should pass blob: URLs
// (obtained via the Rust fetch_video_b64 bridge) to stay same-origin.

interface FilmstripOptions {
  /** number of frames to extract */
  count: number;
  /** start time within the source, seconds */
  fromSec: number;
  /** end time within the source, seconds */
  toSec: number;
  /** output frame height in px (width follows aspect) */
  height?: number;
}

const stripCache = new Map<string, Promise<string[]>>();

function cacheKey(src: string, o: FilmstripOptions): string {
  return `${src}|${o.count}|${o.fromSec.toFixed(2)}|${o.toSec.toFixed(2)}|${o.height ?? 44}`;
}

export function extractFilmstrip(src: string, options: FilmstripOptions): Promise<string[]> {
  const key = cacheKey(src, options);
  const cached = stripCache.get(key);
  if (cached) return cached;
  const job = runExtraction(src, options).catch(() => [] as string[]);
  stripCache.set(key, job);
  return job;
}

function runExtraction(src: string, { count, fromSec, toSec, height = 44 }: FilmstripOptions): Promise<string[]> {
  return new Promise((resolve, reject) => {
    const frameCount = Math.max(1, Math.min(40, Math.round(count)));
    const video = document.createElement("video");
    video.preload = "auto";
    video.muted = true;
    video.crossOrigin = "anonymous";
    video.src = src;

    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      reject(new Error("no 2d context"));
      return;
    }

    const frames: string[] = [];
    let index = 0;
    let aspect = 16 / 9;
    let settled = false;

    const cleanup = () => {
      video.removeAttribute("src");
      video.load();
    };
    const fail = (err: unknown) => {
      if (settled) return;
      settled = true;
      cleanup();
      reject(err instanceof Error ? err : new Error(String(err)));
    };
    const finish = () => {
      if (settled) return;
      settled = true;
      cleanup();
      resolve(frames);
    };

    const span = Math.max(0, toSec - fromSec);
    const timeFor = (i: number) =>
      frameCount === 1 ? fromSec + span / 2 : fromSec + (span * i) / (frameCount - 1);

    const onLoaded = () => {
      if (video.videoWidth && video.videoHeight) aspect = video.videoWidth / video.videoHeight;
      canvas.height = height;
      canvas.width = Math.max(1, Math.round(height * aspect));
      seekNext();
    };

    const seekNext = () => {
      if (index >= frameCount) {
        finish();
        return;
      }
      const t = timeFor(index);
      const clamped = Math.max(0, Math.min(t, (video.duration || toSec) - 0.05));
      // A no-op seek won't fire `seeked`; nudge it.
      if (Math.abs(video.currentTime - clamped) < 0.001) {
        video.currentTime = clamped + 0.01;
      } else {
        video.currentTime = clamped;
      }
    };

    const onSeeked = () => {
      try {
        ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
        frames.push(canvas.toDataURL("image/jpeg", 0.62));
      } catch (err) {
        fail(err);
        return;
      }
      index += 1;
      seekNext();
    };

    video.addEventListener("loadedmetadata", onLoaded, { once: true });
    video.addEventListener("seeked", onSeeked);
    video.addEventListener("error", () => fail(new Error("video load error")), { once: true });

    // Safety timeout so a stuck decode never hangs the cache entry forever.
    window.setTimeout(() => {
      if (!settled && frames.length > 0) finish();
      else fail(new Error("filmstrip timeout"));
    }, 15000);
  });
}

/** Extract a single poster frame (data URL) near the given time. */
export async function extractPoster(src: string, atSec: number, height = 72): Promise<string | null> {
  const frames = await extractFilmstrip(src, { count: 1, fromSec: atSec, toSec: atSec, height });
  return frames[0] ?? null;
}
