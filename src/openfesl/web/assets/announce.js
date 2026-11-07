// The shell's live region, through which screen readers hear what a script just changed.

/**
 * Has screen readers read a short message about what just happened.
 *
 * @param {string} message - The message.
 */
export function announce(message) {
  const region = document.getElementById("announce");
  region.textContent = "";
  requestAnimationFrame(() => {
    region.textContent = message;
  });
}
