// @ts-check
/**
 * Landing page behaviour: the intro is a short staged fade-in that can be skipped
 * (button or any key) and is not played at all with prefers-reduced-motion.
 */

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
