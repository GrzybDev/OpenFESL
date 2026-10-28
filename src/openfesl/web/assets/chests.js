// The treasure chests page: the reveal of the chest just opened comes up as a dialog over the
// page and gives the focus back to the hero's chests when it closes, and opening a chest without
// a key says why instead of sending the form.
import { flash } from "./naming.js";

const reveal = document.querySelector("dialog[data-reveal]");
if (reveal) {
  reveal.opener = document.getElementById(reveal.dataset.returns);
  // Without scripts the reveal stands open in the page, and its close links reload the page.
  reveal.close();
  reveal.showModal();
  for (const link of reveal.querySelectorAll("a[data-close]")) {
    link.addEventListener("click", (event) => event.preventDefault());
  }
}

document.addEventListener("submit", (event) => {
  const button = event.submitter;
  if (event.target.matches(".chest-open") && button?.getAttribute("aria-disabled") === "true") {
    event.preventDefault();
    flash(document.getElementById(button.getAttribute("aria-describedby")));
  }
});
