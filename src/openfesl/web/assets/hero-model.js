// The game's model of a hero, drawn as the game's menu draws its portrait: the skeleton of the
// hero's faction with the meshes of its parts, whose textures are painted into one texture of the
// hero and shaded as the menu's shader (RaShaderPortrait.fx, technique RenderInMenu) shades them.
// The stage loads this module only on a deployment that keeps the game's models.
import {
  AnimationMixer,
  AnimationUtils,
  ClampToEdgeWrapping,
  ColorManagement,
  LinearFilter,
  LinearMipmapLinearFilter,
  LoopOnce,
  MathUtils,
  Mesh,
  NearestFilter,
  NormalBlending,
  OrthographicCamera,
  PerspectiveCamera,
  PlaneGeometry,
  Scene,
  ShaderMaterial,
  Skeleton,
  Texture,
  Vector3,
  WebGLRenderTarget,
  WebGLRenderer,
} from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

// The game's shader works on the textures' raw values, without converting colour spaces.
ColorManagement.enabled = false;

// The part of the portrait's square frame that the stage shows, as left, top, width and height:
// the stage's 300 x 420 box, with the hero standing on its ground.
const FRAME = [0.2227, 0.1592, 0.5829, 0.8161];
// How long the hero blends between its menu idle and a reaction, in seconds.
const BLEND = 0.2;
// The shortest and longest wait for the next idle of the face, in seconds.
const FACE_WAIT = [4, 8];
// The time between frames, at most 30 a second, in milliseconds; a little less, so that a display
// refreshing twice as often draws every other refresh.
const FRAME_TIME = 1000 / 30 - 2;

const LAYER_VERTEX = /* glsl */ `
varying vec2 vUv;
void main() {
  vUv = uv;
  gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
}`;

// A texture blended by overlay with its tint, in the engine's integer steps, before it is painted
// with straight alpha.
const LAYER_FRAGMENT = /* glsl */ `
uniform sampler2D map;
uniform vec3 tint;
uniform bool tinted;
varying vec2 vUv;
void main() {
  vec4 texel = texture2D(map, vUv);
  vec3 rgb = texel.rgb;
  if (tinted) {
    vec3 base = floor(texel.rgb * 255.0 + 0.5);
    vec3 low = floor(2.0 * tint * base / 255.0);
    vec3 high = 255.0 - floor(2.0 * (255.0 - tint) * (255.0 - base) / 255.0);
    rgb = mix(high, low, step(base, vec3(127.5))) / 255.0;
  }
  gl_FragColor = vec4(rgb, texel.a);
}`;

const HERO_VERTEX = /* glsl */ `
#include <common>
#include <skinning_pars_vertex>
varying vec2 vUv;
varying vec3 vNormal;
varying vec3 vCamVec;
void main() {
  vUv = uv;
  #include <beginnormal_vertex>
  #include <skinbase_vertex>
  #include <skinnormal_vertex>
  #include <begin_vertex>
  #include <skinning_vertex>
  vec4 world = modelMatrix * vec4(transformed, 1.0);
  // The hero stands unturned at the origin, so the world is the game's object space.
  vNormal = normalize(mat3(modelMatrix) * objectNormal);
  vCamVec = normalize(cameraPosition - world.xyz);
  gl_Position = projectionMatrix * viewMatrix * world;
}`;

// Half-Lambert from a light at the camera, pushed by the menu's rim light, through the warp ramp
// times two, and a capped Fresnel rim; as in the game, the interpolated vectors stay unnormalised.
const HERO_FRAGMENT = /* glsl */ `
uniform sampler2D atlas;
uniform sampler2D warp;
uniform vec3 rimLight;
varying vec2 vUv;
varying vec3 vNormal;
varying vec3 vCamVec;
void main() {
  vec3 lightDir = normalize(vCamVec + rimLight);
  float halfLambert = max(0.0, 0.5 * dot(vNormal, lightDir) + 0.5);
  vec3 warped = texture2D(warp, vec2(halfLambert, 0.5)).rgb * 2.0;
  vec3 rgb = texture2D(atlas, vUv).rgb * warped;
  rgb += min(pow(1.0 - clamp(dot(vCamVec, vNormal), 0.0, 1.0), 3.5), 0.3) * 0.9;
  gl_FragColor = vec4(rgb, 1.0);
}`;

const loader = new GLTFLoader();

/**
 * Shows the game's model of a hero on a canvas, framed in the stage's box: in the menu's idle
 * while the canvas is on screen, or still in the menu's pose under reduced motion.
 *
 * @param {HTMLCanvasElement} canvas - The canvas, which the page sizes.
 * @param {object} figure - The hero, as the server describes it.
 * @param {() => void} lost - Called once the browser takes the canvas's drawing away.
 * @param {MediaQueryList} stillness - Whether the reader asks for reduced motion.
 * @returns {Promise<HeroModel>} The model, drawn once.
 */
export async function showModel(canvas, figure, lost, stillness) {
  const renderer = new WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  canvas.addEventListener(
    "webglcontextlost",
    (event) => {
      event.preventDefault();
      lost();
    },
    { once: true },
  );
  renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
  const model = new HeroModel(renderer, stillness);
  await model.wear(figure);
  return model;
}

/** The game's model of a hero on its canvas, which wears one look after another. */
class HeroModel {
  /**
   * Prepares a model drawn by a renderer, which moves only while its canvas is seen.
   *
   * @param {WebGLRenderer} renderer - The renderer of the model's canvas.
   * @param {MediaQueryList} stillness - Whether the reader asks for reduced motion.
   */
  constructor(renderer, stillness) {
    this.renderer = renderer;
    this.asked = 0;
    this.skeleton = "";
    this.parts = "";
    this.root = null;
    this.meshes = [];
    this.atlas = null;
    // The materials live as long as the model, so that their programs stay linked between looks.
    this.material = new ShaderMaterial({
      vertexShader: HERO_VERTEX,
      fragmentShader: HERO_FRAGMENT,
      uniforms: {
        atlas: { value: null },
        warp: { value: null },
        rimLight: { value: new Vector3() },
      },
    });
    this.layerMaterial = new ShaderMaterial({
      vertexShader: LAYER_VERTEX,
      fragmentShader: LAYER_FRAGMENT,
      uniforms: {
        map: { value: null },
        tint: { value: new Vector3() },
        tinted: { value: false },
      },
      transparent: true,
      depthTest: false,
      depthWrite: false,
      blending: NormalBlending,
    });
    this.camera = null;
    this.idle = null;
    this.reaction = null;
    this.still = stillness.matches;
    this.seen = false;
    this.running = false;
    this.last = 0;
    this.nextFace = 0;
    this.watching = new AbortController();
    const { signal } = this.watching;
    this.observers = [
      new ResizeObserver(() => this.fit()),
      new IntersectionObserver(([entry]) => {
        this.seen = entry.isIntersecting;
        this.wake();
      }),
    ];
    for (const observer of this.observers) {
      observer.observe(renderer.domElement);
    }
    document.addEventListener("visibilitychange", () => this.wake(), { signal });
    stillness.addEventListener(
      "change",
      () => {
        this.still = stillness.matches;
        if (this.root) {
          this.settle();
          this.draw();
        }
      },
      { signal },
    );
  }

  /**
   * Puts the hero in a look and draws it, unless a later look was asked for meanwhile.
   *
   * @param {object} figure - The hero, as the server describes it.
   */
  async wear(figure) {
    const asked = ++this.asked;
    const parts = figure.parts.join();
    const [rig, meshes, atlas, warp] = await Promise.all([
      figure.skeleton === this.skeleton ? null : loader.loadAsync(figure.skeleton),
      parts === this.parts && figure.skeleton === this.skeleton
        ? null
        : Promise.all(figure.parts.map((part) => loader.loadAsync(part))),
      paint(this.renderer, this.layerMaterial, figure),
      bitmap(figure.warp),
    ]);
    if (asked !== this.asked) {
      atlas.dispose();
      warp.close();
      return;
    }
    if (rig) {
      this.dropMeshes();
      this.root = rig.scene;
      this.clips = Object.fromEntries(rig.animations.map((clip) => [clip.name, clip]));
      this.mixer = new AnimationMixer(this.root);
      this.skeleton = figure.skeleton;
    }
    const { uniforms } = this.material;
    uniforms.warp.value?.dispose();
    this.atlas?.dispose();
    this.atlas = atlas;
    uniforms.atlas.value = atlas.texture;
    uniforms.warp.value = texture(warp, LinearFilter);
    uniforms.rimLight.value.set(...figure.light);
    if (meshes) {
      this.dropMeshes();
      this.dress(meshes);
      this.parts = parts;
    }
    this.camera = portraitCamera(figure.camera);
    this.roles = figure.roles;
    this.pose = figure.pose;
    if (rig) {
      // The idles of the face move only its own joints, on top of the menu's idle.
      this.faces = (figure.roles.faces ?? []).map((name) =>
        AnimationUtils.makeClipAdditive(this.clips[name]),
      );
      this.idle = null;
      this.reaction = null;
    }
    this.settle();
    this.draw();
  }

  /** Holds the hero still in the menu's pose under reduced motion, or plays the menu's idle. */
  settle() {
    if (this.still) {
      const [clip, time] = this.pose;
      this.mixer.stopAllAction();
      this.idle = null;
      this.reaction = null;
      this.mixer.clipAction(this.clips[clip]).play();
      this.mixer.setTime(time);
    } else if (!this.idle) {
      this.mixer.stopAllAction();
      this.idle = this.mixer.clipAction(this.clips[this.roles.idle[0]]).play();
      this.nextFace = wait(FACE_WAIT);
    }
    this.wake();
  }

  /**
   * Plays a reaction once, cutting short the one playing, and returns to the menu's idle; a hero
   * held still does not react.
   *
   * @param {string} role - The reaction's role, such as nod.
   */
  react(role) {
    const clip = this.clips[this.roles[role]?.[0]];
    if (!this.idle || !clip) {
      return;
    }
    const action = this.mixer.clipAction(clip);
    const from = this.reaction ?? this.idle;
    action.reset().setLoop(LoopOnce, 1).play();
    action.clampWhenFinished = true;
    if (from !== action) {
      from.crossFadeTo(action, BLEND, false);
    }
    this.reaction = action;
  }

  /** Starts the frames while the hero moves and its canvas is seen, and stops them otherwise. */
  wake() {
    const running = Boolean(this.idle) && this.seen && !document.hidden;
    if (running && !this.running) {
      this.last = performance.now();
      requestAnimationFrame((time) => this.tick(time));
    }
    this.running = running;
  }

  /**
   * Moves the hero on and draws it, at most 30 times a second.
   *
   * @param {number} now - The time of the frame, in milliseconds.
   */
  tick(now) {
    if (!this.running) {
      return;
    }
    requestAnimationFrame((time) => this.tick(time));
    const elapsed = now - this.last;
    if (elapsed < FRAME_TIME) {
      return;
    }
    this.last = now;
    // A frame long delayed, as after a busy moment, does not throw the hero far ahead.
    const step = Math.min(elapsed, 100) / 1000;
    this.nextFace -= step;
    if (this.nextFace <= 0 && this.faces.length > 0) {
      const face = this.faces[Math.floor(Math.random() * this.faces.length)];
      this.mixer.clipAction(face).reset().setLoop(LoopOnce, 1).play();
      this.nextFace = wait(FACE_WAIT);
    }
    const reaction = this.reaction;
    if (reaction && reaction.getClip().duration - reaction.time <= BLEND) {
      this.idle.enabled = true;
      this.idle.play();
      reaction.crossFadeTo(this.idle, BLEND, false);
      this.reaction = null;
    }
    this.mixer.update(step);
    this.draw();
  }

  /**
   * Binds the meshes of the hero's parts to its skeleton, by the names of their joints.
   *
   * @param {object[]} parts - The parts' loaded files.
   */
  dress(parts) {
    const joints = new Map();
    this.root.traverse((node) => joints.set(node.name, node));
    for (const part of parts) {
      part.scene.traverse((node) => {
        if (node.isSkinnedMesh) {
          const bones = node.skeleton.bones.map((bone) => joints.get(bone.name));
          node.bind(new Skeleton(bones, node.skeleton.boneInverses), node.bindMatrix);
          node.material = this.material;
          // A skinned mesh's bounds do not follow its pose.
          node.frustumCulled = false;
          this.meshes.push(node);
        }
      });
    }
    this.root.add(...this.meshes);
  }

  /** Takes the meshes of the hero's parts off and frees them. */
  dropMeshes() {
    for (const mesh of this.meshes) {
      mesh.removeFromParent();
      mesh.geometry.dispose();
    }
    this.meshes = [];
  }

  /** Sizes the drawing to the canvas and draws the hero again. */
  fit() {
    const canvas = this.renderer.domElement;
    if (canvas.clientWidth > 0 && this.root) {
      this.renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
      this.draw();
    }
  }

  /** Draws the hero in its current pose. */
  draw() {
    this.root.updateMatrixWorld(true);
    this.renderer.setClearColor(0x000000, 0);
    this.renderer.render(this.root, this.camera);
  }

  /** Frees everything the model holds, its canvas's drawing too. */
  dispose() {
    ++this.asked;
    this.idle = null;
    this.wake();
    this.watching.abort();
    for (const observer of this.observers) {
      observer.disconnect();
    }
    this.dropMeshes();
    this.material.uniforms.warp.value?.dispose();
    this.material.dispose();
    this.layerMaterial.dispose();
    this.atlas?.dispose();
    this.renderer.dispose();
  }
}

/**
 * Paints a hero's textures into one texture, each tinted, from the lowest layer up, as the game
 * paints them before the hero is drawn.
 *
 * @param {WebGLRenderer} renderer - The renderer that keeps the texture.
 * @param {ShaderMaterial} material - The material that paints each texture.
 * @param {object} figure - The hero, whose layers name the textures and where they land.
 * @returns {Promise<WebGLRenderTarget>} The hero's texture.
 */
async function paint(renderer, material, figure) {
  const size = figure.atlas;
  const pictures = await Promise.all(figure.layers.map((layer) => bitmap(layer.texture)));
  const target = new WebGLRenderTarget(size, size, {
    depthBuffer: false,
    generateMipmaps: true,
    minFilter: LinearMipmapLinearFilter,
    magFilter: LinearFilter,
  });
  // The camera's y counts the texture's rows from the top, which lands row 0 in the target's
  // first row, where glTF's v = 0 reads it.
  const camera = new OrthographicCamera(0, size, size, 0, -1, 1);
  const quad = new Mesh(new PlaneGeometry(1, 1), material);
  const scene = new Scene().add(quad);
  const { uniforms } = material;
  renderer.setRenderTarget(target);
  renderer.setClearColor(0x000000, 1);
  renderer.clear();
  // Each texture is drawn on the ones before it, with the one material.
  renderer.autoClear = false;
  figure.layers.forEach((layer, index) => {
    const picture = pictures[index];
    const [x, y] = layer.at;
    uniforms.map.value = texture(picture, NearestFilter);
    uniforms.tint.value.set(...(layer.tint ?? [0, 0, 0]));
    uniforms.tinted.value = layer.tint !== null;
    quad.scale.set(picture.width, picture.height, 1);
    quad.position.set(x + picture.width / 2, y + picture.height / 2, 0);
    renderer.render(scene, camera);
    uniforms.map.value.dispose();
    picture.close();
  });
  renderer.autoClear = true;
  renderer.setRenderTarget(null);
  quad.geometry.dispose();
  return target;
}

/**
 * Loads a picture, decoded off the page's thread, its alpha and colours untouched.
 *
 * @param {string} address - The picture's address.
 * @returns {Promise<ImageBitmap>} The picture.
 */
async function bitmap(address) {
  const answer = await fetch(address);
  if (!answer.ok) {
    throw new Error(`${address} answered ${answer.status}`);
  }
  return createImageBitmap(await answer.blob(), {
    premultiplyAlpha: "none",
    colorSpaceConversion: "none",
  });
}

/**
 * Wraps a picture as a texture whose first row is v = 0, as glTF reads textures.
 *
 * @param {ImageBitmap} picture - The picture.
 * @param {number} filter - How the texture is sampled.
 * @returns {Texture} The texture.
 */
function texture(picture, filter) {
  const wrapped = new Texture(picture);
  wrapped.flipY = false;
  wrapped.premultiplyAlpha = false;
  wrapped.generateMipmaps = false;
  wrapped.minFilter = filter;
  wrapped.magFilter = filter;
  wrapped.wrapS = ClampToEdgeWrapping;
  wrapped.wrapT = ClampToEdgeWrapping;
  wrapped.needsUpdate = true;
  return wrapped;
}

/**
 * Returns a random wait between two lengths.
 *
 * @param {number[]} range - The shortest and the longest wait.
 * @returns {number} The wait.
 */
function wait([shortest, longest]) {
  return shortest + Math.random() * (longest - shortest);
}

/**
 * Returns the camera of the game's portrait, which circles the hero, showing the stage's part.
 *
 * @param {object} spec - The camera as the server describes it, its angles in degrees.
 * @returns {PerspectiveCamera} The camera.
 */
function portraitCamera(spec) {
  const camera = new PerspectiveCamera(MathUtils.radToDeg(spec.fov), 1, spec.near, spec.far);
  const azimuth = MathUtils.degToRad(spec.azimuth - 180);
  const zenith = MathUtils.degToRad(spec.zenith);
  const target = new Vector3(...spec.look_at);
  camera.position.set(
    target.x + spec.distance * Math.sin(zenith) * Math.sin(azimuth),
    target.y + spec.distance * Math.cos(zenith),
    target.z + spec.distance * Math.sin(zenith) * Math.cos(azimuth),
  );
  camera.lookAt(target);
  const [left, top, width, height] = FRAME;
  camera.setViewOffset(1, 1, left, top, width, height);
  return camera;
}
