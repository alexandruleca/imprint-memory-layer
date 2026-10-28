// Rebuild Echo's bundled looks. No network or model download occurs at runtime.
// Echo's own look, a synaptic core, is generated here. The anime heads adapt
// CC0 VRoid beta samples, explicitly confirmed by pixiv's original release
// page; the build refuses any other license. Each head gets a generated
// synaptic halo.
//   node scripts/build-echo-avatar.mjs [echo|iris|kai] [local.vrm]
// Without an id it rebuilds every look, fetching head sources from the pinned
// mirror. The optional local file makes rebuilding a head independent of it.
import fs from "node:fs/promises";
import crypto from "node:crypto";
import zlib from "node:zlib";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as THREE from "three";
import { mergeGeometries, mergeVertices } from "three/addons/utils/BufferGeometryUtils.js";
const here = path.dirname(fileURLToPath(import.meta.url));
const revision = "e16eb187100149a315ad92c3c9968f1d5baa6c7d";
const SKIN = [0.87, 0.96, 1, 1];
// Each head is cut from one sample and given its own palette. Shared node
// names (Echo_Face, Echo_Hair, Echo_EyeL/R) are the roles the renderer animates.
// Every look declares its motion in node extras (see coreLook).
const AVATARS = {
  echo: {
    node: "Imprint_Echo",
    version: 6,
    artwork: "Echo synaptic core",
    generator: "Imprint Echo core generator v2",
    look: coreLook,
  },
  iris: {
    file: "Victoria_Rubin.vrm",
    sha256: "b1372131bdbf233f46320146d565f342a7e4f6f4b8f2aefb301f1a283ec07e1e",
    title: "Victoria Rubin",
    node: "Imprint_Iris",
    version: 4,
    artwork: "Iris anime hologram",
    generator: "Imprint Echo anime-head pipeline v5",
    // Straight-cut bangs stay as modeled; the locks fold just below the jaw.
    center: 1.57,
    hairBelow: 1.44,
    hairFold: 0.3,
    // Folded ends also draw in toward the head, so they taper instead of splaying.
    hairTuck: 0.35,
    colors: [[/_Hair_00_HAIR_0[12]$/, [1, 1, 1, 1]], [/_Face(Eyeline|Eyelash|Brow)_/, [0.08, 0.08, 0.16, 1]]],
    // The source's ribbon, the side tail it ties (hair primitives) and iris highlights.
    drop: /_Hair_00_HAIR_03$|_EyeHighlight_/,
    dropHair: [42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 58, 59],
    iris: paleIris,
    // Deep indigo hair, bangs included, keeping each strand's sheen.
    recolor: [[/_Hair_00_HAIR_0[12]$/, [0.012, 0.016, 0.06], [0.15, 0.19, 0.46]]],
    expression: null,
  },
  kai: {
    file: "HairSample_Male.vrm",
    sha256: "7aeca142dabbc26ba0f5b9c998c8f3cb6df341287d67788427ac837223785a3f",
    title: "HairSample_Male",
    node: "Imprint_Kai",
    version: 3,
    artwork: "Kai anime hologram",
    generator: "Imprint Echo anime-head pipeline v5",
    // The spiky short cut stays as modeled.
    center: 1.63,
    hairBelow: null,
    hairFold: 1,
    hairTuck: 0,
    // Recolored textures carry their own color; the factor leaves them as is.
    colors: [[/_Hair_00_HAIR$|_EyeIris_/, [1, 1, 1, 1]], [/_Face(Eyeline|Eyelash|Brow)_/, [0.16, 0.09, 0.05, 1]]],
    drop: null,
    dropHair: [],
    iris: null,
    // Texture brightness remapped onto a ramp: bright orange hair, warm brown eyes.
    recolor: [
      [/_Hair_00_HAIR$/, [0.55, 0.16, 0.02], [1, 0.62, 0.22]],
      [/_EyeIris_/, [0.12, 0.05, 0.02], [0.66, 0.4, 0.17]],
    ],
    // A set, determined frown: the source's angry expression on the brows and
    // eyes only (fading in above the mouth), so the mouth stays at rest.
    expression: { preset: "angry", weight: 0.55, above: 1.565, fade: 0.02 },
  },
};
const wanted = process.argv[2] ? [process.argv[2]] : Object.keys(AVATARS);
for (const id of wanted) {
  const avatar = AVATARS[id];
  if (!avatar) throw Error(`Unknown avatar ${id}; expected ${Object.keys(AVATARS).join(", ")}`);
  let glb;
  if (avatar.look) glb = core(avatar);
  else {
    const url = `https://raw.githubusercontent.com/madjin/vrm-samples/${revision}/vroid/beta/${avatar.file}`;
    const data = process.argv[3]
      ? await fs.readFile(process.argv[3])
      : Buffer.from(await (await fetch(url)).arrayBuffer());
    glb = head(avatar, data);
  }
  const dest = path.resolve(here, `../public/models/imprint-${id}.glb`);
  await fs.writeFile(dest, glb);
  console.log(`${dest}: ${glb.length} bytes, ${avatar.artwork}`);
}

// One glTF document under a named root node, written as a binary GLB.
function gltf(avatar, copyright, extras) {
const result = {
  asset: { version: "2.0", generator: avatar.generator, copyright },
  scene: 0,
  scenes: [{ nodes: [0] }],
  nodes: [{ name: avatar.node, children: [], extras: { version: avatar.version, artwork: avatar.artwork, ...extras } }],
  meshes: [],
  materials: [],
  textures: [],
  images: [],
  accessors: [],
  bufferViews: [],
  buffers: [],
};
const chunks = [];
let offset = 0;
function view(bytes) {
  const padded = Buffer.alloc((bytes.length + 3) & ~3);
  Buffer.from(bytes).copy(padded);
  const i = result.bufferViews.length;
  result.bufferViews.push({
    buffer: 0,
    byteOffset: offset,
    byteLength: bytes.length,
  });
  chunks.push(padded);
  offset += padded.length;
  return i;
}
function accessor(values, size = 3, type = 5126) {
  const bytes =
    type === 5126 ? new Float32Array(values) : type === 5123 ? new Uint16Array(values) : new Uint32Array(values);
  const a = {
    bufferView: view(new Uint8Array(bytes.buffer)),
    componentType: type,
    count: values.length / size,
    type: { 1: "SCALAR", 2: "VEC2", 3: "VEC3", 4: "VEC4" }[size],
  };
  if (size === 3 && type === 5126) {
    a.min = [0, 1, 2].map((c) =>
      values
        .filter((_, i) => i % 3 === c)
        .reduce((v, x) => Math.min(v, x), Infinity),
    );
    a.max = [0, 1, 2].map((c) =>
      values
        .filter((_, i) => i % 3 === c)
        .reduce((v, x) => Math.max(v, x), -Infinity),
    );
  }
  result.accessors.push(a);
  return result.accessors.length - 1;
}
// Triangle indices, in 16 bits when they fit.
function indexAccessor(values) {
  return accessor(values, 1, values.every((v) => v < 65536) ? 5123 : 5125);
}
function glb() {
  if (result.materials.some((m) => m.extensions?.KHR_materials_unlit)) result.extensionsUsed = ["KHR_materials_unlit"];
  // glTF arrays, when present, hold at least one item.
  for (const key of ["textures", "images", "samplers"]) if (!result[key]?.length) delete result[key];
  result.buffers = [{ byteLength: offset }];
  const body = Buffer.concat(chunks);
  const json = Buffer.from(JSON.stringify(result));
  const padded = Buffer.alloc((json.length + 3) & ~3, 32);
  json.copy(padded);
  const header = Buffer.alloc(20);
  header.write("glTF");
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(28 + padded.length + body.length, 8);
  header.writeUInt32LE(padded.length, 12);
  header.write("JSON", 16);
  const bh = Buffer.alloc(8);
  bh.writeUInt32LE(body.length);
  bh.write("BIN\0", 4);
  return Buffer.concat([header, padded, bh, body]);
}
return { result, view, accessor, indexAccessor, glb };
}

function head(avatar, data) {
if (crypto.createHash("sha256").update(data).digest("hex") !== avatar.sha256)
  throw Error("Source model checksum mismatch");
const jsonLength = data.readUInt32LE(12),
  source = JSON.parse(data.subarray(20, 20 + jsonLength));
const binary = data.subarray(28 + jsonLength);
if (source.extensions.VRM.meta.licenseName !== "CC0")
  throw Error("Expected a CC0 beta model");
const { result, view, accessor, indexAccessor, glb } = gltf(avatar, `pixiv ${avatar.title}: CC0-1.0; Imprint adaptations: Apache-2.0`, {
  source: `${avatar.title} / pixiv / CC0`,
  license: "CC0-1.0 source; Apache-2.0 adaptations",
  morphs: "jawOpen, blink",
});
result.samplers = source.samplers;
const sizes = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 };
function read(id) {
  const a = source.accessors[id],
    v = source.bufferViews[a.bufferView],
    size = sizes[a.type],
    n = { 5126: 4, 5125: 4, 5123: 2, 5121: 1 }[a.componentType],
    start = (v.byteOffset || 0) + (a.byteOffset || 0);
  const values = [];
  for (let i = 0; i < a.count; i++)
    for (let k = 0; k < size; k++) {
      const at = start + i * (v.byteStride || size * n) + k * n;
      values.push(
        a.componentType === 5126
          ? binary.readFloatLE(at)
          : n === 4
            ? binary.readUInt32LE(at)
            : n === 2
              ? binary.readUInt16LE(at)
              : binary[at],
      );
    }
  return values;
}
const imageMap = new Map(),
  materialMap = new Map();
function material(id) {
  if (materialMap.has(id)) return materialMap.get(id);
  const s = source.materials[id],
    t = source.textures[s.pbrMetallicRoughness.baseColorTexture.index];
  const color = avatar.colors.find(([pattern]) => pattern.test(s.name))?.[1] ?? SKIN;
  const generated = avatar.iris && /_EyeIris_/.test(s.name);
  const ramp = avatar.recolor.find(([pattern]) => pattern.test(s.name));
  let ti = generated || ramp ? undefined : imageMap.get(t.source);
  if (generated) {
    // Same UV layout as the source sheet: one oval per eye.
    const image = result.images.push({ bufferView: view(avatar.iris()), mimeType: "image/png" }) - 1;
    ti = result.textures.push({ sampler: t.sampler, source: image }) - 1;
  } else if (ramp) {
    const im = source.images[t.source],
      v = source.bufferViews[im.bufferView];
    const png = binary.subarray(v.byteOffset || 0, (v.byteOffset || 0) + v.byteLength);
    const image = result.images.push({ bufferView: view(recolor(png, ramp[1], ramp[2])), mimeType: "image/png" }) - 1;
    ti = result.textures.push({ sampler: t.sampler, source: image }) - 1;
  } else if (ti === undefined) {
    const im = source.images[t.source],
      v = source.bufferViews[im.bufferView];
    const image =
      result.images.push({
        bufferView: view(
          binary.subarray(
            v.byteOffset || 0,
            (v.byteOffset || 0) + v.byteLength,
          ),
        ),
        mimeType: im.mimeType,
      }) - 1;
    ti = result.textures.push({ sampler: t.sampler, source: image }) - 1;
    imageMap.set(t.source, ti);
  }
  // Head surfaces take the renderer's light; generated parts are light themselves.
  const i =
    result.materials.push({
      name: s.name,
      pbrMetallicRoughness: {
        baseColorTexture: { index: ti },
        baseColorFactor: color,
        metallicFactor: 0,
        roughnessFactor: 1,
      },
      alphaMode: s.alphaMode,
      alphaCutoff: s.alphaCutoff,
      doubleSided: s.doubleSided,
    }) - 1;
  materialMap.set(id, i);
  return i;
}
const transformed = new Map(),
  stored = new Map();
// Source attribute values in the output's space, read once per accessor.
function values(id, kind, hair = false) {
  const key = `${id}:${kind}:${hair}`;
  if (!transformed.has(key)) {
    const raw = read(id);
    transformed.set(key, kind === "uv" ? raw
      : kind === "normal" ? raw.map((v, i) => (i % 3 === 1 ? v : -v))
        : position(raw, hair, kind === "delta"));
  }
  return transformed.get(key);
}
function attribute(id, kind, hair = false) {
  const key = `${id}:${kind}:${hair}`;
  if (!stored.has(key)) stored.set(key, accessor(values(id, kind, hair), kind === "uv" ? 2 : 3));
  return stored.get(key);
}
const scale = 7.8;
// VRM 0.x faces -Z. Bake its bind pose into ordinary glTF, keeping only the head.
// An avatar with hairBelow folds the long rear locks into a shoulder-free bob.
function position(values, hair = false, delta = false) {
  const cut = hair ? avatar.hairBelow : null;
  return values.map((v, i) => {
    const below = cut !== null && !delta ? Math.max(0, cut - values[i - (i % 3) + 1]) : 0;
    if (i % 3 === 0 || i % 3 === 2) return -v * (1 - avatar.hairTuck * Math.min(1, below / 0.1)) * scale;
    if (delta) return v * scale;
    return ((below > 0 ? cut + (v - cut) * avatar.hairFold : v) - avatar.center) * scale;
  });
}
// Mouth and blink come from the model's own VRM expression presets.
const preset = (name) =>
  source.extensions.VRM.blendShapeMaster.blendShapeGroups.find((g) => g.presetName === name).binds[0].index;
const morphs = [preset("a"), preset("blink")];
// An avatar with an expression keeps that preset partly applied at rest.
const baked = avatar.expression ? { ...avatar.expression, target: preset(avatar.expression.preset) } : null;
function bake(base, delta) {
  return base.map((v, i) => {
    const y = base[i - (i % 3) + 1],
      mask = Math.min(1, Math.max(0, (y - baked.above) / baked.fade));
    return v + baked.weight * mask * delta[i];
  });
}
// A face primitive's positions, with any resting expression baked in.
const bakedKey = (p) => `${p.attributes.POSITION}:${p.targets[baked.target].POSITION}:baked`;
function facePositions(p) {
  if (!baked) return values(p.attributes.POSITION, "position");
  if (!transformed.has(bakedKey(p)))
    transformed.set(bakedKey(p), position(bake(read(p.attributes.POSITION), read(p.targets[baked.target].POSITION))));
  return transformed.get(bakedKey(p));
}
function facePosition(p) {
  if (!baked) return attribute(p.attributes.POSITION, "position");
  if (!stored.has(bakedKey(p))) stored.set(bakedKey(p), accessor(facePositions(p)));
  return stored.get(bakedKey(p));
}
// The eyes turn as the source turns them: triangles bound to the humanoid
// leftEye/rightEye bones become their own nodes, pivoting where those bones
// sit (VRM 0.x bind poses carry translations only), for the renderer to aim.
const bones = Object.fromEntries(source.extensions.VRM.humanoid.humanBones.map((b) => [b.bone, b.node]));
const parents = new Map();
source.nodes.forEach((n, i) => (n.children || []).forEach((child) => parents.set(child, i)));
function bindPosition(node) {
  const at = [0, 0, 0];
  for (let n = node; n !== undefined; n = parents.get(n))
    (source.nodes[n].translation || [0, 0, 0]).forEach((v, k) => (at[k] += v));
  return at;
}
const faceSkin = source.skins[source.nodes.find((n) => n.mesh === 0).skin];
const eyes = [["Echo_EyeL", bones.leftEye], ["Echo_EyeR", bones.rightEye]].map(([name, node]) =>
  ({ name, joint: faceSkin.joints.indexOf(node), pivot: position(bindPosition(node)), primitives: [] }));
// The look-at range the source declares (degrees at full deflection) bounds the eyes.
const lookAt = source.extensions.VRM.firstPerson;
const gaze = [lookAt.lookAtHorizontalOuter.yRange, lookAt.lookAtVerticalUp.yRange].map((degrees) => (degrees * Math.PI) / 180);
const faceTargets = (p) => morphs.map((index) => p.targets[index].POSITION);
function boundJoint(p) {
  const joints = read(p.attributes.JOINTS_0),
    weights = read(p.attributes.WEIGHTS_0);
  return (v) => {
    let best = 0;
    for (let k = 1; k < 4; k++) if (weights[v * 4 + k] > weights[v * 4 + best]) best = k;
    return joints[v * 4 + best];
  };
}
// An eye's own vertices, relative to its pivot, with the face's morph targets.
function eyePrimitive(p, eye, triangles) {
  const order = new Map();
  for (const v of triangles) if (!order.has(v)) order.set(v, order.size);
  const pick = (all, size, shift) =>
    [...order.keys()].flatMap((v) => all.slice(v * size, v * size + size).map((x, k) => x - (shift ? eye.pivot[k] : 0)));
  return {
    attributes: {
      POSITION: accessor(pick(facePositions(p), 3, true)),
      NORMAL: accessor(pick(values(p.attributes.NORMAL, "normal"), 3, false)),
      TEXCOORD_0: accessor(pick(values(p.attributes.TEXCOORD_0, "uv"), 2, false), 2),
    },
    indices: indexAccessor(triangles.map((v) => order.get(v))),
    material: material(p.material),
    targets: faceTargets(p).map((id) => ({ POSITION: accessor(pick(values(id, "delta"), 3, false)) })),
  };
}
const faceMorphs = { weights: [0, 0], extras: { targetNames: ["jawOpen", "blink"] } };
for (const mi of [0, 2]) {
  const s = source.meshes[mi],
    primitives = [];
  for (const [index, p] of s.primitives.entries()) {
    if (avatar.drop?.test(source.materials[p.material].name)) continue;
    if (mi === 2 && avatar.dropHair.includes(index)) continue;
    let indices = read(p.indices);
    if (mi === 0) {
      const joint = boundJoint(p),
        rest = [];
      for (let t = 0; t < indices.length; t += 3) {
        const triangle = indices.slice(t, t + 3),
          eye = eyes.find((e) => triangle.every((v) => joint(v) === e.joint));
        (eye ? eye.triangles ??= [] : rest).push(...triangle);
      }
      for (const eye of eyes) if (eye.triangles) eye.primitives.push(eyePrimitive(p, eye, eye.triangles));
      eyes.forEach((eye) => delete eye.triangles);
      if (!rest.length) continue;
      indices = rest;
    }
    const attributes = {
      POSITION: mi === 0 ? facePosition(p) : attribute(p.attributes.POSITION, "position", true),
      NORMAL: attribute(p.attributes.NORMAL, "normal"),
      TEXCOORD_0: attribute(p.attributes.TEXCOORD_0, "uv"),
    };
    const targets =
      mi === 0
        ? faceTargets(p).map((id) => ({ POSITION: attribute(id, "delta") }))
        : undefined;
    primitives.push({
      attributes,
      indices: indexAccessor(indices),
      material: material(p.material),
      ...(targets ? { targets } : {}),
    });
  }
  const mesh =
    result.meshes.push({
      name: mi === 0 ? "Echo_Face" : "Echo_Hair",
      primitives,
      ...(mi === 0 ? faceMorphs : {}),
    }) - 1;
  result.nodes[0].children.push(result.nodes.length);
  result.nodes.push({ name: mi === 0 ? "Echo_Face" : "Echo_Hair", mesh });
}
for (const eye of eyes) {
  if (!eye.primitives.length) continue;
  const mesh = result.meshes.push({ name: eye.name, primitives: eye.primitives, ...faceMorphs }) - 1;
  result.nodes[0].children.push(result.nodes.length);
  result.nodes.push({ name: eye.name, mesh, translation: eye.pivot, extras: { gaze } });
}
haloLook(parts(result, view, accessor, indexAccessor));
return glb();
}

// Echo's own look: a synaptic core. Neurons fill a sphere around a bright
// nucleus, wired by curved axons, inside tilted orbits and a ring of ticks.
// Its motion is data on the nodes, for the renderer: "spin" turns a node
// (radians per second about x, y, z), "pulse" grows it with Echo's voice,
// "gaze" aims it at the pointer within yaw and pitch limits (radians) and
// "flow" sends light through it: [cycles per second, afterglow in cycles,
// swelling], passing each vertex at the moment in its first texture coordinate.
function coreLook(add) {
  const DEG = Math.PI / 180;
  const line = color("#0b79ad", 0.9),
    arc = color("#1ea6e3", 0.88),
    glow = color("#6fd3ff", 0.92);
  const random = seeded(0xec40);
  const ring = (radius, width, from, to) =>
    new THREE.RingGeometry(radius - width / 2, radius + width / 2, Math.max(4, Math.round(((to - from) / 360) * 160)), 1, from * DEG, (to - from) * DEG);
  const arcs = (radius, width, spans) => mergeGeometries(spans.map(([from, to]) => ring(radius, width, from, to)));
  // Ticks around a radius, every `major`-th longer, pointing at the center.
  const ticks = (count, radius, length, major) =>
    mergeGeometries(Array.from({ length: count }, (_, i) => {
      const long = i % major ? length : length * 2.2;
      return new THREE.PlaneGeometry(radius * 0.006, long).translate(0, radius - long / 2, 0).rotateZ((i * 2 * Math.PI) / count);
    }));
  // The frame: ticks with four inward chevrons, segmented arcs and a ring of dots.
  const chevrons = [0, 1, 2, 3].map((k) => new THREE.CircleGeometry(0.028, 3, -Math.PI / 2).translate(0, 1.345, 0).rotateZ(k * 90 * DEG));
  add("Echo_Ticks", mergeGeometries([ticks(120, 1.3, 0.04, 10), ...chevrons]), line, { spin: [0, 0, 0.04] });
  add("Echo_RingOuter", arcs(1.19, 0.026, [[8, 64], [80, 150], [166, 250], [262, 300], [312, 352]]), arc, { spin: [0, 0, -0.12] });
  add("Echo_Dots", mergeGeometries(Array.from({ length: 90 }, (_, i) => new THREE.CircleGeometry(i % 15 ? 0.005 : 0.011, 6).translate(0, 1.11, 0).rotateZ(i * 4 * DEG))),
    glow, { spin: [0, 0, -0.05] });
  // Neurons fill a shell around the nucleus; spokes feed the hubs from it.
  const net = network(random, 210, 0.09, () => {
    const p = new THREE.Vector3(random() * 2 - 1, random() * 2 - 1, random() * 2 - 1);
    return p.length() > 1 || p.length() < 0.42 ? null : p.multiplyScalar(0.86);
  });
  for (const h of net.hubs) {
    const root = net.neurons[h].clone().setLength(0.2);
    net.fibres.push(net.axon(root, net.neurons[h], net.moment[h] - root.distanceTo(net.neurons[h]) / net.speed, 0.005));
  }
  addNetwork(add, random, net, [0.3, 0.8, 0.95], { spin: [0, 0.07, 0] });
  // Motes drift just outside the network, each twinkling at its own moment.
  const motes = Array.from({ length: 80 }, () =>
    ball(new THREE.OctahedronGeometry(0.004 + random() * 0.006), new THREE.Vector3(random() - 0.5, random() - 0.5, random() - 0.5).setLength(0.92 + random() * 0.2), random()));
  add("Echo_Motes", mergeGeometries(motes), color("#c4f1ff", 0.7), { spin: [0, -0.05, 0], flow: [0.2, 0.08, 0.006] });
  // Three orbits, tilted and turned apart: open arcs that each carry a bead.
  for (const [name, radius, tilt, speed] of [["Echo_OrbitA", 0.98, [66, 0, 28], 0.45], ["Echo_OrbitB", 1.05, [70, 0, -40], -0.32], ["Echo_OrbitC", 0.93, [12, 0, 84], 0.26]]) {
    const bead = new THREE.SphereGeometry(0.026, 10, 6).translate(radius, 0, 0);
    add(name, mergeGeometries([new THREE.TorusGeometry(radius, 0.005, 4, 120, 300 * DEG), bead]), arc,
      { spin: [0, 0, speed] }, { tilt: new THREE.Quaternion().setFromEuler(new THREE.Euler(...tilt.map((d) => d * DEG), "ZXY")).toArray() });
  }
  // The nucleus is an eyeball: a bright core with its glow behind it (back
  // hemispheres, so nothing veils the eye) growing with the voice.
  const behind = (radius) => new THREE.SphereGeometry(radius, 32, 12, Math.PI, Math.PI);
  add("Echo_Core", new THREE.IcosahedronGeometry(0.19, 3), color("#d8f6ff", 1), { pulse: 0.1 });
  add("Echo_Glow", behind(0.32), color("#4cc0ff", 0.75), { pulse: 0.3 });
  add("Echo_Bloom", behind(0.56), color("#1e8fd0", 0.35), { pulse: 0.16 });
  // The eye turns about the nucleus's center toward the pointer. Its iris is
  // an opaque disc over the core, a shallow cone with the pupil set back. The
  // pupil widens as Echo speaks ("jawOpen", the voice morph) and the eye
  // blinks shut to a line. Gimbal rings turn around it.
  const eye = add("Echo_Eye", null, null, { gaze: [0.5, 0.4] });
  const front = 0.235, iris = 0.2, pupil = 0.34, wide = 0.46, rings = [0, 0.17, pupil, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.96, 1];
  const depth = (r) => front - 0.03 * (1 - r);
  const dilated = (r) => (r < pupil ? (r * wide) / pupil : wide + ((r - pupil) * (1 - wide)) / (1 - pupil));
  const disc = { position: [], uv: [], normal: [], index: [], jawOpen: [], blink: [] };
  const segments = 72;
  rings.forEach((r, k) => {
    for (let j = 0; j <= segments; j++) {
      const a = (j / segments) * 2 * Math.PI,
        x = Math.cos(a) * r * iris,
        y = Math.sin(a) * r * iris,
        w = dilated(r);
      disc.position.push(x, y, depth(r));
      disc.uv.push(0.5 + x / (2 * iris), 0.5 - y / (2 * iris));
      disc.normal.push(0, 0, 1);
      disc.jawOpen.push(Math.cos(a) * (w - r) * iris, Math.sin(a) * (w - r) * iris, depth(w) - depth(r));
      disc.blink.push(0, -0.95 * y, 0);
      if (k && j < segments) {
        const at = k * (segments + 1) + j,
          inner = at - (segments + 1);
        disc.index.push(inner, at, inner + 1, inner + 1, at, at + 1);
      }
    }
  });
  const discGeometry = new THREE.BufferGeometry();
  for (const name of ["position", "normal"]) discGeometry.setAttribute(name, new THREE.Float32BufferAttribute(disc[name], 3));
  discGeometry.setAttribute("uv", new THREE.Float32BufferAttribute(disc.uv, 2));
  discGeometry.setIndex(disc.index);
  add("Echo_Iris", discGeometry, [1, 1, 1, 1], {}, { parent: eye, texture: synapticIris(256), cutout: 0.5, targets: { jawOpen: disc.jawOpen, blink: disc.blink } });
  add("Echo_EyeTicks", mergeGeometries([ring(0.208, 0.004, 0, 360), ticks(60, 0.226, 0.008, 5)]).translate(0, 0, front), glow, { spin: [0, 0, 0.15] }, { parent: eye });
  const markers = [0, 120, 240].map((d) => new THREE.CircleGeometry(0.012, 3, Math.PI / 2).translate(0, 0.262, 0).rotateZ((d + 50) * DEG));
  add("Echo_EyeArcs", mergeGeometries([arcs(0.25, 0.007, [[0, 100], [120, 220], [240, 340]]), ...markers]).translate(0, 0, front), arc, { spin: [0, 0, -0.35] }, { parent: eye });
}
// The heads' halo: a sparser network in a shell behind and beside the head,
// turning slowly about the view axis so it never passes before the face.
function haloLook(add) {
  const random = seeded(0x4a10);
  const net = network(random, 120, 0.13, () => {
    const d = new THREE.Vector3(random() * 2 - 1, random() * 2 - 1, random() * 2 - 1);
    if (d.length() > 1 || d.length() < 0.1) return null;
    d.normalize();
    return d.z > 0.15 ? null : d.multiplyScalar(1.1 + random() * 0.25);
  });
  addNetwork(add, random, net, [0.22, 0.6, 0.85], { spin: [0, 0, 0.03] });
}
// A synaptic network: `count` neurons from `place` (a point, or null to draw
// again), no two closer than `spacing`. Each joins its three nearest
// neighbours and sprouts two short dendrites. The hubs, spread as far apart
// as they can be, start the cascades.
function network(random, count, spacing, place) {
  const neurons = [];
  for (let tries = 0; neurons.length < count && tries < 1e5; tries++) {
    const p = place();
    if (p && neurons.every((n) => n.distanceTo(p) >= spacing)) neurons.push(p);
  }
  const hubs = [0];
  while (hubs.length < 9) {
    const spread = (i) => Math.min(...hubs.map((h) => neurons[h].distanceTo(neurons[i])));
    hubs.push(neurons.reduce((best, _, i) => (spread(i) > spread(best) ? i : best), 0));
  }
  const links = new Map();
  neurons.forEach((p, i) =>
    neurons.map((q, j) => [p.distanceTo(q), j]).filter(([, j]) => j !== i).sort((a, b) => a[0] - b[0]).slice(0, 3)
      .forEach(([, j]) => links.set(`${Math.min(i, j)}:${Math.max(i, j)}`, [i, j])),
  );
  // A signal leaves each hub in turn and spreads at a fixed speed: a neuron
  // fires at the moment (a fraction of the cycle) the earliest signal reaches it.
  const speed = 1.5,
    moment = neurons.map(() => Infinity);
  hubs.forEach((h, k) => (moment[h] = k / hubs.length));
  for (const done = new Set(); done.size < neurons.length; ) {
    const i = moment.reduce((best, m, j) => (!done.has(j) && (best < 0 || m < moment[best]) ? j : best), -1);
    if (moment[i] === Infinity) throw Error("Every neuron must be reachable from a hub");
    done.add(i);
    for (const [a, b] of links.values()) {
      const j = a === i ? b : b === i ? a : -1;
      if (j >= 0) moment[j] = Math.min(moment[j], moment[i] + neurons[i].distanceTo(neurons[j]) / speed);
    }
  }
  // An axon carries light from `from` toward `to`, bowed aside like a nerve
  // fibre; each vertex's texture coordinate is when the light passes it.
  const axon = (from, to, start, radius) => {
    const length = from.distanceTo(to),
      bow = new THREE.Vector3(random() - 0.5, random() - 0.5, random() - 0.5).cross(to.clone().sub(from)).setLength(length * 0.14);
    const tube = new THREE.TubeGeometry(
      new THREE.QuadraticBezierCurve3(from, from.clone().lerp(to, 0.5).add(bow), to), Math.max(1, Math.round(length / 0.05)), radius, 3);
    const uv = tube.attributes.uv;
    for (let i = 0; i < uv.count; i++) uv.setXY(i, start + (uv.getX(i) * length) / speed, 0);
    return tube;
  };
  const fibres = [...links.values()].map(([a, b]) => {
    const [from, to] = moment[a] <= moment[b] ? [a, b] : [b, a];
    return axon(neurons[from], neurons[to], moment[from], 0.0034);
  });
  const tips = [];
  neurons.forEach((p, i) => {
    for (let k = 0; k < 2; k++) {
      const tip = new THREE.Vector3(random() - 0.5, random() - 0.5, random() - 0.5).setLength(0.04 + random() * 0.05).add(p);
      fibres.push(axon(p, tip, moment[i], 0.0022));
      tips.push({ at: tip, when: moment[i] + tip.distanceTo(p) / speed });
    }
  });
  return { neurons, hubs, moment, speed, axon, fibres, tips };
}
// A network's fibres, neurons (with the small bulbs at its dendrites' tips)
// and hubs as three nodes, each lighting as its signal passes; `alpha` gives
// their resting opacities.
function addNetwork(add, random, net, alpha, extras) {
  // Each tube's seam repeats its first vertex; texture v is unused, so they merge.
  add("Echo_Synapses", mergeVertices(mergeGeometries(net.fibres)), color("#2d9fd8", alpha[0]), { ...extras, flow: [0.13, 0.025, 0.005] });
  const neurons = net.neurons.flatMap((p, i) => (net.hubs.includes(i) ? [] : [ball(new THREE.IcosahedronGeometry(0.007 + random() ** 2 * 0.016), p, net.moment[i])]));
  const bulbs = net.tips.map(({ at, when }) => ball(new THREE.OctahedronGeometry(0.0055), at, when));
  add("Echo_Neurons", mergeGeometries([...neurons, ...bulbs]), color("#79d8ff", alpha[1]), { ...extras, flow: [0.13, 0.05, 0.008] });
  add("Echo_Hubs", mergeGeometries(net.hubs.map((h) => ball(new THREE.IcosahedronGeometry(0.03, 1), net.neurons[h], net.moment[h]))), color("#c4f1ff", alpha[2]),
    { ...extras, flow: [0.13, 0.07, 0.012] });
}
// A small smooth ball from a polyhedron, lit at moment `when` by "flow".
function ball(polyhedron, at, when) {
  const geometry = mergeVertices(polyhedron.deleteAttribute("normal").deleteAttribute("uv"));
  geometry.computeVertexNormals();
  geometry.setAttribute("uv", new THREE.Float32BufferAttribute(Array.from({ length: geometry.attributes.position.count }, () => [when, 0]).flat(), 2));
  return geometry.translate(at.x, at.y, at.z);
}
function color(hex, alpha) {
  return [...new THREE.Color(hex).toArray(), alpha];
}
// A small deterministic generator (mulberry32), so every build is identical.
function seeded(seed) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function core(avatar) {
  const { result, view, accessor, indexAccessor, glb } = gltf(avatar, "Imprint: Apache-2.0", { license: "Apache-2.0" });
  avatar.look(parts(result, view, accessor, indexAccessor));
  return glb();
}

// Adds generated parts, which are light: unlit, blended and double-sided.
// add(name, geometry, color, extras, options) returns the new node; without a
// geometry the node is an empty group. Options:
// - parent: the node to hang under (the root by default);
// - tilt: a quaternion for a tilted node in between, so the part's own spin
//   turns it within the tilted plane;
// - texture: PNG bytes for its color, mapped by its texture coordinates;
// - cutout: an alpha cutoff, making it an opaque cut-out that hides what is behind;
// - targets: morph targets by name, as position deltas.
function parts(result, view, accessor, indexAccessor) {
  return (name, geometry, color, extras = {}, { parent = 0, tilt = null, texture = null, cutout = null, targets = null } = {}) => {
    let at = result.nodes[parent];
    if (tilt) {
      at.children.push(result.nodes.length);
      at = result.nodes[result.nodes.push({ name: `${name}Tilt`, rotation: tilt, children: [] }) - 1];
    }
    const node = { name, ...(Object.keys(extras).length ? { extras } : {}) };
    if (!geometry) node.children = [];
    else {
      let baseColorTexture;
      if (texture) {
        result.samplers ??= [];
        const sampler = result.samplers.push({ magFilter: 9729, minFilter: 9987, wrapS: 33071, wrapT: 33071 }) - 1;
        const source = result.images.push({ bufferView: view(texture), mimeType: "image/png" }) - 1;
        baseColorTexture = { index: result.textures.push({ sampler, source }) - 1 };
      }
      const material = result.materials.push({
        name,
        pbrMetallicRoughness: { baseColorFactor: color, ...(texture ? { baseColorTexture } : {}), metallicFactor: 0, roughnessFactor: 1 },
        alphaMode: cutout ? "MASK" : "BLEND",
        ...(cutout ? { alphaCutoff: cutout } : {}),
        doubleSided: true,
        extensions: { KHR_materials_unlit: {} },
      }) - 1;
      node.mesh = result.meshes.push({
        name,
        primitives: [{
          attributes: {
            POSITION: accessor(Array.from(geometry.attributes.position.array)),
            NORMAL: accessor(Array.from(geometry.attributes.normal.array)),
            // Texture coordinates for a texture or for "flow" moments only.
            ...(texture || extras.flow ? { TEXCOORD_0: accessor(Array.from(geometry.attributes.uv.array), 2) } : {}),
          },
          ...(geometry.index ? { indices: indexAccessor(Array.from(geometry.index.array)) } : {}),
          material,
          ...(targets ? { targets: Object.values(targets).map((deltas) => ({ POSITION: accessor(deltas) })) } : {}),
        }],
        ...(targets ? { weights: Object.keys(targets).map(() => 0), extras: { targetNames: Object.keys(targets) } } : {}),
      }) - 1;
    }
    at.children.push(result.nodes.length);
    return result.nodes.push(node) - 1;
  };
}

// A pale, pupil-less iris sheet: lavender that deepens toward a thin ring,
// a soft upper-lid shadow and faint radial streaks. 512x256, eyes centered at
// the quarter points like the VRoid source sheet.
function paleIris() {
  const width = 512,
    height = 256,
    rows = [];
  for (let y = 0; y < height; y++) {
    const row = Buffer.alloc(1 + width * 3);
    for (let x = 0; x < width; x++) {
      const dx = ((x % 256) - 128) / 59,
        dy = (y - 128) / 67,
        r = Math.hypot(dx, dy);
      let rgb = [1, 1, 1];
      if (r <= 1) {
        const depth = Math.min(1, Math.max(0, (r - 0.3) / 0.7)) ** 2,
          lid = Math.max(0, -dy) ** 1.6 * 0.22,
          streak = 0.025 * Math.sin(Math.atan2(dy, dx) * 36) * r,
          ring = r > 0.9 ? (r - 0.9) / 0.1 : 0;
        rgb = [0.95, 0.93, 1].map((c, k) => {
          const deep = [0.72, 0.68, 0.9][k],
            edge = [0.5, 0.47, 0.72][k];
          const v = (c + (deep - c) * depth) * (1 - lid) + streak;
          return v + (edge - v) * ring * 0.85;
        });
      }
      rgb.forEach((v, k) => (row[1 + x * 3 + k] = Math.round(Math.min(1, Math.max(0, v)) * 255)));
    }
    rows.push(row);
  }
  return encodePng(width, height, 3, rows);
}
// Echo's iris, `size` pixels square, RGBA, transparent outside the disc: a
// dark pupil with a bright rim and a faint lens ring; radial fibres that wave
// and brighten toward a zigzag collarette; dark crypts; synapses (bright nodes
// trailing a streak toward the pupil); a dashed ring; a dark limbal band with
// a bright edge; and a catchlight at the upper left.
function synapticIris(size) {
  const random = seeded(0x1215);
  const lattice = 32,
    grid = Array.from({ length: lattice * lattice }, () => random());
  // Smooth value noise, repeating every `lattice` units.
  const noise = (x, y) => {
    const fx = Math.floor(x), fy = Math.floor(y), sx = (x - fx) ** 2 * (3 - 2 * (x - fx)), sy = (y - fy) ** 2 * (3 - 2 * (y - fy));
    const at = (i, j) => grid[(((fy + j) % lattice) + lattice) % lattice * lattice + ((((fx + i) % lattice) + lattice) % lattice)];
    return (at(0, 0) * (1 - sx) + at(1, 0) * sx) * (1 - sy) + (at(0, 1) * (1 - sx) + at(1, 1) * sx) * sy;
  };
  const gauss = (d, width) => Math.exp(-((d / width) ** 2));
  const ramp = (from, to, v) => Math.min(1, Math.max(0, (v - from) / (to - from)));
  const fibres = 128,
    strength = Array.from({ length: fibres }, () => 0.35 + 0.65 * random());
  const synapses = Array.from({ length: 26 }, () => {
    const a = random() * 2 * Math.PI, r = 0.62 + random() * 0.24;
    return { x: Math.cos(a) * r, y: Math.sin(a) * r, ux: -Math.cos(a), uy: -Math.sin(a) };
  });
  const rows = [];
  for (let py = 0; py < size; py++) {
    const row = Buffer.alloc(1 + size * 4);
    for (let px = 0; px < size; px++) {
      const x = ((px + 0.5) / size) * 2 - 1,
        y = 1 - ((py + 0.5) / size) * 2,
        r = Math.hypot(x, y),
        a = Math.atan2(y, x);
      let light = 0.05 + 0.22 * gauss(r - 0.17, 0.01);
      if (r >= 0.34) {
        const warp = a + 0.1 * (noise(x * 4 + 7, y * 4 + 7) - 0.5) * (1 + r),
          f = ((((warp / (2 * Math.PI)) * fibres) % fibres) + fibres) % fibres;
        const strand = strength[Math.floor(f)] * Math.sin(Math.PI * (f % 1)) ** 0.7;
        light = 0.2 + 0.44 * strand * (0.6 + 0.4 * noise(x * 14, y * 14)) + 0.16 * (1 - ramp(0.34, 0.56, r));
        light += 0.45 * gauss(r - (0.56 + 0.022 * Math.sin(a * 13 + 3 * noise(x * 3, y * 3))), 0.013);
        light *= 1 - 0.55 * ramp(0.66, 0.74, noise(x * 7 + 3, y * 7 + 11)) * ramp(0.58, 0.62, r) * (1 - ramp(0.86, 0.9, r));
        for (const s of synapses) {
          const dx = x - s.x, dy = y - s.y, along = dx * s.ux + dy * s.uy, across = Math.abs(dx * s.uy - dy * s.ux);
          light += 0.85 * gauss(Math.hypot(dx, dy), 0.02) + (along > 0 && along < 0.08 ? 0.45 * gauss(across, 0.006) * (1 - along / 0.08) : 0);
        }
        light += 0.5 * gauss(r - 0.9, 0.006) * (Math.sin(a * 40) > -0.3 ? 1 : 0.2);
        light = light * (1 - 0.65 * ramp(0.91, 0.94, r)) + 0.85 * gauss(r - 0.972, 0.007);
      }
      light += 0.9 * gauss(r - 0.352, 0.012);
      light = Math.max(light, 0.95 * (1 - ramp(0.07, 0.13, Math.hypot(x + 0.36, (y - 0.4) / 0.8))), 0.8 * (1 - ramp(0.02, 0.04, Math.hypot(x - 0.3, y + 0.34))));
      [0.8, 0.96, 1].forEach((tint, k) => (row[1 + px * 4 + k] = Math.round(Math.min(1, light * tint) * 255)));
      row[1 + px * 4 + 3] = Math.round(ramp(0.995, 0.983, r) * 255);
    }
    rows.push(row);
  }
  return encodePng(size, size, 4, rows);
}
// 8-bit RGB (3) or RGBA (4) rows, each led by its filter byte.
function encodePng(width, height, channels, rows) {
  const chunk = (type, body) => {
    const out = Buffer.alloc(12 + body.length);
    out.writeUInt32BE(body.length);
    out.write(type, 4);
    body.copy(out, 8);
    out.writeUInt32BE(crc32(out.subarray(4, 8 + body.length)), 8 + body.length);
    return out;
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width);
  header.writeUInt32BE(height, 4);
  header.set([8, channels === 4 ? 6 : 2, 0, 0, 0], 8);
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header),
    chunk("IDAT", zlib.deflateSync(Buffer.concat(rows), { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}
// The VRoid sources' textures: 8-bit RGB/RGBA, not interlaced.
function decodePng(png) {
  let offset = 8, width = 0, height = 0, channels = 0;
  const data = [];
  while (offset < png.length) {
    const length = png.readUInt32BE(offset),
      type = png.toString("latin1", offset + 4, offset + 8),
      body = png.subarray(offset + 8, offset + 8 + length);
    if (type === "IHDR") {
      width = body.readUInt32BE(0);
      height = body.readUInt32BE(4);
      if (body[8] !== 8 || body[12] !== 0 || ![2, 6].includes(body[9])) throw Error("Unsupported PNG texture");
      channels = body[9] === 6 ? 4 : 3;
    } else if (type === "IDAT") data.push(body);
    offset += 12 + length;
  }
  const raw = zlib.inflateSync(Buffer.concat(data)),
    stride = width * channels,
    pixels = Buffer.alloc(height * stride);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)],
      line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let x = 0; x < stride; x++) {
      const a = x >= channels ? pixels[y * stride + x - channels] : 0,
        b = y > 0 ? pixels[(y - 1) * stride + x] : 0,
        c = x >= channels && y > 0 ? pixels[(y - 1) * stride + x - channels] : 0;
      const p = a + b - c,
        predictor = [0, a, b, (a + b) >> 1, Math.abs(p - a) <= Math.abs(p - b) && Math.abs(p - a) <= Math.abs(p - c) ? a : Math.abs(p - b) <= Math.abs(p - c) ? b : c][filter];
      pixels[y * stride + x] = (line[x] + predictor) & 255;
    }
  }
  return { width, height, channels, pixels };
}
// Map each texel's brightness across the texture's own range onto dark..light,
// so every strand keeps its shading in the new color. Alpha is kept.
function recolor(png, dark, light) {
  const { width, height, channels, pixels } = decodePng(png);
  const lum = (i) => 0.2126 * pixels[i] + 0.7152 * pixels[i + 1] + 0.0722 * pixels[i + 2];
  let lo = 255, hi = 0;
  for (let i = 0; i < pixels.length; i += channels) {
    if (channels === 4 && pixels[i + 3] < 16) continue;
    lo = Math.min(lo, lum(i));
    hi = Math.max(hi, lum(i));
  }
  const rows = [];
  for (let y = 0; y < height; y++) {
    const row = Buffer.alloc(1 + width * channels);
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * channels,
        t = Math.min(1, Math.max(0, (lum(i) - lo) / Math.max(1, hi - lo))) ** 0.8;
      for (let k = 0; k < 3; k++) row[1 + x * channels + k] = Math.round((dark[k] + (light[k] - dark[k]) * t) * 255);
      if (channels === 4) row[1 + x * channels + 3] = pixels[i + 3];
    }
    rows.push(row);
  }
  return encodePng(width, height, channels, rows);
}
function crc32(bytes) {
  let crc = ~0;
  for (const byte of bytes) {
    crc ^= byte;
    for (let k = 0; k < 8; k++) crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
  }
  return ~crc >>> 0;
}
