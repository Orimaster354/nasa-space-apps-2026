// @ts-check
/**
 * Achievements page: every badge, which are unlocked, and progress on the counted ones. Also exports the small "unlocked" toast the app shell shows.
 * Stored in this browser only (see core/achievements.js).
 */

import { getAchievements, subscribeAchievements, unlockedCount, resetAchievements, ACHIEVEMENTS } from "../core/achievements.js";
import { esc } from "../core/dom.js";

const when = (/** @type {string} */ iso) => { try { return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" }); } catch { return ""; } };

/** @param {ReturnType<typeof getAchievements>[number]} a */
function badge(a) {
  const done = !!a.unlockedAt;
  const prog = a.goal && !done ? `<div class="ach-bar" role="img" aria-label="${a.progress ?? 0} of ${a.goal}"><span style="width:${Math.min(100, ((a.progress ?? 0) / a.goal) * 100)}%"></span></div>
      <p class="fine muted">${a.progress ?? 0} of ${a.goal}</p>` : "";
  return `<li class="ach ${done ? "is-done" : "is-locked"}">
    <span class="ach-glyph" aria-hidden="true">${done ? esc(a.glyph) : "·"}</span>
    <div><h3>${esc(a.title)} ${done ? `<span class="sr-only">(unlocked)</span>` : `<span class="sr-only">(locked)</span>`}</h3>
      <p class="fine">${esc(a.text)}</p>${prog}
      ${done ? `<p class="fine muted">Unlocked ${esc(when(/** @type {string} */ (a.unlockedAt)))}</p>` : ""}</div></li>`;
}

/** @param {HTMLElement} root @returns {() => void} */
export function mount(root) {
  function render() {
    const all = getAchievements(), n = unlockedCount(), total = ACHIEVEMENTS.length;
    root.innerHTML = `<div class="achievements">
      <section class="panel"><h2>Your progress</h2>
        <p><strong>${n}</strong> of ${total} achievements unlocked</p>
        <div class="ach-bar ach-bar-big" role="img" aria-label="${n} of ${total} unlocked"><span style="width:${(n / total) * 100}%"></span></div>
        <p class="fine muted">Achievements are earned by doing real things in the app, like finishing Navi's tour or completing a comparison. They are stored only in this browser.
          <button class="btn" id="ach-reset" type="button">Reset progress</button></p></section>
      <ul class="ach-grid">${all.map(badge).join("")}</ul></div>`;
    root.querySelector("#ach-reset")?.addEventListener("click", () => { if (window.confirm("Reset all achievements in this browser?")) resetAchievements(); });
  }
  render();
  const off = subscribeAchievements(() => render());
  return () => { off(); root.innerHTML = ""; };
}

/** Shows "Achievement unlocked" for a few seconds. Called once by the app shell. */
export function mountToasts() {
  const el = document.createElement("div");
  el.className = "ach-toast"; el.setAttribute("role", "status"); el.setAttribute("aria-live", "polite"); el.hidden = true;
  document.body.append(el);
  let timer = 0;
  const countEl = () => document.getElementById("ach-count");
  const updateCount = () => { const c = countEl(); if (c) c.textContent = `${unlockedCount()}/${ACHIEVEMENTS.length}`; };
  updateCount();
  subscribeAchievements(a => {
    updateCount();
    if (!a) return;
    el.innerHTML = `<span class="ach-glyph" aria-hidden="true">${esc(a.glyph)}</span><div><strong>Achievement unlocked</strong><br>${esc(a.title)}<span class="fine muted"> · ${esc(a.text)}</span></div>
      <a class="fine" href="#/achievements">See all</a>`;
    el.hidden = false; el.classList.remove("is-shown"); void el.offsetWidth; el.classList.add("is-shown");
    clearTimeout(timer); timer = window.setTimeout(() => { el.classList.remove("is-shown"); window.setTimeout(() => { el.hidden = true; }, 300); }, 5200);
  });
}
