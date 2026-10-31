// The store pages' buy panel: choosing another hero or price asks the server for the panel's lower
// part and the confirmation of that choice; Buy opens the confirmation as a dialog; a hot deal's
// time left counts down; a box just bought opens over the page. Without scripts the panel sends
// its choice and the confirmation is a page of its own.
import { closeDialog, fill, flash, reducedMotion } from "./naming.js";

document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-buy]");
  if (form) {
    buyPanel(form);
  }
  for (const timer of document.querySelectorAll("[role=timer][data-left]:not([data-deal] *)")) {
    countDown(timer, endDeal);
  }
  const reveal = document.querySelector("dialog[data-reveal]");
  if (reveal) {
    // The box was bought with the page's Buy button, which takes the focus back.
    closes(reveal, document.querySelector("[data-buy-open]"));
    // Open in the page's flow without scripts, it opens over the page with them.
    reveal.close();
    reveal.showModal();
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
    for (const timer of document.querySelectorAll("[role=timer][data-left]")) {
      if (timer.dataset.ended) {
        endDeal();
      }
    }
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

/**
 * Counts a time left down each second while the page is shown, under a day as a clock alone, and
 * says so once it runs out; it stops once its element leaves the page.
 *
 * @param {HTMLElement} timer - The time left, with the server's seconds to its end and its wording.
 * @param {() => void} ended - Shows what its end changes.
 */
export function countDown(timer, ended) {
  const ends = Date.now() + Number(timer.dataset.left) * 1000;
  const tick = () => {
    if (!timer.isConnected) {
      clearInterval(interval);
      return;
    }
    if (document.hidden) {
      return;
    }
    // Rounded up, so that it runs out no sooner than its end.
    const left = Math.max(Math.ceil((ends - Date.now()) / 1000), 0);
    const clock = [Math.floor((left % 86400) / 3600), Math.floor((left % 3600) / 60), left % 60]
      .map((part) => String(part).padStart(2, "0"))
      .join(":");
    const days = Math.floor(left / 86400);
    timer.textContent = days ? fill(timer.dataset.format, { days, time: clock }) : clock;
    if (!left) {
      clearInterval(interval);
      timer.dataset.ended = "true";
      ended();
    }
  };
  const interval = setInterval(tick, 1000);
  tick();
}

/** Shows that the page's hot deal has ended: its note, and its buy button unavailable. */
function endDeal() {
  document.querySelector("[data-deal-ended]")?.removeAttribute("hidden");
  document.querySelector("[data-summary]")?.remove();
  const buy = document.querySelector("[data-buy-open]");
  const why = document.querySelector("[data-why]");
  if (buy && why) {
    buy.setAttribute("aria-disabled", "true");
    why.textContent = why.dataset.ended;
    why.animate([{ opacity: 0 }, { opacity: 1 }], { duration: reducedMotion.matches ? 100 : 120 });
  }
}
