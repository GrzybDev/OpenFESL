// The hot deals page: each card's time left counts down while the page is shown, in red once
// under a day; a card whose time runs out says expired, keeps its end time and the line that it
// leaves at the next page load, and hides its sticker, usual price and way to buy. Without scripts
// the cards show their end times.
import { reducedMotion } from "./naming.js";
import { countDown } from "./store.js";

const DAY = 86_400_000;

document.addEventListener("DOMContentLoaded", () => {
  for (const timer of document.querySelectorAll("[data-deal] [role=timer][data-left]")) {
    countDown(timer, () => expire(timer.closest("[data-deal]")));
    const soon = Number(timer.dataset.left) * 1000 - DAY;
    // The longest wait setTimeout takes; a page shown that long has been reloaded.
    if (soon > 0 && soon < 2 ** 31) {
      setTimeout(() => timer.closest(".deal-timer").classList.add("deal-timer-soon"), soon);
    }
  }
});

/**
 * Shows that a deal's time ran out.
 *
 * @param {Element} card - The deal's card.
 */
function expire(card) {
  card.classList.add("deal-expired");
  for (const live of card.querySelectorAll("[data-live]")) {
    live.hidden = true;
  }
  for (const gone of card.querySelectorAll("[data-gone]")) {
    gone.hidden = false;
    gone.animate([{ opacity: 0 }, { opacity: 1 }], { duration: reducedMotion.matches ? 100 : 160 });
  }
}
