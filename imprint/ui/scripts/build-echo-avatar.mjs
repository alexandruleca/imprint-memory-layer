// Rebuild the bundled anime head. No network or model download occurs at runtime.
// Sendagaya Shino is CC0, explicitly confirmed by pixiv's original release page.
// The optional local argument makes rebuilding independent of the mirror.
import fs from "node:fs/promises";
import crypto from "node:crypto";
import path from "node:path";
import { fileURLToPath } from "node:url";
import * as THREE from "three";
const here = path.dirname(fileURLToPath(import.meta.url));
const revision = "e16eb187100149a315ad92c3c9968f1d5baa6c7d";
const url = `https://raw.githubusercontent.com/madjin/vrm-samples/${revision}/vroid/beta/Sendagaya_Shino.vrm`;
const data = process.argv[2]
  ? await fs.readFile(process.argv[2])
  : Buffer.from(await (await fetch(url)).arrayBuffer());
if (
  crypto.createHash("sha256").update(data).digest("hex") !==
  "1e177c1a7b14f783a9c48395831db8616260d3bddd4154cb2784b779adca49b5"
)
  throw Error("Source model checksum mismatch");
const jsonLength = data.readUInt32LE(12),
  source = JSON.parse(data.subarray(20, 20 + jsonLength));
const binary = data.subarray(28 + jsonLength);
if (source.extensions.VRM.meta.licenseName !== "CC0")
  throw Error("Expected the CC0 beta model");
const result = {
  asset: { version: "2.0", generator: "Imprint Echo anime-head pipeline v3" },
  extensionsUsed: ["KHR_materials_unlit"],
  scene: 0,
  scenes: [{ nodes: [0] }],
  nodes: [
    {
      name: "Imprint_Echo",
      children: [],
      extras: {
        version: 3,
        artwork: "Echo anime hologram",
        source: "Sendagaya Shino / pixiv / CC0",
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
  let ti = imageMap.get(t.source);
  if (ti === undefined) {
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
        baseColorFactor:
          id === 15
            ? [0.23, 0.16, 0.13, 1]
            : id === 2 || id === 3 || id === 4
              ? [0.14, 0.12, 0.2, 1]
              : [0.87, 0.96, 1, 1],
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
// Shorten the long rear locks into a shoulder-free bob; retain the modeled bangs.
function position(values, hair = false, delta = false) {
  return values.map((v, i) => {
    if (i % 3 === 0 || i % 3 === 2) return -v * scale;
    if (delta) return v * scale;
    return ((hair && v < 1.4 ? 1.4 + (v - 1.4) * 0.17 : v) - 1.5) * scale;
  });
}
for (const mi of [0, 2]) {
  const s = source.meshes[mi],
    primitives = [];
  for (const p of s.primitives) {
    const attributes = {
      POSITION: attribute(p.attributes.POSITION, "position", mi === 2),
      NORMAL: attribute(p.attributes.NORMAL, "normal"),
      TEXCOORD_0: attribute(p.attributes.TEXCOORD_0, "uv"),
    };
    const targets =
      mi === 0
        ? [29, 12].map((index) => ({
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
// Fine luminous headband and one asymmetric clasp give Echo her own silhouette.
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
const curve = new THREE.CatmullRomCurve3(
  Array.from({ length: 31 }, (_, i) => {
    const a = (Math.PI * i) / 30;
    return new THREE.Vector3(
      -0.91 * Math.cos(a),
      0.16 + 0.98 * Math.sin(a),
      -0.08,
    );
  }),
);
addGeometry(
  "Echo_Headband",
  new THREE.TubeGeometry(curve, 64, 0.022, 6, false),
  [0.42, 0.85, 1, 1],
);
const clasp = new THREE.OctahedronGeometry(0.085, 0);
clasp.translate(0.92, 0.6, 0.26);
addGeometry("Echo_Clasp", clasp, [1, 0.76, 0.35, 1]);
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
const dest = path.resolve(here, "../public/models/imprint-echo.glb");
await fs.writeFile(dest, Buffer.concat([head, padded, bh, body]));
console.log(
  `${dest}: ${body.length} bytes, modeled anime face with jaw/blink morphs`,
);
