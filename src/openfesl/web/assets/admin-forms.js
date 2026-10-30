// The admin panel's forms that ask for a note, whose dialogs open over their pages: what each
// still needs is said beside its button as it is filled in, and a new name is checked as it is
// typed. Without scripts the browser and the server check the same.
import { checkName, fill, flash } from "./naming.js";

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
});

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
  form.querySelector("[data-why]").textContent = missing
    ? fill(form.dataset.still, { what: missing.dataset.needed })
    : "";
  form.querySelector("button[type=submit]").setAttribute("aria-disabled", String(!!missing));
  return !!missing;
}
