// @ts-check
/** Learn: a short, static glossary in Navi's voice. General background only, not findings about any data. */

const TERMS = [
  ["Infrared light", "Light with a longer wavelength than your eyes can see. It passes through dust better than visible light, so it reveals cooler and dustier things."],
  ["Spectral band", "A narrow slice of colours. SPHEREx looks at the sky in many infrared bands, so each source can be studied across wavelength."],
  ["Proper motion", "How far a star appears to drift across the sky each year. Nearby stars tend to drift faster."],
  ["Blink comparison", "Flipping between two aligned images. A source that jumps between them may have moved or changed."],
  ["Candidate", "Something that looks like it changed. A candidate is not a discovery: confirming one needs checks and follow-up outside this app."],
  ["Uncertainty", "How much a measurement could be off. A change smaller than its uncertainty may just be noise."],
  ["Illustrative data", "Everything in this demo is sample content made to show how the tools work. It is not real SPHEREx data."],
];

/** @param {HTMLElement} root @returns {() => void} */
export function mount(root) {
  root.innerHTML = `<div class="learn"><div class="learn-grid">${TERMS.map(([t, d]) =>
    `<article class="panel"><h2>${t}</h2><p>${d}</p></article>`).join("")}</div>
    <p><a class="btn" href="#/investigate">Back to investigating</a></p></div>`;
  return () => { root.innerHTML = ""; };
}
