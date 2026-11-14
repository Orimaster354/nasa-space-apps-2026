// @ts-check
/**
 * Landing page behaviour: the intro is a short staged fade-in that can be skipped
 * (button or any key) and is not played at all with prefers-reduced-motion.
 * Navi (the same companion as in the workspace) and the shared pixel landscape are mounted here.
 */

import { mountNavi } from "./components/navi.js";
import { mountScene } from "./components/scene.js";

mountScene(document.getElementById("scene"));
const navi = mountNavi({
  inApp: false,
  // Her whisper must never cover the art, the headline, the call to action or the navigation.
  avoid: ".ship, .planet, .hero-copy h1, .hero-copy .lede, .cta-row, .skip, .demo-note, .hero-nav, .down, .skip-intro, .m-intro h2, .m-lede, .m-points, .m-figure, .steps, .m-close, .m-how-head h2",
  panelAvoid: ".ship, .hero-copy, .hero-nav, .m-figure",
  critical: ".ship, .planet, .hero-copy, .m-figure",          // she hops rather than flying across these
});

// A few lines of her own on the landing page (all respect the Comments setting).
setTimeout(() => navi.say("psst… did that star just move?", { kind: "ambient" }), 1600);
const mission = document.getElementById("mission");
if (mission && "IntersectionObserver" in window) {
  let said = false;
  new IntersectionObserver(es => {
    if (said || !es.some(e => e.isIntersecting)) return;
    said = true; setTimeout(() => navi.say("Same sky, different nights. That's the whole trick.", { kind: "ambient" }), 1200);
  }, { threshold: 0.35 }).observe(mission);
}

// "Begin exploring" (and the other ways into the workspace): Navi leads the way, then reappears
// in the workspace exactly where she left off and welcomes the explorer.
document.querySelectorAll('a[href^="app.html"]').forEach(a => a.addEventListener("click", e => {
  const me = /** @type {MouseEvent} */ (e);
  if (me.metaKey || me.ctrlKey || me.shiftKey || me.button !== 0) return;     // let "open in new tab" work normally
  e.preventDefault();
  navi.handoff(/** @type {HTMLElement} */ (a), /** @type {HTMLAnchorElement} */ (a).href);
}));

const hero = /** @type {HTMLElement} */ (document.querySelector(".hero"));
const skip = /** @type {HTMLButtonElement} */ (document.getElementById("skip-intro"));

function endIntro() { hero.classList.add("no-intro"); skip.hidden = true; }

if (matchMedia("(prefers-reduced-motion: reduce)").matches) endIntro();
else {
  const t = window.setTimeout(endIntro, 2600);                  // intro is short, then the skip button goes away
  const cancel = () => { clearTimeout(t); endIntro(); };
  skip.addEventListener("click", cancel);
  window.addEventListener("keydown", cancel, { once: true });
}
