// The heroes page's dialogs that give a hero a new name: Rename opens its hero's dialog instead of
// the page that holds the same form, the name is checked as it is typed, and the button stays
// unavailable, saying why, until the name is free and the account has the price.
import { checkName, closeDialog, decode, flash } from "./naming.js";

document.addEventListener("DOMContentLoaded", () => {
  for (const dialog of document.querySelectorAll("[data-rename]")) {
    rename(dialog);
  }
});

/**
 * Brings a hero's rename dialog to life.
 *
 * @param {HTMLDialogElement} dialog - The dialog.
 */
function rename(dialog) {
  const opener = document.querySelector(`[data-opens="${dialog.id}"]`);
  const form = dialog.querySelector("form");
  const input = form.querySelector("[name=name]");
  const submit = form.querySelector("[data-submit]");
  const why = form.querySelector("[data-why]");
  const texts = dialog.dataset;
  // An account with too few Funds can never send it, which the page says already.
  const short = submit.getAttribute("aria-disabled") === "true";
  let check = "";

  opener.addEventListener("click", (event) => {
    event.preventDefault();
    dialog.showModal();
    input.focus();
  });
  for (const button of dialog.querySelectorAll("[data-close]")) {
    button.addEventListener("click", () => closeDialog(dialog, opener));
  }
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDialog(dialog, opener);
  });
  checkName(dialog, input, texts.checkAddress, texts.checking, (found) => {
    check = found;
    ready();
  });

  /** Says what the rename still needs, and shows it unavailable until the name is free. */
  function ready() {
    if (short) {
      return;
    }
    submit.setAttribute("aria-disabled", String(check !== "free"));
    why.textContent = check === "free" ? "" : decode(texts.needsFree);
  }
  ready();

  form.addEventListener("submit", (event) => {
    if (submit.getAttribute("aria-disabled") === "true") {
      event.preventDefault();
      flash(why);
      input.focus();
    }
  });
}
