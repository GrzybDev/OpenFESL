// The Play Now page: it opens the start link it made, which the browser hands to OpenFESLHook's
// launcher, then asks the server every 2 s, while the tab is visible, where the link stands, and
// cross-fades to the panel of each new state until the launcher takes the link or it ends.
import { fetchPage, reducedMotion } from "./shared.js";

const POLL = 2000;
const SETTLED = ["taken", "ended"];

// The link opens once the page has loaded, so that leaving for the launcher stops none of its files.
addEventListener("load", () => {
  const section = document.querySelector("[data-start-link]");
  if (section) {
    location.assign(section.dataset.startLink);
    follow(section);
  }
});

/**
 * Follows the state of the page's start link, swapping in the panel of each new one.
 *
 * @param {HTMLElement} section - The live region that holds the panel, with the link's addresses.
 */
function follow(section) {
  let timer;
  let asking = false;
  schedule();
  document.addEventListener("visibilitychange", schedule);

  /** Asks again in 2 s while the tab is visible and the link has not settled. */
  function schedule() {
    clearTimeout(timer);
    if (!asking && !document.hidden && !settled()) {
      timer = setTimeout(ask, POLL);
    }
  }

  /** Asks where the link stands and shows it; a failed request is asked again next time. */
  async function ask() {
    if (document.hidden) {
      return;
    }
    asking = true;
    const page = await fetchPage(section.dataset.status, { cache: "no-store" }).catch(() => null);
    asking = false;
    show(page?.querySelector("[data-state]"));
    schedule();
  }

  /**
   * Swaps in a panel whose state differs from the one shown, keeping the focus in the panel.
   *
   * @param {HTMLElement | null} next - The panel the server sent.
   */
  function show(next) {
    const panel = section.querySelector("[data-state]");
    if (!next || next.dataset.state === panel.dataset.state) {
      return;
    }
    const focused = panel.contains(document.activeElement);
    const arrived = document.importNode(next, true);
    panel.replaceWith(arrived);
    arrived.animate([{ opacity: 0 }, { opacity: 1 }], {
      duration: reducedMotion.matches ? 100 : 160,
      easing: "cubic-bezier(.2,.8,.2,1)",
    });
    if (focused) {
      arrived.querySelector("h2").focus();
    }
  }

  /** Returns whether the link was taken or ended, so that nothing more changes. */
  function settled() {
    return SETTLED.includes(section.querySelector("[data-state]").dataset.state);
  }
}
