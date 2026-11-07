// The web pages' fonts, stylesheets and htmx, and their motion beyond what the stylesheets do on
// their own. Every page works the same without it.
import { announce } from "./announce.js";
import "@fontsource/barlow/500.css";
import "@fontsource/barlow/600.css";
import "@fontsource/barlow/700.css";
import "@fontsource/barlow-semi-condensed/500.css";
import "@fontsource/barlow-semi-condensed/600.css";
import "@fontsource/barlow-semi-condensed/700.css";
import "@fontsource/barlow-semi-condensed/800.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/shantell-sans/800-italic.css";
import "@fontsource/urbanist/700.css";
import "@fontsource/urbanist/800.css";
import "./base.css";
import "./styles.js";
import "./site.js";
import { closeDialog, fetchPage, openPanel, reducedMotion } from "./shared.js";
import "./creator.js";
import "./rename.js";
import "./play-now.js";
import "./store.js";
import "./chests.js";
import "./claw.js";
import "./catalogue.js";
import "./admin-forms.js";
import "./settings.js";
import "./store-front.js";
import "./hot-deals.js";
import "htmx.org";

// A chevron block whose page takes longer than 300 ms to arrive shows that it is loading, and
// a button that sends a form says what it does meanwhile, such as APPROVING….
document.addEventListener("click", (event) => {
  const block = event.target.closest("a.chevron");
  if (
    block &&
    plainClick(event) &&
    !event.defaultPrevented &&
    !block.target &&
    !block.hasAttribute("data-dialog") &&
    !block.hasAttribute("download")
  ) {
    showBusy(block);
  }
});

document.addEventListener("submit", (event) => {
  if (!event.defaultPrevented && event.submitter?.matches(".chevron")) {
    showBusy(event.submitter);
  }
});

// A purchase's form is sent once: pressing its button again while the answer loads, as a double
// click does, sends nothing more.
document.addEventListener("submit", (event) => {
  const form = event.target;
  if (event.defaultPrevented || !form.matches("[data-once]")) {
    return;
  }
  if (form.dataset.sent) {
    event.preventDefault();
    return;
  }
  form.dataset.sent = "true";
  if (event.submitter) {
    showBusy(event.submitter);
  }
  // A page restored from the back-forward cache may send it again.
  addEventListener("pagehide", () => delete form.dataset.sent, { once: true });
});

/**
 * Tells whether a click is a plain one, not one that opens its link elsewhere.
 *
 * @param {MouseEvent} event - The click.
 * @returns {boolean} Whether it came from the main button without a modifier key.
 */
function plainClick(event) {
  return event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
}

/**
 * Marks a chevron block as loading once its page takes longer than 300 ms to arrive.
 *
 * @param {HTMLElement} block - The link or button that was used.
 */
function showBusy(block) {
  const label = block.querySelector("[data-label]");
  const rest = label?.textContent;
  const timer = setTimeout(() => {
    block.setAttribute("aria-busy", "true");
    if (label && block.dataset.busy) {
      label.textContent = block.dataset.busy;
    }
  }, 300);
  // A page restored from the back-forward cache shows the block as it was.
  addEventListener(
    "pagehide",
    () => {
      clearTimeout(timer);
      block.removeAttribute("aria-busy");
      if (label) {
        label.textContent = rest;
      }
    },
    { once: true },
  );
}

// On phones a form's further options start folded, unless the reader chose one of them.
if (matchMedia("(width < 40rem)").matches) {
  for (const fold of document.querySelectorAll("details[data-fold]:not([data-kept])")) {
    fold.open = false;
  }
}

// A file field hides the browser's own control behind its label, which names the file chosen.
document.addEventListener("change", (event) => {
  const input = event.target;
  const name = input.type === "file" && input.closest("label")?.querySelector("[data-file-name]");
  if (name) {
    name.textContent = input.files[0]?.name ?? name.dataset.none;
  }
});

// A chart wider than the screen starts scrolled to its latest values, and a row of tabs wider than
// the screen to its current tab.
for (const box of document.querySelectorAll("[data-scroll-end]")) {
  box.scrollLeft = box.scrollWidth;
}
for (const row of document.querySelectorAll("[data-scroll-current]")) {
  const current = row.querySelector("[aria-current]");
  if (current) {
    row.scrollLeft += current.getBoundingClientRect().right - row.getBoundingClientRect().right;
  }
}

// Live values: a page's #live region refreshes itself through htmx every 15 s while the tab is
// visible. A changed number counts to its new value, a server's slots fill or empty one by
// one, rows glide to their new places, and a failed refresh keeps the last values,
// muted, until the next one succeeds or the reader asks again.
const REFRESH = 15000;
let refreshedAt = Date.now();
let counted = new Map();
let places = new Map();

document.addEventListener("htmx:before:request", (event) => {
  if (document.hidden) {
    event.preventDefault();
  }
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && Date.now() - refreshedAt >= REFRESH) {
    refresh();
  }
});

document.addEventListener("click", (event) => {
  if (event.target.closest("[data-refresh]")) {
    refresh();
  }
});

/** Refreshes the page's live region now, as its next poll would. */
function refresh() {
  document.getElementById("live")?.dispatchEvent(new Event("refresh"));
}

document.addEventListener("htmx:before:swap", (event) => {
  // A list under the pointer keeps its order until the pointer leaves it.
  const held = document.querySelector("[data-rows]:hover");
  if (held) {
    event.preventDefault();
    held.addEventListener("pointerleave", refresh, { once: true });
    return;
  }
  places = new Map(
    [...document.querySelectorAll("[data-rows] > [data-key]")].map((row) => [
      row.dataset.key,
      row.getBoundingClientRect().top,
    ]),
  );
  counted = new Map(
    [...document.querySelectorAll("[data-count]")].map((element) => [
      element,
      Number(element.dataset.count),
    ]),
  );
  // Each slot waits 20 ms per slot between it and the last taken one, so that the slots the
  // refresh changes fill or empty in turn; the refresh leaves the style attribute alone.
  for (const meter of document.querySelectorAll("svg[data-taken]")) {
    const taken = Number(meter.dataset.taken);
    meter.querySelectorAll(".slot").forEach((slot, index) => {
      slot.style.transitionDelay = `${(index >= taken ? index - taken : taken - 1 - index) * 20}ms`;
    });
  }
});

document.addEventListener("htmx:after:swap", () => {
  refreshedAt = Date.now();
  document.documentElement.classList.add("refreshed");
  glide();
  delete document.documentElement.dataset.stale;
  for (const [element, from] of counted) {
    if (element.isConnected) {
      countTo(element, from);
    }
  }
  tick();
});

for (const failure of ["htmx:error", "htmx:response:error"]) {
  document.addEventListener(failure, (event) => {
    document.documentElement.dataset.stale = "";
    for (const note of document.querySelectorAll("[data-stale-note]")) {
      note.hidden = false;
    }
    // A change the account page's forms sent, which the server did not answer, says so in a toast.
    if (event.target instanceof Element && event.target.closest("[data-account-form]")) {
      const toast = document.getElementById("toast-failed").content.cloneNode(true);
      document.getElementById("toasts").append(toast);
    }
  });
}

// After a change on the previous page, such as a purchase, or in a part of the page that a
// script swapped in, such as an import's blocks: a number that names its value from before the
// change counts to its new one, and the part's toasts leave in time.
countFrom(document);
document.addEventListener("swapped", (event) => {
  countFrom(event.target);
  timeToasts();
});

/**
 * Counts each number in a part of the page from the value it names from before a change.
 *
 * @param {ParentNode} part - The part, or the whole page.
 */
function countFrom(part) {
  for (const element of part.querySelectorAll("[data-count-from]")) {
    countTo(element, Number(element.dataset.countFrom));
  }
}

/**
 * Counts a number that a refresh or a change changed from its old value to its new one.
 *
 * @param {HTMLElement} element - The number, whose data-count holds its new value.
 * @param {number} from - Its value before the refresh or the change.
 */
function countTo(element, from) {
  const to = Number(element.dataset.count);
  if (Number.isNaN(from) || from === to) {
    return;
  }
  element.classList.add("changed");
  setTimeout(() => element.classList.remove("changed"), 400);
  if (reducedMotion.matches) {
    return;
  }
  const final = element.textContent;
  const format = new Intl.NumberFormat(document.documentElement.lang);
  const start = performance.now();
  const step = (time) => {
    const progress = Math.min(1, (time - start) / 400);
    const eased = 1 - (1 - progress) ** 3;
    element.textContent =
      progress < 1 ? format.format(Math.round(from + (to - from) * eased)) : final;
    if (progress < 1) {
      requestAnimationFrame(step);
    }
  };
  requestAnimationFrame(step);
}

/** Moves the rows a refresh reordered from their old places to their new ones. */
function glide() {
  if (reducedMotion.matches || places.size === 0) {
    return;
  }
  for (const row of document.querySelectorAll("[data-rows] > [data-key]")) {
    const top = places.get(row.dataset.key);
    const offset = top === undefined ? -16 : top - row.getBoundingClientRect().top;
    if (offset !== 0) {
      const from =
        top === undefined
          ? { opacity: 0, transform: `translateY(${offset}px)` }
          : { transform: `translateY(${offset}px)` };
      row.animate([from, { opacity: 1, transform: "none" }], {
        duration: top === undefined ? 160 : 200,
        easing: "cubic-bezier(.2, .8, .2, 1)",
      });
    }
  }
}

/** Writes how many seconds ago the live values were refreshed. */
function tick() {
  const seconds = Math.max(0, Math.round((Date.now() - refreshedAt) / 1000));
  for (const note of document.querySelectorAll("[data-ago]")) {
    note.textContent = note.dataset.ago.replace("{seconds}", String(seconds));
  }
}

tick();
setInterval(() => {
  if (!document.hidden) {
    tick();
  }
}, 1000);

// A copy button copies its text, says so for 1.5 s, in the line it names too, and has the page
// announce it. Without clipboard access it stays as it was; the text it copies is on the page.
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy]");
  if (!button) {
    return;
  }
  try {
    await navigator.clipboard.writeText(button.dataset.copy);
  } catch {
    return;
  }
  const label = button.querySelector("[data-label]");
  const line = button.dataset.line ? document.getElementById(button.dataset.line) : null;
  label.dataset.rest ??= label.textContent;
  label.textContent = button.dataset.copied;
  button.classList.add("copied");
  if (line) {
    line.textContent = button.dataset.note;
  }
  announce(button.dataset.note);
  clearTimeout(button.copiedTimer);
  button.copiedTimer = setTimeout(() => {
    label.textContent = label.dataset.rest;
    button.classList.remove("copied");
    if (line) {
      line.textContent = "";
    }
  }, 1500);
});

// A read-only field of a text to copy, such as a personal link, selects all of it on focus.
document.addEventListener("focusin", (event) => {
  if (event.target.matches("[data-select]")) {
    event.target.select();
  }
});

// A toast that says a change was saved leaves after 4 s; one that says it failed stays until
// dismissed.
timeToasts();

/** Has each toast that arrived and says a change was saved leave after 4 s. */
function timeToasts() {
  for (const toast of document.querySelectorAll(".toast[data-leaves]:not([data-timed])")) {
    toast.dataset.timed = "";
    setTimeout(() => leave(toast), 4000);
  }
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-dismiss]");
  if (button) {
    leave(button.closest(".toast"));
  }
});

/**
 * Fades a toast out and removes it.
 *
 * @param {Element} toast - The toast.
 */
function leave(toast) {
  toast.classList.add("leaving");
  setTimeout(() => toast.remove(), reducedMotion.matches ? 100 : 200);
}

// A row's details button opens its details in place; without scripts it is a link that
// opens them with the page.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-details]");
  if (button && plainClick(event)) {
    event.preventDefault();
    toggleDetails(button);
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === " " && event.target.matches("[data-details]")) {
    event.preventDefault();
    toggleDetails(event.target);
  }
});

/**
 * Opens or closes the details of the row a details button belongs to.
 *
 * @param {Element} button - The row's details button.
 */
function toggleDetails(button) {
  const row = button.closest("[data-key]");
  const open = !row.hasAttribute("data-open");
  row.toggleAttribute("data-open", open);
  button.setAttribute("aria-expanded", String(open));
}

// A decision that asks for confirmation opens its confirmation page's panel as a dialog
// over the page it was made on; without scripts, or when the page does not come, the link
// opens the confirmation page itself.
document.addEventListener("click", async (event) => {
  const link = event.target.closest("a[data-dialog]");
  if (!link || !plainClick(event)) {
    return;
  }
  event.preventDefault();
  const timer = setTimeout(() => link.setAttribute("aria-busy", "true"), 300);
  const page = await fetchPage(link.href, { headers: { Accept: "text/html" } }).catch(() => null);
  const panel = page?.querySelector("[data-confirm]");
  clearTimeout(timer);
  link.removeAttribute("aria-busy");
  if (!panel) {
    location.assign(link.href);
    return;
  }
  panel.removeAttribute("aria-labelledby");
  panel.removeAttribute("aria-describedby");
  const dialog = openPanel(document.importNode(panel, true), link);
  // A form's own first field takes the focus; a question starts on its way back.
  if (!dialog.querySelector("[autofocus]")) {
    dialog.querySelector(".confirm-buttons a")?.focus();
  }
});

document.addEventListener("click", (event) => {
  const back = event.target.closest("dialog.dialog :is([data-close], .confirm-buttons a)");
  if (back && !event.metaKey && !event.ctrlKey) {
    event.preventDefault();
    closeDialog(back.closest("dialog"));
  }
});

// A game password field's button shows the password as text and hides it again.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-show-password]");
  if (!button) {
    return;
  }
  const shown = button.getAttribute("aria-pressed") !== "true";
  button.setAttribute("aria-pressed", String(shown));
  button.parentElement.querySelector("input").type = shown ? "text" : "password";
});

// The account page's forms swap only their part: it cross-fades in with the toast of the change,
// and Cloudflare's check in it is drawn anew.
document.addEventListener("htmx:after:swap", (event) => {
  const part = event.target instanceof Element ? event.target.closest("[data-account-part]") : null;
  if (!part) {
    return;
  }
  timeToasts();
  part.animate([{ opacity: 0.4 }, { opacity: 1 }], {
    duration: reducedMotion.matches ? 100 : 120,
    easing: "linear",
  });
  for (const check of part.querySelectorAll(".cf-turnstile:empty")) {
    window.turnstile?.render(check, {
      sitekey: check.dataset.sitekey,
      language: check.dataset.language,
      theme: "light",
    });
  }
  followParts?.();
});

// The account page's index marks the part being read, its block sliding to the part's link.
const followParts = accountIndex(document.querySelector("[data-account-index]"));

/**
 * Marks in the account page's index the part being read as the page scrolls: the last part whose
 * top passed the upper third of the window, the first before any did, and the last one once the
 * page is scrolled to its end.
 *
 * @param {Element | null} nav - The index, if the page has one.
 * @returns {(() => void) | undefined} Marks the part again, as after one was swapped.
 */
function accountIndex(nav) {
  if (!nav) {
    return undefined;
  }
  const block = nav.querySelector(".account-index-block");
  let pending = false;
  const mark = () => {
    pending = false;
    const parts = [...document.querySelectorAll("[data-account-part]")];
    const ended = innerHeight + scrollY >= document.documentElement.scrollHeight - 2;
    const passed = parts.filter((part) => part.getBoundingClientRect().top <= innerHeight / 3);
    const part = ended ? parts.at(-1) : (passed.at(-1) ?? parts[0]);
    const current = part && nav.querySelector(`a[href="#${part.id}"]`);
    for (const link of nav.querySelectorAll("a")) {
      if (link === current) {
        link.setAttribute("aria-current", "true");
      } else {
        link.removeAttribute("aria-current");
      }
    }
    if (current) {
      block.style.width = `${current.offsetWidth}px`;
      block.style.height = `${current.offsetHeight}px`;
      block.style.transform = `translate(${current.offsetLeft}px, ${current.offsetTop}px)`;
    }
  };
  const later = () => {
    if (!pending) {
      pending = true;
      requestAnimationFrame(mark);
    }
  };
  addEventListener("scroll", later, { passive: true });
  addEventListener("resize", later);
  mark();
  return later;
}
