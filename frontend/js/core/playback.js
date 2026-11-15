// @ts-check
/**
 * Playback engine for blinking through observations. Pure timing logic: it only asks the
 * owner for the current index and tells it the next one, so the viewer, timeline and
 * Compare blink all share the same behaviour and stay synchronised through the store.
 */

export const SPEEDS = [0.5, 1, 2, 4];
const BASE_MS = 1000;

/**
 * @param {{ count: () => number, get: () => number, set: (i: number) => void, onChange?: () => void }} host
 */
export function createPlayback(host) {
  let timer = 0, playing = false, speed = 1;

  const tick = () => {
    const n = host.count();
    if (n < 2) return stop();
    host.set((host.get() + 1) % n);             // loops
    schedule();
  };
  const schedule = () => { clearTimeout(timer); timer = window.setTimeout(tick, BASE_MS / speed); };

  function play() { if (host.count() < 2) return; playing = true; schedule(); host.onChange?.(); }
  function pause() { playing = false; clearTimeout(timer); host.onChange?.(); }
  function stop() { pause(); if (host.count()) host.set(0); }
  function toggle() { playing ? pause() : play(); }
  function setSpeed(/** @type {number} */ s) { speed = s; if (playing) schedule(); host.onChange?.(); }
  function step(/** @type {number} */ d) { const n = host.count(); if (n) host.set((host.get() + d + n) % n); }

  return { play, pause, stop, toggle, setSpeed, step, destroy: pause,
           get playing() { return playing; }, get speed() { return speed; } };
}
