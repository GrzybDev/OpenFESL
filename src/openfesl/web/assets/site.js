// The motion and keyboard of a game's own site: one menu open at a time, closed by Escape, a
// click outside or another menu, playing its closing first; and the message of the last change,
// which its button closes. Without scripts the menus open and close as disclosures.
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

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
  const menu = document.querySelector("[data-site-menu][open]");
  if (menu && event.key === "Escape") {
    closeMenu(menu, 140, 100);
    menu.querySelector("summary").focus();
  }
});

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
