// Blocks of the admin panel that save as soon as they change: their form is sent in the
// background, and the block that the answer's page holds under the same id takes the old one's
// place with the toast the answer brings. A change that was not saved says so in a toast
// that stays.
import { fetchPage } from "./shared.js";

/**
 * Sends a form in the background and reads the page it answers with.
 *
 * @param {HTMLFormElement} form - The form.
 * @param {FormData} [body] - What it sends, when read before the form changed.
 * @returns {Promise<Document | null>} The page, or null when the change was not saved.
 */
export function post(form, body = new FormData(form)) {
  return fetchPage(form.action, { method: "POST", body }).catch(() => null);
}

/**
 * Turns a glyph into the spinning segment and says the change is being saved, in the state word.
 *
 * @param {Element} glyph - The glyph.
 * @param {Element} word - The state word, muted meanwhile.
 */
export function spin(glyph, word) {
  const use = glyph.querySelector("use");
  glyph.dataset.glyph = use.getAttribute("href");
  use.setAttribute("href", glyph.dataset.glyph.replace(/#.*$/, "#busy"));
  glyph.classList.add("spinning");
  word.dataset.word = word.textContent;
  word.textContent = word.dataset.saving;
  word.classList.add("bar-extra-off");
}

/**
 * Puts a page's copy of a block in the block's place and shows the page's toast.
 *
 * @param {Element} section - The block, which the page holds under the same id.
 * @param {Document} page - The page.
 * @returns {Element} The block put in place.
 */
export function swap(section, page) {
  const placed = document.importNode(page.getElementById(section.id), true);
  section.replaceWith(placed);
  const said = page.querySelector(".toast");
  if (said) {
    toast(document.importNode(said, true));
  }
  return placed;
}

/** Says in a toast that stays that the change was not saved. */
export function toastFailed() {
  toast(document.querySelector("[data-save-failed]").content.cloneNode(true));
}

/**
 * Shows a toast at the bottom right of the window.
 *
 * @param {Node} shown - The toast.
 */
function toast(shown) {
  let place = document.querySelector(".toasts");
  if (!place) {
    place = document.createElement("div");
    place.className = "toasts";
    document.getElementById("area").append(place);
  }
  place.replaceChildren(shown);
  place.dispatchEvent(new Event("swapped", { bubbles: true }));
}
