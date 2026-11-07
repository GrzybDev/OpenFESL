// The Daily Claw's machine at play: the picked hero's belts run while the server draws the prize,
// then stop with it under the claw, which drops, takes it, swings to the chute and lets it fall
// into the prize slot. Transforms and opacity only; Skip finishes every move at once.

// How long the belts run before they stop at the prize, in ms.
const RUN = 2400;

/**
 * Returns the frames of a move along an axis.
 *
 * @param {string} axis - X or Y.
 * @param {number} from - Where the move starts, in pixels.
 * @param {number} to - Where it ends, in pixels.
 * @returns {Keyframe[]} The frames.
 */
function along(axis, from, to) {
  return [from, to].map((at) => ({ transform: `translate${axis}(${at}px)` }));
}

/**
 * Starts the belts and the claw's wait, and returns how to stop them or play the taking.
 *
 * @param {HTMLFormElement} form - The grab form, whose picked hero's belts run.
 * @returns {{ran: Promise<void>, stop: function(): void, take: function(string): Promise<void>}}
 *   When the belts have run long enough, how to stop them, and how to take a prize.
 */
export function run(form) {
  const glass = form.querySelector("[data-claw-glass]");
  const belts = [...glass.querySelectorAll(`[data-belts="${form.dataset.hero}"] .claw-belt`)];
  const skip = form.querySelector("[data-claw-skip]");
  const playing = [];
  let skipped = false;
  skip.hidden = false;
  skip.onclick = () => {
    skipped = true;
    for (const animation of playing) {
      animation.finish();
    }
  };
  const loops = belts.map((belt, row) => {
    const half = belt.scrollWidth / 2;
    const frames = row === 1 ? along("X", -half, 0) : along("X", 0, -half);
    return belt.animate(frames, { duration: 1600, iterations: Infinity });
  });

  /**
   * Plays an animation, which Skip finishes.
   *
   * @param {Element} element - What moves.
   * @param {Keyframe[]} frames - Its keyframes.
   * @param {number} duration - How long, in ms.
   * @returns {Promise<void>} When it ends.
   */
  function move(element, frames, duration) {
    const animation = element.animate(frames, {
      duration: skipped ? 0 : duration,
      easing: "cubic-bezier(.2,.8,.2,1)",
      fill: "forwards",
    });
    playing.push(animation);
    return animation.finished.then(() => undefined);
  }

  return {
    ran: new Promise((done) => setTimeout(done, RUN)),
    stop() {
      for (const loop of loops) {
        loop.cancel();
      }
      skip.hidden = true;
    },
    async take(item) {
      const arm = glass.querySelector("[data-claw-arm]");
      const head = arm.querySelector("[data-claw-head]");
      const cable = arm.querySelector("[data-claw-cable]");
      const box = glass.getBoundingClientRect();
      // The prize stops under the claw's third column, on the belt that holds it.
      const target = box.width * 0.84 * 0.42;
      let tile = null;
      const stops = belts.map((belt) => {
        const at = new DOMMatrixReadOnly(getComputedStyle(belt).transform).m41;
        const half = belt.scrollWidth / 2;
        const found = [...belt.querySelectorAll(`[data-item="${item}"]`)].find(
          (each) => each.offsetLeft < half,
        );
        tile ??= found;
        const left = found ? found.offsetLeft : belt.firstElementChild.offsetLeft;
        const width = belt.firstElementChild.offsetWidth;
        let stop = target - left - width / 2;
        while (stop > 0) {
          stop -= half;
        }
        return { belt, at, stop, found };
      });
      for (const loop of loops) {
        loop.cancel();
      }
      await Promise.all(stops.map(({ belt, at, stop }) => move(belt, along("X", at, stop), 900)));
      const row = (stops.find((each) => each.found) ?? stops[1]).belt.closest(".claw-row");
      const drop = row.getBoundingClientRect().top - box.top - 34;
      const x = target - 34;
      await move(arm, along("X", 0, x), 450);
      const down = along("Y", 0, drop);
      const stretched = [{ transform: "scaleY(1)" }, { transform: `scaleY(${1 + drop / 12})` }];
      await Promise.all([move(head, down, 500), move(cable, stretched, 500)]);
      head.classList.add("claw-closed-prongs");
      await new Promise((done) => setTimeout(done, skipped ? 0 : 200));
      const held = (tile ?? stops[1].belt.firstElementChild)
        .querySelector(".claw-tile-art")
        .cloneNode(true);
      held.classList.add("claw-held");
      head.append(held);
      tile?.style.setProperty("visibility", "hidden");
      await Promise.all([
        move(head, down.toReversed(), 500),
        move(cable, stretched.toReversed(), 500),
      ]);
      const chute = box.width * 0.92 - 34;
      await move(arm, along("X", x, chute), 500);
      head.classList.remove("claw-closed-prongs");
      const slot = glass.querySelector("[data-claw-slot]");
      const fall = slot.getBoundingClientRect().top - held.getBoundingClientRect().top;
      await move(held, along("Y", 0, fall), 350);
      for (const animation of held.getAnimations()) {
        animation.cancel();
      }
      held.classList.remove("claw-held");
      slot.replaceChildren(held);
      await new Promise((done) => setTimeout(done, skipped ? 0 : 650));
      skip.hidden = true;
    },
  };
}
