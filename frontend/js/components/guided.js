// @ts-check
/**
 * Guided investigation card ("Spot the shift"). It lives inside the workspace and is driven by
 * the SAME events the rest of the app records (observation_viewed, comparison_created), not by a
 * separate quiz state machine. Content (steps, hints, answer) comes from the API client
 * (PROPOSED operation getGuidedInvestigation; mock only today).
 */

import { api } from "../api/client.js";
import { getState, update, subscribe } from "../core/store.js";
import { esc } from "../core/dom.js";
import { emit } from "../core/events.js";

/** @type {Promise<any>|null} */ let defPromise = null;
/** Guided definition (cached). Resolves to null if the backend does not provide it. */
export function loadGuided() {
  defPromise ??= api.getGuidedInvestigation("spot-the-shift").catch(() => null);
  return defPromise;
}

/** Short line for Navi's speech bubble, based on progress. */
export function guidedHint(/** @type {any} */ def, /** @type {any} */ g) {
  if (!def) return "Spot the shift.";
  if (!g) return "Spot the shift.";
  if (g.done) return "You did it!";
  return def.steps[g.step]?.prompt.split(".")[0] + ".";
}

/**
 * @param {HTMLElement} root
 * @param {{ ensureTarget: (objectId: string) => Promise<void>, getCandidates: () => any[] }} host
 */
export async function mountGuided(root, host) {
  const def = await loadGuided();
  root.classList.add("panel", "guided");
  let feedback = "";

  /** Steps are completed by real workspace events recorded after the guide began. */
  function progress() {
    const s = getState(), g = s.guided;
    if (!g || g.done || !def) return;
    const events = s.history.slice(/** @type {any} */ (g).base ?? 0);
    if (g.step === 0 && new Set(events.filter(e => e.type === "observation_viewed").map(e => e.targetId)).size >= 3) {
      feedback = def.steps[0].done; update({ guided: { ...g, step: 1 } });
    } else if (g.step === 2 && events.some(e => e.type === "comparison_created")) {
      feedback = def.steps[2].done; update({ guided: { ...g, done: true } });
    }
  }

  function render() {
    const g = getState().guided;
    if (!def) { root.innerHTML = `<h2>Guided investigation</h2><p class="muted">Not available from the backend yet.</p>`; return; }
    if (!g) {
      root.innerHTML = `<h2>${esc(def.title)}</h2><p>${esc(def.intro)}</p>
        <button class="btn" data-a="start">Start guided investigation</button>`;
      return;
    }
    const dots = def.steps.map((/** @type {any} */ st, /** @type {number} */ i) =>
      `<li class="gstep ${i < g.step || g.done ? "is-done" : i === g.step ? "is-now" : ""}" ${i === g.step && !g.done ? 'aria-current="step"' : ""}>
         <span class="gdot" aria-hidden="true">${i < g.step || g.done ? "✓" : i + 1}</span><span class="gname">${esc(st.title)}</span></li>`).join("");
    if (g.done) {
      root.innerHTML = `<h2>${esc(def.title)} · complete</h2><ol class="gsteps">${dots}</ol>
        <p>${esc(def.explanation)}</p><button class="btn" data-a="reset">Restart</button>`;
      return;
    }
    const step = def.steps[g.step];
    const hints = step.hints ? step.hints.slice(0, g.hints).map((/** @type {string} */ h) => `<li>${esc(h)}</li>`).join("") : "";
    root.innerHTML = `<h2>${esc(def.title)}</h2>
      <p class="progress-label label">Step ${g.step + 1} of ${def.steps.length}</p>
      <ol class="gsteps">${dots}</ol>
      <p>${esc(step.prompt)}</p>
      ${hints ? `<ul class="hints">${hints}</ul>` : ""}
      <div class="guided-actions">
        ${step.hints && g.hints < step.hints.length ? `<button class="btn" data-a="hint">Hint</button>` : ""}
        ${g.step === 2 ? `<button class="btn" data-a="blink">Blink two dates</button>` : ""}
        <button class="btn" data-a="reset">Exit</button>
      </div>
      <p class="feedback" role="status">${esc(feedback)}</p>`;
  }

  root.addEventListener("click", async e => {
    const a = /** @type {HTMLElement} */ (e.target).closest("[data-a]")?.getAttribute("data-a");
    const g = getState().guided;
    if (a === "start") {
      await host.ensureTarget(def.objectId);
      feedback = ""; update({ guided: { id: def.guidedId, step: 0, hints: 0, done: false, base: getState().history.length } });
    } else if (a === "hint" && g) { update({ guided: { ...g, hints: g.hints + 1 } }); }
    else if (a === "reset") { feedback = ""; update({ guided: null }); }
    else if (a === "blink") { update({ compareMode: "blink" }); }
  });

  const unsub = subscribe((_s, patch) => {
    if ("history" in patch || "guided" in patch) { progress(); render(); }
  });
  render(); progress();

  return {
    /** The viewer was clicked. Only meaningful during the "find" step. */
    handleViewerClick(/** @type {number} */ x, /** @type {number} */ y) {
      const g = getState().guided;
      if (!g || g.done || !def || def.steps[g.step]?.id !== "find") return false;
      const answer = host.getCandidates().find(c => c.candidateId === def.answerCandidateId);
      const hit = !!answer && answer.track.some((/** @type {any} */ t) => Math.hypot(t.pixel.x - x, t.pixel.y - y) <= def.tolerancePx);
      feedback = hit ? def.steps[1].correct : def.steps[1].incorrect;
      emit(hit ? "guided_right" : "guided_wrong");
      if (hit) update({ guided: { ...g, step: 2 } }); else render();
      return true;
    },
    /** True while candidate markers must stay hidden (so the answer is not given away). */
    hidingMarkers() { const g = getState().guided; return !!g && !g.done && def?.steps[g.step]?.id === "find"; },
    destroy() { unsub(); root.innerHTML = ""; root.classList.remove("panel", "guided"); },
  };
}
