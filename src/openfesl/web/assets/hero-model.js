// The game's model of a hero, drawn as the game's menu draws its portrait: the skeleton of the
// hero's faction with the meshes of its parts, whose textures are painted into one texture of the
// hero and shaded as the menu's shader (RaShaderPortrait.fx, technique RenderInMenu) shades them.
// The stage loads this module only on a deployment that keeps the game's models.
import {
  AnimationMixer,
  ClampToEdgeWrapping,
  ColorManagement,
  LinearFilter,
  LinearMipmapLinearFilter,
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
 * Shows the game's model of a hero on a canvas, framed in the stage's box.
 *
 * @param {HTMLCanvasElement} canvas - The canvas, which the page sizes.
 * @param {object} figure - The hero, as the server describes it.
 * @param {() => void} lost - Called once the browser takes the canvas's drawing away.
 * @returns {Promise<HeroModel>} The model, drawn once.
 */
export async function showModel(canvas, figure, lost) {
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
  const model = new HeroModel(renderer);
  await model.wear(figure);
  new ResizeObserver(() => model.fit()).observe(canvas);
  return model;
}

/** The game's model of a hero on its canvas, which wears one look after another. */
class HeroModel {
  /**
   * Prepares a model drawn by a renderer.
   *
   * @param {WebGLRenderer} renderer - The renderer of the model's canvas.
   */
  constructor(renderer) {
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
    const [clip, time] = figure.pose;
    this.mixer.stopAllAction();
    this.mixer.clipAction(this.clips[clip]).play();
    this.mixer.setTime(time);
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
    if (canvas.clientWidth > 0) {
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
