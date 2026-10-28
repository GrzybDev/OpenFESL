// The admin panel's import of a catalogue: the chosen file shows with its size, a file too large
// is refused before anything is sent, a replacement is confirmed in a dialog, and the file
// goes up in the background with its progress until the server has read it. An import puts the
// page's new blocks in place, whose numbers count to their new values; a refusal or a stopped
// upload says so above the steps, and the file stays chosen. Without scripts the form is sent as
// it is.

document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector("[data-catalogue-upload]");
  if (form) {
    // The dialog asks instead of the form's required box, and the script says what is missing.
    form.noValidate = true;
  }
});

document.addEventListener("change", (event) => {
  const input = event.target;
  const form = input instanceof HTMLInputElement ? input.closest("[data-catalogue-upload]") : null;
  if (!form || input.type !== "file") {
    return;
  }
  const name = form.querySelector("[data-catalogue-file-name]");
  const file = input.files[0];
  name.textContent = file ? chosen(form, file) : name.dataset.none;
  if (file) {
    form.querySelector("[data-catalogue-needed]").hidden = true;
    input.removeAttribute("aria-invalid");
  }
});

document.addEventListener("submit", (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !form.matches("[data-catalogue-upload]")) {
    return;
  }
  event.preventDefault();
  const input = form.querySelector("input[type=file]");
  const file = input.files[0];
  if (!file) {
    form.querySelector("[data-catalogue-needed]").hidden = false;
    input.setAttribute("aria-invalid", "true");
    input.focus();
    return;
  }
  if (file.size > Number(form.dataset.most)) {
    say("[data-catalogue-refused-size]", { file: file.name, size: megabytes(form, file.size) });
    return;
  }
  if ("replaces" in form.dataset) {
    confirmReplace(form, file);
  } else {
    send(form, file);
  }
});

/**
 * Asks in a dialog before a file replaces a catalogue, and sends it once the admin confirms.
 *
 * @param {HTMLFormElement} form - The import's form.
 * @param {File} file - The chosen file.
 */
function confirmReplace(form, file) {
  const panel = document.querySelector("[data-catalogue-confirm]").content.cloneNode(true);
  const dialog = document.createElement("dialog");
  dialog.className = "dialog";
  dialog.setAttribute("aria-labelledby", "confirm-question");
  dialog.setAttribute("aria-describedby", "confirm-text");
  dialog.append(panel);
  dialog.querySelector("[data-chosen]").textContent = chosen(form, file);
  // The dialog takes the colours of the area the form is in.
  (form.closest(".area") ?? document.body).append(dialog);
  dialog.addEventListener("close", () => {
    dialog.remove();
    form.querySelector("[type=submit]").focus();
  });
  // Escape plays the closing as the cancel button does.
  dialog.addEventListener("cancel", (cancelled) => {
    cancelled.preventDefault();
    dialog.querySelector(".confirm-buttons [data-close]").click();
  });
  dialog.querySelector("[data-catalogue-replace]").addEventListener("click", () => {
    dialog.close();
    send(form, file);
  });
  dialog.showModal();
  dialog.querySelector(".confirm-buttons [data-close]").focus();
}

/**
 * Sends the file in the background, showing its progress and then the import, and puts what came
 * of it on the page.
 *
 * @param {HTMLFormElement} form - The import's form.
 * @param {File} file - The chosen file.
 */
function send(form, file) {
  const input = form.querySelector("input[type=file]");
  const progress = form.querySelector("[data-catalogue-progress]");
  const importing = form.querySelector("[data-catalogue-importing]");
  const sending = form.querySelector("[data-catalogue-send]");
  const bar = progress.querySelector("progress");
  const amount = progress.querySelector("[data-amount]");
  const stop = progress.querySelector("[data-catalogue-stop]");
  const data = new FormData(form);
  data.set("confirmed", "1");
  const request = new XMLHttpRequest();
  let sent = 0;

  const show = (loaded, total) => {
    sent = total ? (file.size * loaded) / total : 0;
    bar.value = total ? loaded / total : 0;
    const percent = new Intl.NumberFormat(document.documentElement.lang, { style: "percent" });
    amount.textContent = `${amount.dataset.amount
      .replace("{sent}", number(sent))
      .replace("{size}", megabytes(form, file.size))} · ${percent.format(bar.value)}`;
  };
  const settle = () => {
    // The focus that the stop holds, or that hidden controls dropped, comes back to the import.
    const dropped =
      document.activeElement === document.body || progress.contains(document.activeElement);
    input.disabled = false;
    sending.hidden = false;
    progress.hidden = true;
    importing.hidden = true;
    if (dropped) {
      form.querySelector("[type=submit]").focus();
    }
  };
  const stopped = (template) => {
    settle();
    say(template, {
      file: file.name,
      sent: number(sent),
      size: megabytes(form, file.size),
    });
  };

  say(null);
  input.disabled = true;
  sending.hidden = true;
  progress.hidden = false;
  // The stop takes the place of the import button, its focus included.
  stop.focus();
  show(0, 1);
  stop.onclick = () => request.abort();
  request.upload.addEventListener("progress", (event) => show(event.loaded, event.total));
  request.upload.addEventListener("load", () => {
    progress.hidden = true;
    importing.hidden = false;
  });
  request.addEventListener("abort", () => stopped("[data-catalogue-stopped]"));
  request.addEventListener("error", () => {
    // Once the file is up the server goes on with the import, so the page shows where it stands.
    if (!importing.hidden) {
      location.assign(form.action);
      return;
    }
    stopped("[data-catalogue-lost]");
  });
  request.addEventListener("load", () => {
    const page = new DOMParser().parseFromString(request.responseText, "text/html");
    const fresh = page.querySelector("[data-catalogues]");
    if (request.status !== 200 || !fresh) {
      location.assign(request.responseURL || form.action);
      return;
    }
    if (fresh.querySelector(".catalogue-done")) {
      const blocks = document.importNode(fresh, true);
      document.querySelector("[data-catalogues]").replaceWith(blocks);
      blocks.querySelector(".catalogue-done").focus();
      blocks.querySelector("[data-catalogue-upload]").noValidate = true;
      blocks.dispatchEvent(new Event("swapped", { bubbles: true }));
      return;
    }
    settle();
    document
      .querySelector("[data-catalogue-result]")
      .replaceChildren(
        ...document.importNode(fresh.querySelector("[data-catalogue-result]"), true).childNodes,
      );
  });
  request.open("POST", form.action);
  request.send(data);
}

/**
 * Puts a result above the steps, from one of the page's templates with its placeholders filled,
 * or clears it.
 *
 * @param {string | null} selector - The template's selector; null clears the result.
 * @param {Object<string, string>} [values] - The placeholders' values, by name.
 */
function say(selector, values = {}) {
  const slot = document.querySelector("[data-catalogue-result]");
  if (!selector) {
    slot.replaceChildren();
    return;
  }
  const result = document.querySelector(selector).content.cloneNode(true);
  const walker = document.createTreeWalker(result, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    walker.currentNode.data = walker.currentNode.data.replace(
      /\{(\w+)\}/g,
      (found, name) => values[name] ?? found,
    );
  }
  slot.replaceChildren(result);
}

/**
 * Returns a chosen file's name with its size.
 *
 * @param {HTMLFormElement} form - The import's form, which holds the size's text.
 * @param {File} file - The file.
 * @returns {string} The name and the size.
 */
function chosen(form, file) {
  return `${file.name} · ${megabytes(form, file.size)}`;
}

/**
 * Returns a size in megabytes as the page's language writes it, with its unit.
 *
 * @param {HTMLFormElement} form - The import's form, which holds the unit's text.
 * @param {number} bytes - The size in bytes.
 * @returns {string} The size.
 */
function megabytes(form, bytes) {
  return form.dataset.megabytes.replace("{count}", number(bytes));
}

/**
 * Returns a number of bytes in megabytes with at most one decimal, as the page's language writes
 * it.
 *
 * @param {number} bytes - The number of bytes.
 * @returns {string} The number.
 */
function number(bytes) {
  return new Intl.NumberFormat(document.documentElement.lang, {
    maximumFractionDigits: 1,
  }).format(bytes / 2 ** 20);
}

// Who sees a game's art: a tile takes the accent at once and the choice is saved once it rests for
// 400 ms, so arrow keys passing over a tile save nothing. A save that takes longer than 300 ms
// spins the chosen tile's glyph; a newer choice waits for the running save, the last one
// winning. The saved section comes in place with the strip's changed cells cross-fading and its
// toast; a failed save returns the accent to the saved tile and says so in a toast that
// stays.
const stillMotion = matchMedia("(prefers-reduced-motion: reduce)");

document.addEventListener("change", (event) => {
  const form = event.target instanceof Element ? event.target.closest("form[data-art]") : null;
  if (form) {
    clearTimeout(form.restTimer);
    form.restTimer = setTimeout(() => saveArt(form), 400);
  }
});

/**
 * Saves the chosen tile of a game's art, unless it is the saved one or a save is running.
 *
 * @param {HTMLFormElement} form - The choice's form.
 */
async function saveArt(form) {
  const chosen = form.querySelector("input:checked")?.value;
  if (form.dataset.saving || !chosen || chosen === form.dataset.saved) {
    return;
  }
  form.dataset.saving = chosen;
  const section = form.closest("[data-art-section]");
  const timer = setTimeout(() => spin(section, chosen), 300);
  let page = null;
  try {
    const answer = await fetch(form.action, { method: "POST", body: new FormData(form) });
    page = answer.ok ? new DOMParser().parseFromString(await answer.text(), "text/html") : null;
  } catch {
    page = null;
  }
  clearTimeout(timer);
  const fresh = page?.getElementById(section.id);
  if (!fresh) {
    delete form.dataset.saving;
    unspin(section);
    form.querySelector(`input[value="${form.dataset.saved}"]`).checked = true;
    toast(document.querySelector("[data-art-failed]").content.cloneNode(true));
    return;
  }
  // A choice made while this one was saved is kept, and saved next.
  const latest = form.querySelector("input:checked")?.value;
  const before = [...section.querySelectorAll("[data-strip] td")].map((cell) => cell.dataset.shown);
  const focused = section.contains(document.activeElement) ? document.activeElement.value : null;
  const placed = document.importNode(fresh, true);
  section.replaceWith(placed);
  // The keyboard stays on the tile it was on.
  if (focused) {
    placed.querySelector(`input[value="${focused}"]`)?.focus();
  }
  placed.querySelectorAll("[data-strip] td").forEach((cell, index) => {
    if (cell.dataset.shown !== before[index] && !stillMotion.matches) {
      cell.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 160, easing: "linear" });
    }
  });
  const said = page.querySelector(".toast");
  if (said) {
    toast(document.importNode(said, true));
  }
  const next = placed.querySelector("form[data-art]");
  if (latest && latest !== next.dataset.saved) {
    next.querySelector(`input[value="${latest}"]`).checked = true;
    saveArt(next);
  }
}

/**
 * Turns the chosen tile's glyph into the spinning segment and says the choice is being saved.
 *
 * @param {Element} section - The game's art section.
 * @param {string} chosen - The choice being saved.
 */
function spin(section, chosen) {
  const glyph = section.querySelector(`input[value="${chosen}"] ~ .choice-square svg`);
  const use = glyph.querySelector("use");
  glyph.dataset.glyph = use.getAttribute("href");
  use.setAttribute("href", glyph.dataset.glyph.replace(/#.*$/, "#busy"));
  glyph.classList.add("spinning");
  const word = section.querySelector("[data-art-word]");
  word.dataset.word = word.textContent;
  word.textContent = word.dataset.saving;
  word.classList.add("catalogue-art-off");
}

/**
 * Gives the tile that was being saved its glyph back, and the header its word.
 *
 * @param {Element} section - The game's art section.
 */
function unspin(section) {
  const glyph = section.querySelector("svg.spinning");
  if (glyph) {
    glyph.querySelector("use").setAttribute("href", glyph.dataset.glyph);
    glyph.classList.remove("spinning");
  }
  const word = section.querySelector("[data-art-word]");
  word.textContent = word.dataset.word ?? word.textContent;
  word.classList.toggle(
    "catalogue-art-off",
    section.querySelector("form[data-art]").dataset.saved === "nobody",
  );
}

/**
 * Shows a toast at the bottom right of the window.
 *
 * @param {Node} shown - The toast.
 */
function toast(shown) {
  let place = document.querySelector(".toasts");
  if (!place) {
    place = document.createElement("div");
    place.className = "toasts";
    document.getElementById("area").append(place);
  }
  place.replaceChildren(shown);
  place.dispatchEvent(new Event("swapped", { bubbles: true }));
}
