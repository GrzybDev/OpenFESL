// The in-game store's motion and its calls to the game, which binds its bridge as window.game.
// The game's browser is WebKit 533 of 2010, so this keeps to what that runs: no modules, arrow
// functions, let, const, classList or dataset. Version 1.46's bridge lacks equip and keepOpen and
// its notifyPurchase takes the tag alone. Every page works the same without this script, when the
// price points are links, a form is sent as it is and nothing tells the game.
(function () {
  var game = window.game;
  // Whether the bridge can equip a bought item, which only 1.89's can.
  var equips = !!(game && game.equip);
  var panel = document.getElementById("panel");
  var busy = false;

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

  function startBuying(form) {
    busy = true;
    setClass(panel, "busy", true);
    var tiles = byClass("tile");
    for (var i = 0; i < tiles.length; i++) {
      tiles[i].setAttribute("aria-disabled", "true");
    }
    // The game keeps the store open while the purchase is under way.
    if (game && game.keepOpen) {
      game.keepOpen();
    }
    form.setAttribute("aria-busy", "true");
  }

  // The game refreshes the item it was told of, equipping it when asked.
  function tellGame(bought) {
    var tag = bought.getAttribute("data-tag");
    if (!game || !game.notifyPurchase || !tag) {
      return;
    }
    if (equips) {
      game.notifyPurchase(tag, bought.getAttribute("data-equip") === "1");
    } else {
      game.notifyPurchase(tag);
    }
  }

  on(document, "click", function (event) {
    var target = event.target;
    while (target && target.nodeType === 1) {
      if (hasClass(target, "tile")) {
        event.preventDefault();
        if (!busy) {
          pick(target);
        }
        return;
      }
      if (hasClass(target, "button") && target.getAttribute("aria-disabled") === "true") {
        event.preventDefault();
        return;
      }
      if (hasClass(target, "dismiss")) {
        event.preventDefault();
        var message = document.getElementById("message");
        if (message) {
          message.parentNode.removeChild(message);
        }
        return;
      }
      target = target.parentNode;
    }
  });

  on(document, "submit", function (event) {
    var form = event.target;
    if (!hasClass(form, "buy")) {
      return;
    }
    if (busy) {
      event.preventDefault();
      return;
    }
    startBuying(form);
  });

  // A page the browser keeps for its back button comes back as it was before buying.
  on(window, "pageshow", function (event) {
    if (event.persisted && panel) {
      busy = false;
      setClass(panel, "busy", false);
    }
  });

  // Without a bridge that equips, Buy is the one action, drawn as such.
  if (!equips && game) {
    var equipping = byClass("equip");
    for (var i = 0; i < equipping.length; i++) {
      equipping[i].parentNode.removeChild(equipping[i]);
    }
    var paired = byClass("pair");
    for (var j = 0; j < paired.length; j++) {
      setClass(paired[j], "button-dark", false);
      setClass(paired[j], "button-go", true);
    }
  }

  var bought = document.getElementById("bought");
  if (bought) {
    tellGame(bought);
  }
  if (!reducedMotion()) {
    slideIn();
  }
})();
