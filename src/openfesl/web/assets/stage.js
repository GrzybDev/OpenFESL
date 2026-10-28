// The hero's stage: on a deployment that keeps the game's models of heroes, the game's model takes
// the place of the class glyph and look in words once its first frame is drawn, and they come
// back for good whenever the model cannot be shown, as in a browser without WebGL2, with files
// that fail to load or once the browser takes the model's drawing away. Without scripts they
// stay, and so they do where WebGL is drawn without a graphics card, as loading the model there
// holds the page up for about a second.
import { reducedMotion } from "./naming.js";

// The renderers that draw WebGL in software, as browsers name them where they do.
const SOFTWARE = /swiftshader|llvmpipe|softpipe|lavapipe|basic render/i;

/**
 * Prepares a hero's stage, loading the game's model where the page offers it.
 *
 * @param {HTMLElement} stage - The stage, whose [data-model] script describes the hero's model.
 * @returns {{show: Function, react: Function}} The stage.
 */
export function heroStage(stage) {
  const described = stage.querySelector("script[data-model]");
  if (!described || !("WebGL2RenderingContext" in window) || drawnInSoftware()) {
    return { show() {}, react() {} };
  }
  const standIn = stage.querySelector("[data-stand-in]");
  const caption = stage.querySelector("[data-caption]");
  const ownCaption = caption.textContent;
  const canvas = document.createElement("canvas");
  canvas.className = "stage-model";
  canvas.setAttribute("aria-hidden", "true");
  standIn.after(canvas);
  let model = null;
  let wanted = null;
  let asked = 0;
  let shown = 0;
  let reaction = "";
  let failed = false;
  start(JSON.parse(described.textContent));

  /**
   * Loads the model and shows it in the glyph's place, in the look last chosen meanwhile.
   *
   * @param {object} figure - The hero as the page describes it.
   */
  async function start(figure) {
    try {
      const { showModel } = await import("./hero-model.js");
      model = await showModel(canvas, figure, fallBack, reducedMotion);
      if (wanted) {
        await model.wear(await describe(wanted));
      }
    } catch {
      fallBack();
      return;
    }
    if (!failed) {
      stage.dataset.shown = "model";
      standIn.setAttribute("aria-hidden", "true");
      canvas.removeAttribute("aria-hidden");
      canvas.setAttribute("role", "img");
      canvas.setAttribute("aria-label", stage.dataset.modelLabel);
      caption.textContent = stage.dataset.modelCaption;
      play();
    }
  }

  /**
   * Returns the server's description of the hero in a look.
   *
   * @param {URLSearchParams} look - The look, as the creator's fields name it.
   * @returns {Promise<object>} The hero as the server describes it.
   */
  async function describe(look) {
    const answer = await fetch(`${stage.dataset.figures}?${look}`);
    if (!answer.ok) {
      throw new Error(`the hero's model answered ${answer.status}`);
    }
    return answer.json();
  }

  /** Plays the reaction to the last choice, once the model wears the look it brought. */
  function play() {
    if (model && stage.dataset.shown && shown === asked && reaction) {
      model.react(reaction);
      reaction = "";
    }
  }

  /** Shows the glyph and look in words for good, freeing the model. */
  function fallBack() {
    if (failed) {
      return;
    }
    failed = true;
    model?.dispose();
    canvas.remove();
    delete stage.dataset.shown;
    standIn.removeAttribute("aria-hidden");
    caption.textContent = ownCaption;
  }

  return {
    /**
     * Shows the model in another look once it is loaded, and gives it the look's label.
     *
     * @param {URLSearchParams} look - The look, as the creator's fields name it.
     * @param {string} label - The model's label for screen readers.
     */
    async show(look, label) {
      stage.dataset.modelLabel = label;
      wanted = look;
      if (!model || failed) {
        return;
      }
      canvas.setAttribute("aria-label", label);
      const number = ++asked;
      // Choices made at once, as by Surprise me, ask the server only for the last look.
      await Promise.resolve();
      if (number !== asked) {
        return;
      }
      try {
        const figure = await describe(look);
        if (number === asked && !failed) {
          await model.wear(figure);
          shown = number;
          play();
        }
      } catch {
        fallBack();
      }
    },
    /**
     * Has the model react to a choice once the look that the choice brought is shown.
     *
     * @param {string} role - The reaction's role, such as nod.
     */
    react(role) {
      reaction = role;
      play();
    },
  };
}

/**
 * Tells whether the browser draws WebGL2 in software, or not at all.
 *
 * @returns {boolean} Whether its renderer is a software one, as far as the browser names it.
 */
function drawnInSoftware() {
  const gl = document.createElement("canvas").getContext("webgl2");
  if (!gl) {
    return true;
  }
  const debug = gl.getExtension("WEBGL_debug_renderer_info");
  const renderer = String(gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : gl.RENDERER));
  gl.getExtension("WEBGL_lose_context")?.loseContext();
  return SOFTWARE.test(renderer);
}
