// The store pages' buy panel: choosing another hero or price asks the server for the panel's lower
// part for that choice, which without scripts shows the default hero's and price's.
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
  let asked = 0;

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
    history.replaceState(null, "", `${location.pathname}?${query}`);
    // The tile that was chosen keeps the focus, though the price tiles were drawn again.
    form.querySelector(`[name=${changed.name}][value="${changed.value}"]`)?.focus();
  });
}
