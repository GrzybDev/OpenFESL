// The in-game store's motion and its price points. The game's browser is WebKit 533 of 2010, so
// this keeps to what that runs: no modules, arrow functions, let, const, classList or dataset.
// Every page works the same without this script, when the price points are links.
(function () {
  var panel = document.getElementById("panel");

  function reducedMotion() {
    return !!(window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  function hasClass(element, name) {
    return (" " + element.className + " ").indexOf(" " + name + " ") >= 0;
  }

  function setClass(element, name, on) {
    var rest = (" " + element.className + " ").replace(" " + name + " ", " ");
    element.className = (on ? rest + name : rest).replace(/^\s+|\s+$/g, "");
  }

  function byClass(name) {
    var found = [];
    var all = document.getElementsByTagName("*");
    for (var i = 0; i < all.length; i++) {
      if (hasClass(all[i], name)) {
        found.push(all[i]);
      }
    }
    return found;
  }

  function on(element, type, handler) {
    element.addEventListener(type, handler, false);
  }

  // How deep a store address lies: the categories, a category, an item, need more.
  function depth(address) {
    var path = address.split("?")[0].split("#")[0];
    var at = path.indexOf("/game/store");
    return at < 0 ? -1 : path.slice(at).split("/").length;
  }

  // The panel slides in from the side its address lies on, against the page it came from.
  function slideIn() {
    var from = document.referrer ? depth(document.referrer) : -1;
    var here = depth(location.href);
    if (!panel || from < 0 || from === here) {
      return;
    }
    var side = here > from ? "from-right" : "from-left";
    setClass(panel, side, true);
    // The next frame takes the panel from there, as a transition needs a drawn start.
    setTimeout(function () {
      setClass(panel, side, false);
    }, 20);
  }

  // A price point picked shows what buying it does, without a new page.
  function pick(tile) {
    var tiles = byClass("tile");
    for (var i = 0; i < tiles.length; i++) {
      var picked = tiles[i] === tile;
      tiles[i].setAttribute("aria-pressed", picked ? "true" : "false");
      var details = document.getElementById(tiles[i].getAttribute("data-pick"));
      if (details) {
        setClass(details, "pick-off", !picked);
      }
    }
  }

  on(document, "click", function (event) {
    var target = event.target;
    while (target && target.nodeType === 1) {
      if (hasClass(target, "tile")) {
        event.preventDefault();
        pick(target);
        return;
      }
      target = target.parentNode;
    }
  });

  if (!reducedMotion()) {
    slideIn();
  }
})();
