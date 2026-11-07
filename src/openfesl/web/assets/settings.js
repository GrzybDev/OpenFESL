// A game's own server mark on the settings page: its box saves once it changes, without asking,
// and stays disabled while the request runs; one that takes longer than 300 ms turns the box into
// the spinning segment and the state word into Saving… The saved block comes in place with
// its toast; a change that was not saved leaves the block as it was and says so in a toast.
import { post, spin, swap, toastFailed } from "./saving.js";

document.addEventListener("change", (event) => {
  const form = event.target.closest("form[data-mark]");
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
  const timer = setTimeout(() => {
    section.setAttribute("aria-busy", "true");
    spin(section.querySelector(".check-box svg"), section.querySelector("[data-mark-word]"));
  }, 300);
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
