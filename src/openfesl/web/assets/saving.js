// Blocks of the admin panel that save as soon as they change: their form is sent in the
// background, and the block that the answer's page holds under the same id takes the old one's
// place with the toast the answer brings. A change that was not saved says so in a toast
// that stays.

/**
 * Sends a form in the background and reads the page it answers with.
 *
 * @param {HTMLFormElement} form - The form.
 * @param {FormData} [body] - What it sends, when read before the form changed.
 * @returns {Promise<Document | null>} The page, or null when the change was not saved.
 */
export async function post(form, body = new FormData(form)) {
  try {
    const answer = await fetch(form.action, { method: "POST", body });
    return answer.ok ? new DOMParser().parseFromString(await answer.text(), "text/html") : null;
  } catch {
    return null;
  }
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
