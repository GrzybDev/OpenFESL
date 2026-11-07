// The heroes page's dialogs that give a hero a new name: the name is checked as it is typed, and
// the button stays unavailable, saying why, until the name is free and the account has the price.
import { checkName, decode } from "./shared.js";

for (const dialog of document.querySelectorAll("[data-rename]")) {
  const form = dialog.querySelector("form");
  const input = form.querySelector("[name=name]");
  const submit = form.querySelector("[data-submit]");
  const why = form.querySelector("[data-why]");
  const texts = dialog.dataset;
  // An account with too few Funds can never send it, which the page says already.
  const short = submit.getAttribute("aria-disabled") === "true";
  let check = "";

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
}
