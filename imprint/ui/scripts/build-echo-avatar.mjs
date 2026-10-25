// Rebuild the bundled anime heads. No network or model download occurs at runtime.
// Every source is a CC0 VRoid beta sample, explicitly confirmed by pixiv's
// original release page; the build refuses any other license.
//   node scripts/build-echo-avatar.mjs [echo|iris|kai] [local.vrm]
// Without an avatar id it rebuilds all of them from the pinned mirror. The
// optional local file makes rebuilding independent of the mirror.
import fs from "node:fs/promises";
import crypto from "node:crypto";
import zlib from "node:zlib";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as THREE from "three";
const here = path.dirname(fileURLToPath(import.meta.url));
const revision = "e16eb187100149a315ad92c3c9968f1d5baa6c7d";
const SKIN = [0.87, 0.96, 1, 1],
  LINES = [0.14, 0.12, 0.2, 1];
// Each avatar is a head cut from one sample and given its own palette.
// Shared mesh names (Echo_Face, Echo_Hair) are the roles the renderer animates.
const AVATARS = {
  echo: {
    file: "Sendagaya_Shino.vrm",
    sha256: "1e177c1a7b14f783a9c48395831db8616260d3bddd4154cb2784b779adca49b5",
    title: "Sendagaya Shino",
    node: "Imprint_Echo",
    version: 4,
    artwork: "Echo anime hologram",
    generator: "Imprint Echo anime-head pipeline v3",
    // Head center (m) in the source; hair below hairBelow is folded up by
    // hairFold, here into a shoulder-free bob.
    center: 1.5,
    hairBelow: 1.4,
    hairFold: 0.17,
    hairTuck: 0,
    colors: [[/_Hair_00_HAIR_01$/, [0.23, 0.16, 0.13, 1]], [/_Face(Eyeline|Eyelash|Brow)_/, LINES]],
    // The source's crossed hair clip; Echo wears ribbons instead.
    drop: /_Hair_00_HAIR_02$/,
    dropHair: [],
    iris: null,
    recolor: [],
    expression: null,
    accessories: echoAccessories,
  },
  iris: {
    file: "Victoria_Rubin.vrm",
    sha256: "b1372131bdbf233f46320146d565f342a7e4f6f4b8f2aefb301f1a283ec07e1e",
    title: "Victoria Rubin",
    node: "Imprint_Iris",
    version: 2,
    artwork: "Iris anime hologram",
    generator: "Imprint Echo anime-head pipeline v3",
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
    accessories: () => {},
  },
  kai: {
    file: "HairSample_Male.vrm",
    sha256: "7aeca142dabbc26ba0f5b9c998c8f3cb6df341287d67788427ac837223785a3f",
    title: "HairSample_Male",
    node: "Imprint_Kai",
    version: 1,
    artwork: "Kai anime hologram",
    generator: "Imprint Echo anime-head pipeline v3",
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
    accessories: () => {},
  },
};
const wanted = process.argv[2] ? [process.argv[2]] : Object.keys(AVATARS);
for (const id of wanted) {
  const avatar = AVATARS[id];
  if (!avatar) throw Error(`Unknown avatar ${id}; expected ${Object.keys(AVATARS).join(", ")}`);
  const url = `https://raw.githubusercontent.com/madjin/vrm-samples/${revision}/vroid/beta/${avatar.file}`;
  const data = process.argv[3]
    ? await fs.readFile(process.argv[3])
    : Buffer.from(await (await fetch(url)).arrayBuffer());
  await build(id, avatar, data);
}

async function build(id, avatar, data) {
if (crypto.createHash("sha256").update(data).digest("hex") !== avatar.sha256)
  throw Error("Source model checksum mismatch");
const jsonLength = data.readUInt32LE(12),
  source = JSON.parse(data.subarray(20, 20 + jsonLength));
const binary = data.subarray(28 + jsonLength);
if (source.extensions.VRM.meta.licenseName !== "CC0")
  throw Error("Expected a CC0 beta model");
const result = {
  asset: { version: "2.0", generator: avatar.generator, copyright: `pixiv ${avatar.title}: CC0-1.0; Imprint adaptations: Apache-2.0` },
  extensionsUsed: ["KHR_materials_unlit"],
  scene: 0,
  scenes: [{ nodes: [0] }],
  nodes: [
    {
      name: avatar.node,
      children: [],
      extras: {
        version: avatar.version,
        artwork: avatar.artwork,
        source: `${avatar.title} / pixiv / CC0`,
        license: "CC0-1.0 source; Apache-2.0 adaptations",
        morphs: "jawOpen, blink",
      },
    },
  ],
  meshes: [],
  materials: [],
  textures: [],
  images: [],
  samplers: source.samplers,
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
    type === 5126 ? new Float32Array(values) : new Uint32Array(values);
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
      extensions: { KHR_materials_unlit: {} },
    }) - 1;
  materialMap.set(id, i);
  return i;
}
const transformed = new Map();
function attribute(id, kind, hair = false) {
  const key = `${id}:${kind}:${hair}`;
  if (transformed.has(key)) return transformed.get(key);
  const values = read(id);
  const out = accessor(
    kind === "uv"
      ? values
      : kind === "normal"
        ? values.map((v, i) => (i % 3 === 1 ? v : -v))
        : position(values, hair, kind === "delta"),
    kind === "uv" ? 2 : 3,
  );
  transformed.set(key, out);
  return out;
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
for (const mi of [0, 2]) {
  const s = source.meshes[mi],
    primitives = [];
  for (const [index, p] of s.primitives.entries()) {
    if (avatar.drop?.test(source.materials[p.material].name)) continue;
    if (mi === 2 && avatar.dropHair.includes(index)) continue;
    const attributes = {
      POSITION: mi === 0 && baked ? accessor(position(bake(read(p.attributes.POSITION), read(p.targets[baked.target].POSITION))))
        : attribute(p.attributes.POSITION, "position", mi === 2),
      NORMAL: attribute(p.attributes.NORMAL, "normal"),
      TEXCOORD_0: attribute(p.attributes.TEXCOORD_0, "uv"),
    };
    const targets =
      mi === 0
        ? morphs.map((index) => ({
            POSITION: attribute(p.targets[index].POSITION, "delta"),
          }))
        : undefined;
    primitives.push({
      attributes,
      indices: accessor(read(p.indices), 1, 5125),
      material: material(p.material),
      ...(targets ? { targets } : {}),
    });
  }
  const mesh =
    result.meshes.push({
      name: mi === 0 ? "Echo_Face" : "Echo_Hair",
      primitives,
      ...(mi === 0
        ? { weights: [0, 0], extras: { targetNames: ["jawOpen", "blink"] } }
        : {}),
    }) - 1;
  result.nodes[0].children.push(result.nodes.length);
  result.nodes.push({ name: mi === 0 ? "Echo_Face" : "Echo_Hair", mesh });
}
function addGeometry(name, g, color) {
  const mesh = result.meshes.length;
  const mat =
    result.materials.push({
      name,
      pbrMetallicRoughness: {
        baseColorFactor: color,
        metallicFactor: 0,
        roughnessFactor: 1,
      },
      extensions: { KHR_materials_unlit: {} },
    }) - 1;
  result.meshes.push({
    name,
    primitives: [
      {
        attributes: {
          POSITION: accessor(Array.from(g.attributes.position.array)),
          NORMAL: accessor(Array.from(g.attributes.normal.array)),
        },
        ...(g.index
          ? { indices: accessor(Array.from(g.index.array), 1, 5125) }
          : {}),
        material: mat,
      },
    ],
  });
  result.nodes[0].children.push(result.nodes.length);
  result.nodes.push({ name, mesh });
}
avatar.accessories(addGeometry);
result.buffers = [{ byteLength: offset }];
const body = Buffer.concat(chunks);
const json = Buffer.from(JSON.stringify(result));
const padded = Buffer.alloc((json.length + 3) & ~3, 32);
json.copy(padded);
const head = Buffer.alloc(20);
head.write("glTF");
head.writeUInt32LE(2, 4);
head.writeUInt32LE(28 + padded.length + body.length, 8);
head.writeUInt32LE(padded.length, 12);
head.write("JSON", 16);
const bh = Buffer.alloc(8);
bh.writeUInt32LE(body.length);
bh.write("BIN\0", 4);
const dest = path.resolve(here, `../public/models/imprint-${id}.glb`);
await fs.writeFile(dest, Buffer.concat([head, padded, bh, body]));
console.log(
  `${dest}: ${body.length} bytes, modeled anime face with jaw/blink morphs`,
);
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

// A warm hairband hugging the hair, tied off with a ribbon bow above each ear.
function echoAccessories(addGeometry) {
  const rx = 0.97,
    ry = 0.99,
    lift = 0.16,
    depth = -0.06,
    end = 0.5; // the band stops at the bows, above the ears
  const curve = new THREE.CatmullRomCurve3(
    Array.from({ length: 31 }, (_, i) => {
      const a = end + ((Math.PI - 2 * end) * i) / 30;
      return new THREE.Vector3(-rx * Math.cos(a), lift + ry * Math.sin(a), depth);
    }),
  );
  const band = [1, 0.8, 0.22, 1];
  addGeometry("Echo_Headband", new THREE.TubeGeometry(curve, 72, 0.03, 8, false), band);
  for (const [side, name] of [[-1, "Right"], [1, "Left"]]) {
    const a = side < 0 ? end : Math.PI - end,
      out = side < 0 ? -1 : 1,
      at = new THREE.Vector3(-rx * Math.cos(a) + out * 0.05, lift + ry * Math.sin(a), depth);
    const place = (g) => {
      g.rotateZ(out * 0.4);
      g.translate(at.x, at.y, at.z);
      return g;
    };
    // Two soft fabric wings swept forward and back, a knot, two tails.
    for (const [z, sweep] of [[1, 0.42], [-1, -0.36]]) {
      const wing = new THREE.SphereGeometry(1, 20, 14);
      wing.scale(0.03, 0.085, 0.17);
      wing.translate(0, 0, 0.14);
      wing.rotateX(-sweep);
      if (z < 0) wing.rotateY(Math.PI);
      addGeometry(`Echo_Ribbon${name}Wing${z > 0 ? "Front" : "Back"}`, place(wing), band);
    }
    const knot = new THREE.SphereGeometry(0.055, 16, 12);
    knot.scale(0.8, 1, 1.1);
    addGeometry(`Echo_Ribbon${name}Knot`, place(knot), band);
    for (const [z, swing] of [[0.04, 0.45], [-0.05, -0.35]]) {
      const tail = new THREE.BoxGeometry(0.016, 0.24, 0.07);
      tail.translate(0, -0.13, 0);
      tail.rotateX(swing);
      tail.translate(0, -0.02, z);
      addGeometry(`Echo_Ribbon${name}Tail${z > 0 ? "Front" : "Back"}`, place(tail), band);
    }
  }
}
