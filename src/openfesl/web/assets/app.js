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

// Live values: a page's #live region refreshes itself through htmx every 15 s while the tab is
// visible. A changed number counts to its new value (M5), and a failed refresh keeps the last
// values, muted, until the next one succeeds.
const REFRESH = 15000;
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
let refreshedAt = Date.now();
let counted = new Map();

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

/** Refreshes the page's live region now, as its next poll would. */
function refresh() {
  document.getElementById("live")?.dispatchEvent(new Event("refresh"));
}

document.addEventListener("htmx:before:swap", () => {
  counted = new Map(
    [...document.querySelectorAll("[data-count]")].map((element) => [
      element,
      Number(element.dataset.count),
    ]),
  );
});

document.addEventListener("htmx:after:swap", () => {
  refreshedAt = Date.now();
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
