// @ts-check
/**
 * Navi's behaviour as a small finite state machine, kept separate from her flight (navi.js owns
 * the animation; this module only decides which behaviour is active).
 *
 *   IDLE           hovering in place; after ~10–15 s she may start MOVING; on entering IDLE there is a
 *                  25% chance (subject to a cooldown) of IDLE_DIALOGUE
 *   MOVING         flying to a safe destination; on arrival → IDLE
 *   TOUCHED        the user clicked her: autonomy paused, assistant open; on close → IDLE
 *   IDLE_DIALOGUE  a spontaneous remark while hovering; when the bubble closes → IDLE
 *   MISC           contextual events: tutorials, explanations, reactions, warnings; → IDLE when done
 *
 * Priority (one state at a time): TOUCHED > MISC > IDLE_DIALOGUE > MOVING > IDLE.
 * A request for a lower-priority state than the current one is refused, unless the current state
 * has finished (release) or the caller forces it (used only for user-driven corrections).
 */

export const IDLE = "idle", MOVING = "moving", TOUCHED = "touched", IDLE_DIALOGUE = "idle_dialogue", MISC = "misc";
/** @typedef {"idle"|"moving"|"touched"|"idle_dialogue"|"misc"} NaviState */
const PRIORITY = { [IDLE]: 0, [MOVING]: 1, [IDLE_DIALOGUE]: 2, [MISC]: 3, [TOUCHED]: 4 };

/**
 * @param {{ onChange?: (from: NaviState, to: NaviState) => void }} [hooks]
 */
export function createStateMachine(hooks = {}) {
  /** @type {NaviState} */ let state = IDLE;
  let since = Date.now();
  /** @type {{ from: NaviState, to: NaviState, at: number }[]} */ const history = [];

  function enter(/** @type {NaviState} */ to) {
    const from = state;
    if (from === to) return;
    state = to; since = Date.now();
    history.push({ from, to, at: since }); if (history.length > 50) history.shift();
    hooks.onChange?.(from, to);
  }

  return {
    get state() { return state; },
    /** Milliseconds spent in the current state. */
    get age() { return Date.now() - since; },
    get history() { return history.slice(); },
    is(/** @type {NaviState[]} */ ...s) { return s.includes(state); },
    /** Can `to` take over right now? (Equal priority is allowed: e.g. one MISC event replaces another.) */
    allows(/** @type {NaviState} */ to) { return PRIORITY[to] >= PRIORITY[state]; },
    /**
     * Ask to enter a state. Returns false (and changes nothing) if a higher-priority state is active.
     * @param {NaviState} to @param {{ force?: boolean }} [o]
     */
    request(to, o = {}) {
      if (!o.force && PRIORITY[to] < PRIORITY[state]) return false;
      enter(to); return true;
    },
    /** The current state has finished: go back to IDLE, but only if we are still in `from`. */
    release(/** @type {NaviState} */ from) {
      if (state !== from) return false;
      enter(IDLE); return true;
    },
  };
}
