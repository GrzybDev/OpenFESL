// The admin panel's forms that ask for a note, whose dialogs open over their pages: what each
// still needs is said beside its button as it is filled in, a new name is checked as it is typed,
// and a form's search follows its choices. Without scripts the browser and the server check the
// same, and a search is sent with its own button.
import { checkName, fill, flash, reducedMotion } from "./naming.js";

// A dialog arrives after the page has loaded, so its name field starts its check when it first
// takes the focus, as its autofocus gives it.
document.addEventListener("focusin", (event) => {
  const input = event.target instanceof Element ? event.target.closest("[data-name-check]") : null;
  if (!input || input.dataset.started) {
    return;
  }
  input.dataset.started = "true";
  checkName(input.form, input, input.dataset.nameCheck, input.dataset.checking, () =>
    update(input.form),
  );
});

document.addEventListener("input", (event) => {
  const form = event.target instanceof Element ? event.target.closest("[data-admin-form]") : null;
  if (form) {
    update(form);
  }
  // A field of a form's search, such as the item's name, is followed a moment after typing.
  const field = event.target instanceof Element ? event.target.closest("[data-refresh]") : null;
  if (field) {
    clearTimeout(refreshing);
    refreshing = setTimeout(() => refresh(field, chosen(field)), 250);
  }
});

document.addEventListener("change", (event) => {
  if (
    event.target instanceof HTMLInputElement &&
    event.target.matches("[data-live] [type=radio]")
  ) {
    refresh(event.target, chosen(event.target));
  }
});

// A search's result is chosen in place, as its link would choose it on the page of its own.
document.addEventListener("click", (event) => {
  const link =
    event.target instanceof Element ? event.target.closest("[data-live] [data-choose]") : null;
  if (link && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey) {
    event.preventDefault();
    refresh(link, new URL(link.href).searchParams);
  }
});

let refreshing;
let asked = 0;

/**
 * Returns what a form's search has chosen so far, as the fields its page reads.
 *
 * @param {Element} field - A field of the form.
 * @returns {URLSearchParams} The fields.
 */
function chosen(field) {
  const form = field.closest("[data-confirm]");
  const fields = new URLSearchParams();
  for (const input of form.querySelectorAll("[name=q], [data-live] [name]")) {
    if (input.name !== "csrf" && (input.type !== "radio" || input.checked)) {
      fields.set(input.name, input.value);
    }
  }
  return fields;
}

/**
 * Shows what the server says of a form's choices in its live part, keeping the focus in place.
 *
 * @param {Element} field - The field or link that changed the choices.
 * @param {URLSearchParams} fields - The choices.
 */
async function refresh(field, fields) {
  const live = field.closest("[data-confirm]").querySelector("[data-live]");
  const number = ++asked;
  const answer = await fetch(`${live.dataset.live}?${fields}`).catch(() => null);
  if (!answer?.ok || number !== asked) {
    return;
  }
  const page = new DOMParser().parseFromString(await answer.text(), "text/html");
  const found = page.querySelector("[data-live]");
  const focused = document.activeElement?.id;
  live.replaceChildren(...found.childNodes);
  live.toggleAttribute("data-blocked", found.hasAttribute("data-blocked"));
  if (found.dataset.blocked) {
    live.dataset.blocked = found.dataset.blocked;
  }
  live.animate([{ opacity: 0.6 }, { opacity: 1 }], { duration: reducedMotion.matches ? 100 : 120 });
  if (focused && live.querySelector(`#${CSS.escape(focused)}`)) {
    live.querySelector(`#${CSS.escape(focused)}`).focus();
  }
  update(live.closest("form"));
}

// A field the browser finds missing is named beside the button instead of in its own bubble.
document.addEventListener(
  "invalid",
  (event) => {
    const form = event.target.closest?.("[data-admin-form]");
    if (form) {
      event.preventDefault();
      update(form);
      flash(form.querySelector(".admin-form-why"));
    }
  },
  true,
);

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (form instanceof HTMLFormElement && form.matches("[data-admin-form]") && update(form)) {
    event.preventDefault();
    flash(form.querySelector(".admin-form-why"));
  }
});

/**
 * Says the first thing a form still needs beside its button, and marks the button unavailable.
 *
 * @param {HTMLFormElement} form - The form.
 * @returns {boolean} Whether something is still needed.
 */
function update(form) {
  const status = form.querySelector("[data-check]");
  const missing = [...form.querySelectorAll("[data-needed]")].find(
    (field) =>
      !field.value.trim() ||
      !field.validity.valid ||
      (field.matches("[data-name-check]") && status?.dataset.check !== "free"),
  );
  const blocked = form.querySelector("[data-blocked]")?.dataset.blocked;
  form.querySelector("[data-why]").textContent =
    blocked ?? (missing ? fill(form.dataset.still, { what: missing.dataset.needed }) : "");
  const stopped = !!missing || !!blocked;
  // Not the button of a search form whose fields sit inside this one.
  form
    .querySelector("button[type=submit]:not([form])")
    .setAttribute("aria-disabled", String(stopped));
  return stopped;
}
