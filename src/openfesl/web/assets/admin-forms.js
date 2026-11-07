// The admin panel's forms that ask for a note, whose dialogs open over their pages, and its editors:
// what each still needs is said beside its button as it is filled in, a new name is checked as it
// is typed, and the live parts of a form, such as its search, follow its choices. Without scripts
// the browser and the server check the same, and a search is sent with its own button.
import { checkName, fill, flash, reducedMotion } from "./naming.js";

// A dialog arrives after the page has loaded, so its name field starts its check when it first
// takes the focus, as its autofocus gives it.
document.addEventListener("focusin", (event) => {
  const input = event.target.closest("[data-name-check]");
  if (!input || input.dataset.started) {
    return;
  }
  input.dataset.started = "true";
  checkName(input.form, input, input.dataset.nameCheck, input.dataset.checking, () =>
    update(input.form),
  );
});

document.addEventListener("input", (event) => {
  const form = event.target.closest("[data-admin-form]");
  if (form) {
    update(form);
  }
  // A field that the live parts follow, such as a search's item name, is followed a moment after
  // typing.
  const field = event.target.closest("[data-refresh]");
  if (field) {
    clearTimeout(refreshing);
    refreshing = setTimeout(() => refresh(field, chosen(field)), 250);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.matches("[data-live] [type=radio]")) {
    refresh(event.target, chosen(event.target));
  }
});

// A search's result is chosen in place, as its link would choose it on the page of its own.
document.addEventListener("click", (event) => {
  const link = event.target.closest("[data-live] [data-choose]");
  if (link && event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey) {
    event.preventDefault();
    refresh(link, new URL(link.href).searchParams);
  }
});

let refreshing;
let asked = 0;

/**
 * Returns what a form has chosen so far, as the fields its page reads.
 *
 * @param {Element} field - A field of the form.
 * @returns {URLSearchParams} The fields.
 */
function chosen(field) {
  const form = field.closest("[data-confirm], form");
  const fields = new URLSearchParams();
  for (const input of form.querySelectorAll(
    "[name=q], [data-live] [name], [data-refresh] [name]",
  )) {
    if (input.name !== "csrf" && (!["radio", "checkbox"].includes(input.type) || input.checked)) {
      fields.append(input.name, input.value);
    }
  }
  return fields;
}

/**
 * Shows what the server says of a form's choices in its live parts, keeping the focus in place.
 *
 * @param {Element} field - The field or link that changed the choices.
 * @param {URLSearchParams} fields - The choices.
 */
async function refresh(field, fields) {
  const lives = [...field.closest("[data-confirm], form").querySelectorAll("[data-live]")];
  const number = ++asked;
  const answer = await fetch(`${lives[0].dataset.live}?${fields}`).catch(() => null);
  if (!answer?.ok || number !== asked) {
    return;
  }
  const page = new DOMParser().parseFromString(await answer.text(), "text/html");
  const founds = page.querySelectorAll("[data-live]");
  const focused = document.activeElement?.id;
  lives.forEach((live, index) => {
    const found = founds[index];
    if (live.innerHTML === found.innerHTML) {
      return;
    }
    live.replaceChildren(...found.childNodes);
    live.toggleAttribute("data-blocked", found.hasAttribute("data-blocked"));
    if (found.dataset.blocked) {
      live.dataset.blocked = found.dataset.blocked;
    }
    live.animate([{ opacity: 0.6 }, { opacity: 1 }], {
      duration: reducedMotion.matches ? 100 : 120,
    });
    if (focused && live.querySelector(`#${CSS.escape(focused)}`)) {
      live.querySelector(`#${CSS.escape(focused)}`).focus();
    }
  });
  const form = lives[0].closest("form");
  if (form.matches("[data-admin-form]")) {
    update(form);
  }
}

// A field the browser finds missing is named beside the button instead of in its own bubble.
document.addEventListener(
  "invalid",
  (event) => {
    const form = event.target.closest("[data-admin-form]");
    if (form) {
      event.preventDefault();
      update(form);
      flash(form.querySelector(".admin-form-why"));
    }
  },
  true,
);

// A button with a name, such as an editor's Add, answers the form without saving it, so only the
// nameless button that saves is held back.
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (form.matches("[data-admin-form]") && !event.submitter?.name && update(form)) {
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
  const count = form.querySelector("[data-count]");
  if (count) {
    follow(count, form);
  }
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
  // The button that saves: not a named one, nor that of a search form inside this one.
  form
    .querySelector("button[type=submit]:not([name], [form])")
    .setAttribute("aria-disabled", String(stopped));
  return stopped;
}

/**
 * Shows what a count holds after a form's grant or take, and blocks one that is not possible.
 *
 * @param {HTMLElement} count - The line of the count now and after.
 * @param {HTMLFormElement} form - The form.
 */
function follow(count, form) {
  const amount = Number(form.querySelector("[name=amount]").value) || 0;
  const taking = form.querySelector("[name=direction]:checked")?.value === "take";
  const now = Number(count.dataset.now);
  const after = taking ? now - amount : now + amount;
  const format = new Intl.NumberFormat(document.documentElement.lang);
  const possible = after >= 0 && after <= Number(count.dataset.most);
  count.querySelector("[data-count-after]").textContent =
    `${format.format(now)} → ${possible ? format.format(after) : count.dataset.impossible}`;
  if (possible) {
    delete count.dataset.blocked;
  } else {
    count.dataset.blocked = taking
      ? count.dataset.fewer
      : fill(count.dataset.more, { after: format.format(after) });
  }
}
