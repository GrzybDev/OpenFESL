// The treasure chests page: the reveal of the chest just opened comes up as a dialog over the
// page and gives the focus back to the hero's chests when it closes; opening a chest without a
// key, or buying keys without the Funds, says why instead of sending the form; and the key
// purchase shows what the chosen number of keys costs and leaves.
import { flash } from "./shared.js";

const reveal = document.querySelector("dialog[data-reveal]");
if (reveal) {
  // store.js opens every reveal as a dialog; here the focus goes back to the hero's chests.
  reveal.opener = document.getElementById(reveal.dataset.returns);
  // Without scripts the reveal stands open in the page, and its close links reload the page.
  for (const link of reveal.querySelectorAll("a[data-close]")) {
    link.addEventListener("click", (event) => event.preventDefault());
  }
}

document.addEventListener("submit", (event) => {
  const button = event.submitter;
  const form = event.target;
  if (
    form.matches(".chest-open, [data-keys-form]") &&
    !form.closest("dialog") &&
    button?.getAttribute("aria-disabled") === "true"
  ) {
    event.preventDefault();
    flash(document.getElementById(button.getAttribute("aria-describedby")));
  }
});

for (const form of document.querySelectorAll("[data-keys-form]")) {
  form.addEventListener("change", () => choose(form));
}

/**
 * Shows what the chosen number of keys costs and leaves, and whether the account can pay it.
 *
 * @param {HTMLFormElement} form - The key purchase.
 */
function choose(form) {
  const pack = form.querySelector("input[name=keys]:checked").dataset;
  const short = pack.packWhy !== "";
  form.querySelector("[data-fact=price]").textContent = pack.packPrice;
  const after = form.querySelector("[data-fact=after]");
  after.textContent = pack.packAfter;
  after.classList.toggle("site-short", short);
  form.querySelector("[data-fact=keys]").textContent = pack.packKeys;
  form.querySelector("[data-label]").textContent = pack.packLabel;
  const why = form.querySelector("[data-why]");
  why.hidden = !short;
  why.querySelector("[data-why-text]").textContent = pack.packWhy;
  form.querySelector("[type=submit]").setAttribute("aria-disabled", String(short));
  form.querySelector("[data-earn]").hidden = !short;
  const cancel = form.querySelector("[data-cancel]");
  if (cancel) {
    cancel.hidden = short;
  }
}
