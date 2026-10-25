// The web pages' fonts and stylesheets, and their motion beyond what the stylesheets do on their
// own. Every page works the same without it.
import "@fontsource/barlow/500.css";
import "@fontsource/barlow/600.css";
import "@fontsource/barlow/700.css";
import "@fontsource/barlow-semi-condensed/500.css";
import "@fontsource/barlow-semi-condensed/600.css";
import "@fontsource/barlow-semi-condensed/700.css";
import "@fontsource/barlow-semi-condensed/800.css";
import "./base.css";
import "./styles.js";

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
