// A game's own server mark on the settings page: its box saves once it changes, without asking,
// and stays disabled while the request runs; one that takes longer than 300 ms turns the box into
// the spinning segment and the state word into Saving… The saved block comes in place with
// its toast; a change that was not saved leaves the block as it was and says so in a toast.
import { post, swap, toastFailed } from "./saving.js";

document.addEventListener("change", (event) => {
  const form = event.target instanceof Element ? event.target.closest("form[data-mark]") : null;
  if (form) {
    saveMark(form);
  }
});

/**
 * Saves the state of a game's own server mark that its box shows now.
 *
 * @param {HTMLFormElement} form - The mark's form.
 */
async function saveMark(form) {
  const section = form.closest("[data-mark-section]");
  // The block as the server last drew it, with the box back in the saved state.
  const saved = section.cloneNode(true);
  const kept = saved.querySelector("input[name=shown]");
  kept.checked = kept.defaultChecked;
  const box = form.elements.namedItem("shown");
  const body = new FormData(form);
  // Read before the box is disabled, which takes the focus from it.
  const focused = section.contains(document.activeElement);
  box.disabled = true;
  const timer = setTimeout(() => spin(section), 300);
  const page = await post(form, body);
  clearTimeout(timer);
  const placed = page?.getElementById(section.id) ? swap(section, page) : null;
  if (!placed) {
    section.replaceWith(saved);
    toastFailed();
  }
  if (focused) {
    (placed ?? saved).querySelector("input[name=shown]").focus();
  }
}

/**
 * Turns the mark's box into the spinning segment and says the change is being saved.
 *
 * @param {Element} section - The mark's block.
 */
function spin(section) {
  section.setAttribute("aria-busy", "true");
  const glyph = section.querySelector(".check-box svg");
  const use = glyph.querySelector("use");
  use.setAttribute("href", use.getAttribute("href").replace(/#.*$/, "#busy"));
  glyph.classList.add("spinning");
  const word = section.querySelector("[data-mark-word]");
  word.textContent = word.dataset.saving;
  word.classList.add("game-mark-off");
}
