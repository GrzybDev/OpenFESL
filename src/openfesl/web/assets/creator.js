// The hero creator: the hero on the stage follows every choice, the look tabs and Surprise me
// work, and I'm done opens the naming dialog, whose name is checked as it is typed. Without
// scripts the same form shows every list and its naming part, and the server checks the name.
import { checkName, closes, decode, fill, flash, reducedMotion } from "./shared.js";
import { heroStage } from "./stage.js";

// The fields of the look that the hero's model is drawn in.
const LOOK = ["faction", "class", "skin_tone", "hair_colour", "hair_style", "facial_hair"];

const form = document.querySelector("[data-creator]");
if (form) {
  const texts = JSON.parse(form.querySelector("[data-creator-texts]").textContent);
  const naming = form.querySelector("[data-naming]");
  const input = naming.querySelector("[name=name]");
  const done = form.querySelector("[data-done]");
  const stage = heroStage(form.querySelector("[data-stage]"));
  let check = naming.querySelector("[data-check]")?.dataset.check ?? "";

  form.addEventListener("change", (event) => {
    const field = event.target;
    if (field.name === "faction") {
      changeFaction(field.value);
    } else if (field.name === "class") {
      form.dataset.class = field.value;
      hop();
    } else if (field.name === "skin_tone") {
      form.dataset.skin = field.value;
    } else if (field.name === "hair_colour") {
      form.dataset.hairColour = field.value;
    }
    describe();
    // The game's model nods to a faction and flexes for a class; a look only repaints it.
    const reaction = { faction: "nod", class: "flex" }[field.name];
    if (reaction) {
      stage.react(reaction);
    }
  });

  /**
   * Shows the chosen faction's look lists, keeping the place picked in each.
   *
   * @param {string} faction - The faction's name in the game data.
   */
  function changeFaction(faction) {
    form.dataset.faction = faction;
    for (const panel of form.querySelectorAll("[data-look]")) {
      const place = panel.querySelector(":checked")?.dataset.place ?? "0";
      for (const list of panel.querySelectorAll("[data-for]")) {
        const own = list.dataset.for === faction;
        list.toggleAttribute("data-other", !own);
        list.hidden = !own;
        if (own) {
          list.querySelector(`[data-place="${place}"]`).checked = true;
          if (!reducedMotion.matches) {
            flash(list, "fading");
          }
        }
      }
    }
    form.querySelector(".look-note-disc use")?.setAttribute("href", texts.glyphs[faction]);
    hop();
  }

  /** Makes the hero hop and the bubble grow as its faction or class changes. */
  function hop() {
    flash(form.querySelector("[data-hop]"), "hop");
  }

  /** Writes the chosen faction, class and look into the stage, the dialog and the labels. */
  function describe() {
    const { faction, class: heroClass } = form.dataset;
    const kit = `${faction}.${heroClass}`;
    const who = texts.who[kit];
    for (const element of form.querySelectorAll("[data-who]")) {
      element.textContent = decode(who);
    }
    form.querySelector("[data-bubble-who]").textContent = fill(texts.bubble_who, {
      who: decode(who),
    });
    const hair = place("hair_style");
    const beard = place("facial_hair");
    form.querySelector("[data-class-glyph] use").setAttribute("href", texts.classes[heroClass]);
    const chosen = {
      who: decode(who),
      skin: form.dataset.skin,
      hair_colour: form.dataset.hairColour,
      hair: hair ? fill(texts.hair, { style: hair }) : decode(texts.no_hair),
      beard: beard ? fill(texts.beard, { style: beard }) : decode(texts.no_beard),
    };
    form.querySelector("[data-look-text]").textContent = fill(texts.look, chosen);
    const look = new URLSearchParams(LOOK.map((name) => [name, form.elements[name].value]));
    stage.show(look, fill(texts.model_label, chosen));
    const note = form.querySelector("[data-look-note]");
    if (note) {
      note.textContent = fill(texts.note, { faction: decode(texts.factions[faction]) });
    }
    const [skin, hairColour] = form.querySelectorAll("[data-swatch-value]");
    skin.textContent = fill(texts.of_skin, { n: form.dataset.skin });
    hairColour.textContent = fill(texts.of_hair, { n: form.dataset.hairColour });
    // The naming dialog's line of the kit.
    for (const line of form.querySelectorAll("[data-kit]")) {
      line.toggleAttribute("data-other", line.dataset.kit !== kit);
    }
  }

  /**
   * Returns the place in the chosen faction's list of a look's checked tile, 0 for none.
   *
   * @param {string} name - The look's field.
   * @returns {number} The place.
   */
  function place(name) {
    const checked = form.querySelector(
      `[data-for="${form.dataset.faction}"] [name=${name}]:checked`,
    );
    return Number(checked?.dataset.place ?? 0);
  }

  // The look tabs, with arrow keys between them and one tab in the tab order.
  const tabs = [...form.querySelectorAll("[role=tab]")];
  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (event) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (step) {
        const next = tabs[(index + step + tabs.length) % tabs.length];
        select(next);
        next.focus();
      }
    });
  }
  select(tabs[0]);

  /**
   * Shows a look tab's panel.
   *
   * @param {HTMLElement} chosen - The tab.
   */
  function select(chosen) {
    for (const tab of tabs) {
      const on = tab === chosen;
      tab.setAttribute("aria-selected", String(on));
      tab.tabIndex = on ? 0 : -1;
      document.getElementById(tab.getAttribute("aria-controls")).hidden = !on;
    }
  }
  for (const list of form.querySelectorAll("[data-for][data-other]")) {
    list.hidden = true;
  }

  // Surprise me: a random look for the chosen faction and class.
  form.querySelector("[data-surprise]").addEventListener("click", () => {
    for (const name of ["skin_tone", "hair_colour"]) {
      pick(form.querySelectorAll(`[name=${name}]`));
    }
    for (const name of ["hair_style", "facial_hair"]) {
      pick(form.querySelectorAll(`[data-for="${form.dataset.faction}"] [name=${name}]`));
    }
    hop();
    stage.react("wave");
  });

  /**
   * Checks one of some radios at random, as a change of the reader's would.
   *
   * @param {NodeListOf<HTMLInputElement>} radios - The radios.
   */
  function pick(radios) {
    const chosen = radios[Math.floor(Math.random() * radios.length)];
    chosen.checked = true;
    chosen.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // The naming dialog: opened by I'm done, or at once when the server refused the name.
  naming.close();
  done.addEventListener("click", () => {
    naming.showModal();
    input.focus();
  });
  if (form.hasAttribute("data-naming-open")) {
    naming.showModal();
    input.focus();
    input.select();
  }
  closes(naming, done);
  checkName(naming, input, form.dataset.nameCheck, texts.checking, (found) => {
    check = found;
    ready();
  });

  /** Says what Create hero still needs, and shows it unavailable until the name is free. */
  function ready() {
    const why = naming.querySelector("[data-why]");
    const name = input.value.trim();
    const free = check === "free";
    naming.querySelector("[data-create]").setAttribute("aria-disabled", String(!free));
    why.textContent = free
      ? fill(texts.ready, { name })
      : decode(name ? texts.needs_free : texts.needs_name);
  }
  ready();

  // Create hero pressed before the name is free flashes what is missing.
  form.addEventListener("submit", (event) => {
    if (naming.querySelector("[data-create]").getAttribute("aria-disabled") === "true") {
      event.preventDefault();
      flash(naming.querySelector("[data-why]"));
      input.focus();
    }
  });
}
