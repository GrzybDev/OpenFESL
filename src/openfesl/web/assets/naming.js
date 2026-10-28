// What the dialogs that name a hero share: the check of a name as it is typed, the dialogs'
// closing, the flash of what is still missing, and the page's texts with their placeholders.
export const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

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
    const answer = await fetch(`${address.split("?")[0]}?${query}`);
    if (!answer.ok || number !== asked) {
      return;
    }
    const page = new DOMParser().parseFromString(await answer.text(), "text/html");
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
 * @param {HTMLElement} opener - What opened it, which takes the focus back.
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
 * Flashes the line that says what is still missing.
 *
 * @param {HTMLElement} line - The line.
 */
export function flash(line) {
  line.classList.remove("flash");
  void line.offsetWidth;
  line.classList.add("flash");
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
