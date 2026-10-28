// The motion and keyboard of a game's own site: one menu open at a time, closed by Escape, a
// click outside or another menu, playing its closing first; and the message of the last change,
// which its button closes. Without scripts the menus open and close as disclosures.
import { closeDialog, flash, reducedMotion } from "./naming.js";

document.addEventListener("click", (event) => {
  if (!(event.target instanceof Element)) {
    return;
  }
  const summary = event.target.closest("[data-site-menu] > summary");
  for (const menu of document.querySelectorAll("[data-site-menu][open]")) {
    if (summary?.parentElement === menu) {
      event.preventDefault();
      closeMenu(menu, 140, 100);
    } else if (!menu.contains(event.target)) {
      closeMenu(menu, 140, 100);
    }
  }
  const dismiss = event.target.closest("[data-site-dismiss]");
  if (dismiss) {
    const message = dismiss.closest("[data-site-message]");
    // The closed message takes the focus with it, so the page's title takes it over.
    const title = message.parentElement.querySelector("h1");
    if (title && message.contains(document.activeElement)) {
      title.tabIndex = -1;
      title.focus();
    }
    message.classList.add("leaving");
    setTimeout(() => message.remove(), reducedMotion.matches ? 100 : 120);
  }
});

document.addEventListener("keydown", (event) => {
  // An open dialog takes Escape alone, closing and returning the focus to what opened it.
  if (document.querySelector("dialog:modal")) {
    return;
  }
  const menu = document.querySelector("[data-site-menu][open]");
  if (menu && event.key === "Escape") {
    closeMenu(menu, 140, 100);
    menu.querySelector("summary").focus();
  }
  const toggle = document.querySelector("[data-selector-toggle][aria-expanded=true]");
  if (toggle && event.key === "Escape") {
    showSelector(toggle, false);
    toggle.focus();
  }
});

// The hero selector opens under the bar from its toggle, which without scripts is a link to the
// heroes page; as a button it also answers Space.
for (const toggle of document.querySelectorAll("[data-selector-toggle]")) {
  toggle.setAttribute("role", "button");
  toggle.setAttribute("aria-expanded", "false");
  toggle.setAttribute("aria-controls", "hero-selector");
  toggle.addEventListener("click", (event) => {
    event.preventDefault();
    showSelector(toggle, toggle.getAttribute("aria-expanded") !== "true");
  });
  toggle.addEventListener("keydown", (event) => {
    if (event.key === " ") {
      event.preventDefault();
      showSelector(toggle, toggle.getAttribute("aria-expanded") !== "true");
    }
  });
}

// The site's dialogs open from the links that name them, which without scripts lead to pages that
// hold the same forms; a form whose button is unavailable says why instead of being sent.
for (const opener of document.querySelectorAll("[data-opens]")) {
  const dialog = document.getElementById(opener.dataset.opens);
  opener.addEventListener("click", (event) => {
    event.preventDefault();
    dialog.opener = opener;
    dialog.showModal();
    dialog.querySelector("input:not([type=hidden])")?.focus();
  });
}

for (const dialog of document.querySelectorAll("dialog[data-modal]")) {
  for (const button of dialog.querySelectorAll("[data-close]")) {
    button.addEventListener("click", () => closeDialog(dialog, dialog.opener));
  }
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDialog(dialog, dialog.opener);
  });
  dialog.addEventListener("submit", (event) => {
    if (event.submitter?.getAttribute("aria-disabled") === "true") {
      event.preventDefault();
      flash(dialog.querySelector("[data-why]"));
    }
  });
}

// A link that is unavailable flashes the line beside it that says why instead of leading on;
// without scripts it opens its page, which says the same.
document.addEventListener("click", (event) => {
  const link =
    event.target instanceof Element
      ? event.target.closest("a[aria-disabled=true][aria-describedby]")
      : null;
  if (link) {
    event.preventDefault();
    flash(document.getElementById(link.getAttribute("aria-describedby")));
  }
});

/**
 * Opens or closes the hero selector, playing its motion.
 *
 * @param {HTMLElement} toggle - The bar's toggle.
 * @param {boolean} open - Whether to open it.
 */
function showSelector(toggle, open) {
  const selector = document.getElementById("hero-selector");
  toggle.setAttribute("aria-expanded", String(open));
  clearTimeout(selector.closing);
  if (open) {
    selector.hidden = false;
    requestAnimationFrame(() => selector.classList.add("open"));
  } else {
    selector.classList.remove("open");
    selector.closing = setTimeout(
      () => {
        selector.hidden = true;
      },
      reducedMotion.matches ? 100 : 160,
    );
  }
}

/**
 * Plays a menu's closing and closes it.
 *
 * @param {HTMLDetailsElement} menu - The open menu.
 * @param {number} delay - How long its closing plays, in milliseconds.
 * @param {number} reducedDelay - How long its closing plays with reduced motion.
 */
export function closeMenu(menu, delay, reducedDelay) {
  menu.classList.add("closing");
  setTimeout(
    () => {
      menu.open = false;
      menu.classList.remove("closing");
    },
    reducedMotion.matches ? reducedDelay : delay,
  );
}
