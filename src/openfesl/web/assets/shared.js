// What the pages' scripts share: pages fetched in the background, the check of a hero's name as
// it is typed, the dialogs' closing, the flash of what is still missing, and the page's texts with
// their placeholders.
export const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

/**
 * Fetches a page of the site, following its redirect, as a document.
 *
 * @param {string | URL} address - The page's address.
 * @param {RequestInit} [options] - How to fetch it.
 * @returns {Promise<Document>} The page; a page the server refuses fails as a lost connection does.
 */
export async function fetchPage(address, options) {
  const answer = await fetch(address, options);
  if (!answer.ok) {
    throw new Error("failed");
  }
  return new DOMParser().parseFromString(await answer.text(), "text/html");
}

/**
 * Checks a dialog's name field as it is typed, swapping in the server's status line.
 *
 * @param {HTMLDialogElement} dialog - The dialog, whose [data-check] element is the status line.
 * @param {HTMLInputElement} input - The name field.
 * @param {string} address - The address of the check, with its query so far.
 * @param {string} checking - The line shown while the check runs, with %(name)s.
 * @param {function(string): void} changed - Called with what the name meets, empty while unknown.
 */
export function checkName(dialog, input, address, checking, changed) {
  let timer;
  let asked = 0;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const name = input.value.trim();
    const status = dialog.querySelector("[data-check]");
    status.dataset.check = "";
    status.textContent = name ? fill(checking, { name }) : "";
    input.removeAttribute("aria-invalid");
    changed("");
    if (name) {
      timer = setTimeout(() => ask(name), 250);
    }
  });

  /**
   * Asks the server what a name meets and shows its answer, unless the name changed since.
   *
   * @param {string} name - The name.
   */
  async function ask(name) {
    const number = ++asked;
    const query = new URLSearchParams(address.split("?")[1] ?? "");
    query.set("name", name);
    const page = await fetchPage(`${address.split("?")[0]}?${query}`).catch(() => null);
    if (!page || number !== asked) {
      return;
    }
    const found = page.querySelector("[data-check]");
    // The status line stays the same element: screen readers announce a live region's new
    // content, not a new region.
    const status = dialog.querySelector("[data-check]");
    status.dataset.check = found.dataset.check;
    status.replaceChildren(...found.childNodes);
    status.animate([{ opacity: 0 }, { opacity: 1 }], {
      duration: reducedMotion.matches ? 100 : 120,
    });
    input.setAttribute("aria-invalid", String(found.dataset.check !== "free"));
    changed(found.dataset.check);
  }
}

/**
 * Plays a dialog's closing, closes it and returns to what opened it.
 *
 * @param {HTMLDialogElement} dialog - The open dialog.
 * @param {HTMLElement} [opener] - What opened it, which takes the focus back.
 */
export function closeDialog(dialog, opener) {
  dialog.classList.add("closing");
  setTimeout(
    () => {
      dialog.classList.remove("closing");
      dialog.close();
      opener?.focus();
    },
    reducedMotion.matches ? 100 : 120,
  );
}

/**
 * Lets a dialog close from its close buttons and Escape, returning the focus to its opener.
 *
 * @param {HTMLDialogElement} dialog - The dialog.
 * @param {HTMLElement | null} [opener] - What takes the focus back when nothing else opened it.
 */
export function closes(dialog, opener) {
  dialog.addEventListener("click", (event) => {
    if (event.target.closest("[data-close]")) {
      closeDialog(dialog, dialog.opener ?? opener);
    }
  });
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDialog(dialog, dialog.opener ?? opener);
  });
}

/**
 * Opens a black-headed panel as a dialog over the page, in the colours of the area its opener is in.
 *
 * @param {Node} panel - The panel, which holds the question and text that name the dialog.
 * @param {HTMLElement} opener - What takes the focus back once the dialog is gone.
 * @returns {HTMLDialogElement} The open dialog.
 */
export function openPanel(panel, opener) {
  const dialog = document.createElement("dialog");
  dialog.className = "dialog";
  dialog.setAttribute("aria-labelledby", "confirm-question");
  dialog.setAttribute("aria-describedby", "confirm-text");
  dialog.append(panel);
  opener.closest(".area").append(dialog);
  dialog.addEventListener("close", () => {
    dialog.remove();
    opener.focus();
  });
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDialog(dialog);
  });
  dialog.showModal();
  return dialog;
}

/**
 * Plays an element's animation again: the flash of what is still missing, unless another is named.
 *
 * @param {HTMLElement} element - The element.
 * @param {string} [name] - The class that plays the animation.
 */
export function flash(element, name = "flash") {
  element.classList.remove(name);
  void element.offsetWidth;
  element.classList.add(name);
}

/**
 * Fills a text's placeholders, written %(name)s, with values.
 *
 * @param {string} text - The text, as the page gave it.
 * @param {Object<string, string|number>} values - The values by placeholder.
 * @returns {string} The text with its values, as plain text.
 */
export function fill(text, values) {
  return decode(text).replace(/%\((\w+)\)s/g, (_, name) => String(values[name] ?? ""));
}

/**
 * Returns the plain text of a text that the page escaped for HTML.
 *
 * @param {string} text - The escaped text.
 * @returns {string} The plain text.
 */
export function decode(text) {
  return new DOMParser().parseFromString(text, "text/html").documentElement.textContent;
}
