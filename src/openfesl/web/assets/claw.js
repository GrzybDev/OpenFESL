// The Daily Claw: its overlay, which opens by itself on the first pages of the day while a free
// grab waits and from the menu, loading the Claw's page into it; the hero picker, which fills
// the machine's belts with the hero's prizes; the paid grab's confirmation; and the grab, sent at
// once, whose prize the server draws, while the machine plays taking it. Skip and Speed Claw go
// straight to the prize, and reduced motion starts with Speed Claw. Without scripts the menu
// leads to the Claw's page, whose form grabs and shows the prize on the next page.
import { run } from "./claw-machine.js";
import { closeDialog, flash, reducedMotion } from "./naming.js";
import { countDown } from "./store.js";

const SPEED = "claw-speed";

const overlay = document.querySelector("dialog[data-claw-overlay]");
if (overlay) {
  if (overlay.hasAttribute("data-opens-now")) {
    open();
  }
  overlay.addEventListener("cancel", (event) => {
    event.preventDefault();
    close();
  });
  overlay.querySelector("[data-claw-close]").addEventListener("submit", (event) => {
    event.preventDefault();
    close();
  });
}

document.addEventListener("click", (event) => {
  const opener = event.target.closest("[data-claw-open]");
  if (opener && overlay) {
    event.preventDefault();
    const menu = opener.closest("details");
    menu?.removeAttribute("open");
    // The entry hides with its menu, whose tab takes the focus back instead.
    open(menu?.querySelector("summary") ?? opener);
  }
});

for (const body of document.querySelectorAll(".site-main [data-claw-body]")) {
  enhance(body);
}

/**
 * Opens the overlay and loads the Claw into it.
 *
 * @param {HTMLElement} [opener] - What opened it, which takes the focus back.
 */
async function open(opener) {
  overlay.opener = opener ?? document.activeElement;
  if (!overlay.open) {
    overlay.showModal();
  }
  await load();
}

/** Loads the Claw's page into the overlay, with a message it brings. */
async function load() {
  const slot = overlay.querySelector("[data-claw-slot]");
  try {
    const page = await fetchPage(overlay.dataset.src);
    const body = page.querySelector("[data-claw-body]");
    slot.replaceChildren(body);
    showMessage(page.querySelector("[data-site-message]"), body);
    enhance(body);
    focusNext(body, overlay.querySelector(".claw-close"));
  } catch {
    showMessage(failure("connection"), slot);
  }
}

/** Closes the overlay, keeps it closed for the day and says so on the page. */
function close() {
  const form = overlay.querySelector("[data-claw-close]");
  fetch(form.action, { method: "POST", body: new FormData(form), keepalive: true });
  closeDialog(overlay, overlay.opener);
  const main = document.querySelector(".site-main");
  main.querySelector("[data-site-message]")?.remove();
  const said = overlay.querySelector("template[data-claw-closed]").content.cloneNode(true);
  main.querySelector(".site-texture").after(said);
}

/**
 * Makes a Claw work: the picker, Speed Claw, the paid grab's confirmation, the countdown and the
 * grab itself.
 *
 * @param {HTMLElement} body - The Claw's [data-claw-body].
 * @param {string} [last] - The hero that the last grab was for, which stays picked.
 */
function enhance(body, last) {
  const form = body.querySelector("[data-claw-form]");
  const prize = body.querySelector("dialog[data-claw-prize]");
  if (prize) {
    reveal(prize);
  }
  if (!form) {
    return;
  }
  const heroes = [...form.querySelectorAll("[name=hero]")];
  // The picker comes first where there is a choice to make.
  if (heroes.length > 1) {
    for (const hero of heroes) {
      hero.checked = hero.value === last;
    }
  }
  // A click also comes with Space and the arrow keys, and on the hero already picked.
  form.addEventListener("click", (event) => {
    if (event.target.name === "hero") {
      pick(form, event.target);
    }
  });
  const chosen = heroes.find((hero) => hero.checked);
  if (chosen) {
    pick(form, chosen);
  } else {
    available(form, false);
  }
  form.querySelector("[data-claw-change]").addEventListener("click", () => {
    delete form.dataset.hero;
    const hero = form.querySelector(".store-hero input:checked");
    // Unchecked, the hero takes Space as a pick again, which browsers ignore on a checked radio.
    hero.checked = false;
    hero.focus();
  });

  const speed = form.querySelector("[data-claw-speed]");
  let kept = null;
  try {
    kept = localStorage.getItem(SPEED);
  } catch {
    // Storage may be blocked; the default stands.
  }
  speed.checked = kept === null ? reducedMotion.matches : kept === "1";
  form.querySelector("[data-speed-reduced]").hidden = !(kept === null && reducedMotion.matches);
  speed.addEventListener("change", () => {
    try {
      localStorage.setItem(SPEED, speed.checked ? "1" : "0");
    } catch {
      // The choice lasts for this page.
    }
  });

  const confirm = form.querySelector("[data-claw-confirm]");
  form.querySelector("[data-claw-cancel]")?.addEventListener("click", () => {
    confirm.hidden = true;
    form.querySelector("[data-claw-paid]").focus();
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const button = event.submitter;
    if (!form.dataset.hero) {
      form.querySelector(".store-hero input")?.focus();
      return;
    }
    if (button?.getAttribute("aria-disabled") === "true") {
      flash(form.querySelector("[data-why]") ?? button);
      return;
    }
    // A paid grab asks first, in place; its confirmation sends it.
    if (button?.matches("[data-claw-paid]") && confirm) {
      confirm.hidden = false;
      confirm.querySelector("[type=submit]").focus();
      return;
    }
    grab(body, form, button, speed.checked);
  });

  // The next free grab loads the Claw again.
  const countdown = body.querySelector("[data-left]");
  if (countdown) {
    countDown(countdown, () => (overlay?.contains(body) ? load() : location.reload()));
  }
}

/**
 * Picks the hero the prize goes to: the picker folds into one row and the belts fill.
 *
 * @param {HTMLFormElement} form - The grab form.
 * @param {HTMLInputElement} input - The hero's radio button.
 */
function pick(form, input) {
  const card = input.closest(".store-hero");
  form.dataset.hero = input.value;
  const row = form.querySelector("[data-claw-chosen]");
  row.hidden = false;
  row
    .querySelector("[data-chosen-disc]")
    .replaceChildren(card.querySelector(".store-hero-disc svg").cloneNode(true));
  const name = card.querySelector(".store-hero-name").textContent;
  for (const named of form.querySelectorAll("[data-chosen-name]")) {
    named.textContent = name;
  }
  row.querySelector("[data-chosen-what]").textContent = form.dataset.chosenText.replace(
    "%(what)s",
    card.querySelector(".store-hero-kind").textContent.trim(),
  );
  for (const belts of form.querySelectorAll("[data-belts]")) {
    belts.hidden = belts.dataset.belts !== input.value;
  }
  const main = form.closest(".claw-main");
  for (const odds of main.querySelectorAll("[data-chances]")) {
    odds.hidden = odds.dataset.chances !== input.value;
  }
  main.querySelector("[data-chances-label]").textContent = form.dataset.chancesText.replace(
    "%(hero)s",
    name,
  );
  available(form, true);
  // The picker folds away, so a choice made in it moves on to the grab.
  if (form.querySelector("[data-claw-picker]").contains(document.activeElement)) {
    focusNext(form);
  }
}

/**
 * Puts the focus where the Claw goes on: the first hero while none is picked, else the grab,
 * else the first control shown.
 *
 * @param {HTMLElement} place - The Claw or its form.
 * @param {HTMLElement} [fallback] - What takes the focus when the Claw shows no control.
 */
function focusNext(place, fallback) {
  const form = place.querySelector("[data-claw-form]") ?? place;
  const first = form.dataset.hero ? null : form.querySelector(".store-hero input:not(:disabled)");
  const grabs = place.querySelector("[data-claw-grab], [data-claw-paid]");
  const shown = [...place.querySelectorAll("a[href], button")].find((each) =>
    each.checkVisibility(),
  );
  (first ?? grabs ?? shown ?? fallback)?.focus();
}

/**
 * Makes the grab buttons available once a hero is picked, keeping the unaffordable one off.
 *
 * @param {HTMLFormElement} form - The grab form.
 * @param {boolean} picked - Whether a hero is picked.
 */
function available(form, picked) {
  for (const button of form.querySelectorAll("[data-claw-grab], [data-claw-paid]")) {
    if (!button.hasAttribute("aria-describedby")) {
      button.setAttribute("aria-disabled", String(!picked));
    }
  }
}

/**
 * Sends a grab and plays the machine while the server's answer comes, then shows the prize.
 *
 * @param {HTMLElement} body - The Claw.
 * @param {HTMLFormElement} form - The grab form.
 * @param {HTMLElement} button - The button that sent it.
 * @param {boolean} quick - Whether to show the prize at once.
 */
async function grab(body, form, button, quick) {
  if (form.dataset.sent) {
    return;
  }
  form.dataset.sent = "true";
  const data = new FormData(form, button);
  const machine = quick ? null : run(form);
  let page;
  try {
    const [answer] = await Promise.all([fetchPage(form.action, data), machine?.ran]);
    page = answer;
  } catch (error) {
    machine?.stop();
    delete form.dataset.sent;
    showMessage(failure(error.message === "failed" ? "server" : "connection"), body);
    return;
  }
  const next = page.querySelector("[data-claw-body]");
  const prize = next?.querySelector("dialog[data-claw-prize]");
  if (!prize) {
    machine?.stop();
    swap(body, page);
    return;
  }
  if (machine) {
    await machine.take(prize.dataset.item);
  }
  // Closed meanwhile: the grab counts, and the page shows what it changed without the card.
  if (overlay?.contains(body) && !overlay.open) {
    swap(body, page);
    return;
  }
  prize.remove();
  body.append(prize);
  reveal(prize, () => swap(body, page));
}

/**
 * Shows a prize card as a dialog over the Claw; OK and Escape close it.
 *
 * @param {HTMLDialogElement} prize - The card, which stands open in the page without scripts.
 * @param {function(): void} [after] - Called once it is closed.
 */
function reveal(prize, after) {
  prize.close();
  prize.showModal();
  const ok = prize.querySelector("[data-claw-ok]");
  ok.focus();
  ok.addEventListener("click", () => prize.close());
  prize.addEventListener("close", () => {
    // The close before showModal() fires too, while the card shows.
    if (!prize.open) {
      prize.remove();
      after?.();
    }
  });
}

/**
 * Puts the Claw of a page that answered a grab in place of the one shown, with the page's
 * message, wallets and menu counts.
 *
 * @param {HTMLElement} body - The Claw shown.
 * @param {Document} page - The page that answered.
 */
function swap(body, page) {
  const next = page.querySelector("[data-claw-body]");
  next.querySelector("dialog[data-claw-prize]")?.remove();
  const last = body.querySelector("[data-claw-form]")?.dataset.hero;
  body.replaceWith(next);
  showMessage(page.querySelector("[data-site-message]"), next);
  for (const amount of page.querySelectorAll(".site-wallets [data-wallet]")) {
    const shown = amount.querySelector("[data-count]").textContent;
    const code = amount.dataset.wallet;
    for (const place of document.querySelectorAll(
      `.site-wallets [data-wallet="${code}"] [data-count], .claw-wallet[data-wallet="${code}"] .claw-wallet-amount`,
    )) {
      place.textContent = shown;
    }
  }
  // Only the counts: the menus mark this page as the current one, the answer the Claw's.
  const tabs = ".site-nav :is(.site-tab, .site-entry)";
  const counted = [...page.querySelectorAll(tabs)];
  document.querySelectorAll(tabs).forEach((tab, index) => {
    tab.querySelector(".site-count")?.remove();
    const count = counted[index]?.querySelector(".site-count");
    if (count) {
      // Before a menu's turn arrow, after an entry's name.
      tab.insertBefore(count, tab.querySelector(":scope > .site-turn"));
    }
  });
  enhance(next, last);
  focusNext(next);
}

/**
 * Shows a message above the Claw, in the overlay's place for it or on the page.
 *
 * @param {Element|null} message - The message; nothing changes without one.
 * @param {HTMLElement} body - The Claw it is about.
 */
function showMessage(message, body) {
  if (!message) {
    return;
  }
  const place = body.closest("dialog[data-claw-overlay]")?.querySelector("[data-claw-message]");
  if (place) {
    place.replaceChildren(message);
  } else {
    body.parentElement.querySelector("[data-site-message]")?.remove();
    body.before(message);
  }
}

/**
 * Returns the message that says why a grab's answer did not come.
 *
 * @param {string} why - server or connection.
 * @returns {Element} The message.
 */
function failure(why) {
  const template = document.querySelector(`template[data-claw-failed="${why}"]`);
  return template.content.firstElementChild.cloneNode(true);
}

/**
 * Fetches a page of the site, following its redirect, as a document.
 *
 * @param {string} address - The page's address.
 * @param {FormData} [data] - A form to post to it.
 * @returns {Promise<Document>} The page.
 */
async function fetchPage(address, data) {
  const answer = await fetch(address, data ? { method: "POST", body: data } : {});
  if (!answer.ok) {
    throw new Error("failed");
  }
  return new DOMParser().parseFromString(await answer.text(), "text/html");
}
