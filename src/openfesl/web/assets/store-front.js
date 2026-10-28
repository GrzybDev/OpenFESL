// The store front's filter: with scripts a hero, a category or Owned by applies once chosen and a
// search once it changes, without the page moving. The page that answers the latest choice puts
// its filter and list in place, whose tiles rise and fade in turn, and the address follows;
// screen readers hear the list's heading. Should the answer fail, the browser loads the address
// itself. Without scripts Show items sends the form.
import { announce } from "./announce.js";

const stillMotion = matchMedia("(prefers-reduced-motion: reduce)");
// The address being shown, as Enter in the search both changes it and sends the form.
let pending = "";
// The number of the latest choice: an answer to an earlier one, such as a search that left its
// box as a category was picked, would undo it.
let asked = 0;

document.addEventListener("change", (event) => {
  const form = event.target instanceof Element ? event.target.closest("[data-store-filter]") : null;
  if (form) {
    show(form, event.target);
  }
});

document.addEventListener("submit", (event) => {
  if (event.target instanceof HTMLFormElement && event.target.matches("[data-store-filter]")) {
    event.preventDefault();
    show(event.target, event.target.elements.namedItem("q"));
  }
});

/**
 * Shows the list that the filter's choices make.
 *
 * @param {HTMLFormElement} form - The filter.
 * @param {Element} control - The control that changed, which keeps the keyboard.
 */
async function show(form, control) {
  const address = new URL(form.action, location.href);
  const chosen = new URLSearchParams(new FormData(form));
  for (const [name, value] of [...chosen]) {
    if (!value) {
      chosen.delete(name, value);
    }
  }
  address.search = chosen.toString();
  if (address.href === pending) {
    return;
  }
  pending = address.href;
  const mine = ++asked;
  form.closest("[data-store-front]").setAttribute("aria-busy", "true");
  let page = null;
  try {
    const answer = await fetch(address);
    page = answer.ok ? new DOMParser().parseFromString(await answer.text(), "text/html") : null;
  } catch {
    page = null;
  }
  if (mine !== asked) {
    return;
  }
  pending = "";
  const fresh = page?.querySelector("[data-store-front]");
  if (!fresh) {
    location.assign(address);
    return;
  }
  const placed = document.importNode(fresh, true);
  document.querySelector("[data-store-front]").replaceWith(placed);
  history.replaceState(null, "", address);
  const same = control?.name
    ? (placed.querySelector(`[name="${control.name}"][value="${CSS.escape(control.value)}"]`) ??
      placed.querySelector(`[name="${control.name}"]`))
    : null;
  same?.focus();
  announce(placed.querySelector("#store-results").textContent.replace(/\s+/g, " ").trim());
  rise(placed.querySelectorAll(".store-tiles > li"));
}

/**
 * Has the first tiles of a list rise 12 px and fade in, 30 ms apart.
 *
 * @param {NodeListOf<Element>} tiles - The list's tiles.
 */
function rise(tiles) {
  if (stillMotion.matches) {
    return;
  }
  [...tiles].slice(0, 12).forEach((tile, index) => {
    tile.animate(
      [
        { opacity: 0, transform: "translateY(12px)" },
        { opacity: 1, transform: "none" },
      ],
      { duration: 200, delay: index * 30, easing: "cubic-bezier(.2,.8,.2,1)", fill: "backwards" },
    );
  });
}
