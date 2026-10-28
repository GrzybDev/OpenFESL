// The store pages' buy panel: choosing another hero or price asks the server for the panel's lower
// part and the confirmation of that choice, and Buy opens the confirmation as a dialog. Without
// scripts the panel sends its choice and the confirmation is a page of its own.
import { closeDialog, flash } from "./naming.js";

document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-buy]");
  if (form) {
    buyPanel(form);
  }
});

/**
 * Brings a store page's buy panel to life.
 *
 * @param {HTMLFormElement} form - The panel's form.
 */
function buyPanel(form) {
  const dialog = document.getElementById("buy-confirm");
  let asked = 0;
  closes(dialog, null);

  form.addEventListener("change", async (event) => {
    const changed = event.target;
    if (!(changed instanceof HTMLInputElement) || !["hero", "price"].includes(changed.name)) {
      return;
    }
    const number = ++asked;
    const query = new URLSearchParams();
    for (const name of ["hero", "price"]) {
      const value = form.querySelector(`[name=${name}]:checked`)?.value;
      if (value) {
        query.set(name, value);
      }
    }
    const answer = await fetch(`${location.pathname}?${query}`);
    if (!answer.ok || number !== asked) {
      return;
    }
    const page = new DOMParser().parseFromString(await answer.text(), "text/html");
    const panel = form.querySelector("[data-buy-panel]");
    const found = page.querySelector("[data-buy-panel]");
    panel.replaceChildren(...found.childNodes);
    panel.dataset.hero = found.dataset.hero;
    panel.dataset.price = found.dataset.price;
    const confirmation = page.getElementById("buy-confirm");
    dialog.replaceChildren(...confirmation.childNodes);
    // The dialog is named by its title only while it holds the confirmation.
    const title = confirmation.getAttribute("aria-labelledby");
    if (title) {
      dialog.setAttribute("aria-labelledby", title);
    } else {
      dialog.removeAttribute("aria-labelledby");
    }
    history.replaceState(null, "", `${location.pathname}?${query}`);
    // The tile that was chosen keeps the focus, though the price tiles were drawn again.
    form.querySelector(`[name=${changed.name}][value="${changed.value}"]`)?.focus();
  });

  form.addEventListener("click", (event) => {
    const buy = event.target instanceof Element ? event.target.closest("[data-buy-open]") : null;
    if (!buy) {
      return;
    }
    if (buy.getAttribute("aria-disabled") === "true") {
      event.preventDefault();
      flash(form.querySelector("[data-why]"));
      return;
    }
    // A choice the panel has not caught up with yet goes the way without scripts.
    const panel = form.querySelector("[data-buy-panel]");
    const hero = form.querySelector("[name=hero]:checked")?.value;
    const price = form.querySelector("[name=price]:checked")?.value;
    if (panel.dataset.hero === hero && panel.dataset.price === price) {
      event.preventDefault();
      dialog.opener = buy;
      dialog.showModal();
      dialog.querySelector("[type=submit]")?.focus();
    }
  });
}

/**
 * Lets a dialog close from its close buttons and Escape, returning the focus to its opener.
 *
 * @param {HTMLDialogElement} dialog - The dialog.
 * @param {HTMLElement | null} opener - What takes the focus back when nothing else opened it.
 */
function closes(dialog, opener) {
  dialog.addEventListener("click", (event) => {
    if (event.target instanceof Element && event.target.closest("[data-close]")) {
      closeDialog(dialog, dialog.opener ?? opener);
    }
  });
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDialog(dialog, dialog.opener ?? opener);
  });
}
