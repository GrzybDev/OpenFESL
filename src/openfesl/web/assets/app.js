// The web pages' fonts, stylesheets and htmx, and their motion beyond what the stylesheets do on
// their own. Every page works the same without it.
import "@fontsource/barlow/500.css";
import "@fontsource/barlow/600.css";
import "@fontsource/barlow/700.css";
import "@fontsource/barlow-semi-condensed/500.css";
import "@fontsource/barlow-semi-condensed/600.css";
import "@fontsource/barlow-semi-condensed/700.css";
import "@fontsource/barlow-semi-condensed/800.css";
import "@fontsource/ibm-plex-mono/500.css";
import "./base.css";
import "./styles.js";
import "htmx.org";

// M8: a chevron block whose page takes longer than 300 ms to arrive shows that it is loading.
document.addEventListener("click", (event) => {
  const block = event.target instanceof Element ? event.target.closest("a.chevron") : null;
  const plain =
    event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
  if (!block || !plain || event.defaultPrevented || block.target) {
    return;
  }
  const timer = setTimeout(() => block.setAttribute("aria-busy", "true"), 300);
  addEventListener(
    "pagehide",
    () => {
      clearTimeout(timer);
      block.removeAttribute("aria-busy");
    },
    { once: true },
  );
});

// On phones a form's further options start folded, unless the reader chose one of them.
document.addEventListener("DOMContentLoaded", () => {
  if (matchMedia("(width < 40rem)").matches) {
    for (const fold of document.querySelectorAll("details[data-fold]:not([data-kept])")) {
      fold.open = false;
    }
  }
});

// A chart wider than the screen starts scrolled to its latest values.
document.addEventListener("DOMContentLoaded", () => {
  for (const box of document.querySelectorAll("[data-scroll-end]")) {
    box.scrollLeft = box.scrollWidth;
  }
});

// Live values: a page's #live region refreshes itself through htmx every 15 s while the tab is
// visible. A changed number counts to its new value (M5), a server's slots fill or empty one by
// one (M6), rows glide to their new places (M9), and a failed refresh keeps the last values,
// muted, until the next one succeeds or the reader asks again.
const REFRESH = 15000;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
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
  if (event.target instanceof Element && event.target.closest("[data-refresh]")) {
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
  document.addEventListener(failure, () => {
    document.documentElement.dataset.stale = "";
    for (const note of document.querySelectorAll("[data-stale-note]")) {
      note.hidden = false;
    }
  });
}

/**
 * Counts a number that a refresh changed from its old value to its new one.
 *
 * @param {HTMLElement} element - The number, whose data-count holds its new value.
 * @param {number} from - Its value before the refresh.
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

/** Moves the rows a refresh reordered from their old places to their new ones (M9). */
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

document.addEventListener("DOMContentLoaded", tick);
setInterval(() => {
  if (!document.hidden) {
    tick();
  }
}, 1000);

// M8: a copy button copies its text, says so for 1.5 s and has the page announce it. Without
// clipboard access it stays as it was; the text it copies is on the page.
document.addEventListener("click", async (event) => {
  const button = event.target instanceof Element ? event.target.closest("[data-copy]") : null;
  if (!button) {
    return;
  }
  try {
    await navigator.clipboard.writeText(button.dataset.copy);
  } catch {
    return;
  }
  const label = button.querySelector("[data-label]");
  label.dataset.rest ??= label.textContent;
  label.textContent = button.dataset.copied;
  button.classList.add("copied");
  announce(button.dataset.note);
  clearTimeout(button.copiedTimer);
  button.copiedTimer = setTimeout(() => {
    label.textContent = label.dataset.rest;
    button.classList.remove("copied");
  }, 1500);
});

/**
 * Has screen readers read a short message about what just happened.
 *
 * @param {string} message - The message.
 */
function announce(message) {
  const region = document.getElementById("announce");
  if (region) {
    region.textContent = "";
    requestAnimationFrame(() => {
      region.textContent = message;
    });
  }
}

// M4: a row's details button opens its details in place; without scripts it is a link that
// opens them with the page.
document.addEventListener("click", (event) => {
  const button = event.target instanceof Element ? event.target.closest("[data-details]") : null;
  if (
    button &&
    event.button === 0 &&
    !event.metaKey &&
    !event.ctrlKey &&
    !event.shiftKey &&
    !event.altKey
  ) {
    event.preventDefault();
    toggleDetails(button);
  }
});

document.addEventListener("keydown", (event) => {
  if (
    event.key === " " &&
    event.target instanceof Element &&
    event.target.matches("[data-details]")
  ) {
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
