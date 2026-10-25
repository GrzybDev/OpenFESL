// What the page sets before it is first drawn: which way the page transition that brings the page
// moves.

// M12: the page that arrives tells its transition which way to move: back through the history
// slides the other way, and another game's pages or another language cross-fade as a whole.
addEventListener("pagereveal", (event) => {
  const activation = window.navigation?.activation;
  if (!event.viewTransition || !activation?.from) {
    return;
  }
  document.documentElement.classList.add("arrived");
  const from = new URL(activation.from.url);
  if (area(from) !== area(location) || translated(from)) {
    event.viewTransition.types.add("swap");
  } else if (
    activation.navigationType === "traverse" &&
    activation.from.index > activation.entry.index
  ) {
    event.viewTransition.types.add("back");
  }
});

/**
 * Returns the address of the shell's game link that an address belongs to.
 *
 * @param {URL | Location} address - The address of a page.
 * @returns {string} The game link's path, or an empty string for pages of no game.
 */
function area(address) {
  for (const link of document.querySelectorAll("[data-games] a")) {
    if (address.pathname === link.pathname || address.pathname.startsWith(link.pathname + "/")) {
      return link.pathname;
    }
  }
  return "";
}

/**
 * Tells whether an address is this page in another language.
 *
 * @param {URL} address - The address of a page.
 * @returns {boolean} Whether the page names the address as its version in another language.
 */
function translated(address) {
  const lang = document.documentElement.lang;
  const versions = document.querySelectorAll("link[rel=alternate]");
  return [...versions].some((link) => link.href === address.href && link.hreflang !== lang);
}
