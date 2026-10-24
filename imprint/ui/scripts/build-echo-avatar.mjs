// Echo artwork: deterministic point-cloud sculpture derived from MediaPipe's
// Apache-2.0 canonical facial topology. See avatar-source/NOTICE.md.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import * as THREE from 'three';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const output = path.resolve(here, '../public/models');
const source = await fs.readFile(path.join(here, 'avatar-source/canonical_face_model.obj'), 'utf8');
const vertices = [], faces = [];
for (const line of source.split('\n')) {
  const parts = line.trim().split(/\s+/);
  if (parts[0] === 'v') vertices.push(new THREE.Vector3(+parts[1] / 8.5, +parts[2] / 8.5 + .08, +parts[3] / 8.5 - .32));
  if (parts[0] === 'f') faces.push(parts.slice(1).map(p => +p.split('/')[0] - 1));
}
let seed = 20261024;
function random() { seed = (1664525 * seed + 1013904223) >>> 0; return seed / 4294967296; }
const group = new THREE.Group();
group.name = 'Imprint_Echo';
group.userData = { artwork: 'Echo / Imprint', version: 1, license: 'Apache-2.0 (facial template); see NOTICE.md',
  morphs: 'jawOpen, blink', units: 'metres (artistic scale)', front: '+Z' };

function morph(p, kind) {
  const q = p.clone();
  if (kind === 'jawOpen' && p.z > .06 && p.y < -.28) {
    const lip = Math.exp(-((p.x / .42) ** 2));
    const lower = THREE.MathUtils.smoothstep(-p.y, .40, .70);
    q.y -= .14 * lower * lip;
    q.z -= .045 * lower;
  }
  if (kind === 'blink' && p.z > .07) {
    const eye = Math.exp(-(((Math.abs(p.x) - .37) / .23) ** 4) - (((p.y - .36) / .13) ** 4));
    q.y += (.36 - p.y) * .94 * eye;
  }
  return q;
}
function geometry(points, colors) {
  const g = new THREE.BufferGeometry().setFromPoints(points);
  if (colors) g.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  g.morphAttributes.position = ['jawOpen', 'blink'].map(name => {
    const attribute = new THREE.Float32BufferAttribute(points.flatMap(p => morph(p, name).toArray()), 3);
    attribute.name = name;
    return attribute;
  });
  return g;
}
const positions = [], colors = [];
function add(p, brightness = 1) {
  positions.push(p);
  const light = brightness * (.45 + .55 * random());
  colors.push(.46 * light, .79 * light, light);
}
// Allocate samples by triangle area, preserving the anatomy around lids/lips.
const areas = faces.map(f => new THREE.Triangle(...f.map(i => vertices[i])).getArea());
const total = areas.reduce((a, b) => a + b, 0);
faces.forEach((face, index) => {
  const count = Math.ceil(21000 * areas[index] / total);
  const [a, b, c] = face.map(i => vertices[i]);
  for (let i = 0; i < count; i++) {
    const u = Math.sqrt(random()), v = random();
    add(a.clone().multiplyScalar(1 - u).addScaledVector(b, u * (1 - v)).addScaledVector(c, u * v));
  }
});
const face = new THREE.Points(geometry(positions, colors), new THREE.PointsMaterial({
  size: .009, vertexColors: true, transparent: true, opacity: .88, depthWrite: false, sizeAttenuation: true,
}));
face.name = 'Echo_Face';
group.add(face);
// An almost invisible anatomical shell provides an optional depth mask in the
// live renderer, keeping the far cheek from washing out the eyes and nose.
const shellGeometry = geometry(vertices.map(p => p.clone().add(new THREE.Vector3(0,0,-.004))));
shellGeometry.setIndex(faces.flat());
const shell = new THREE.Mesh(shellGeometry, new THREE.MeshBasicMaterial({ color: '#08121a', transparent: true, opacity: .04, side: THREE.DoubleSide }));
shell.name = 'Echo_Depth';
shell.renderOrder = -1;
group.add(shell);

const surround = [], surroundColors = [];
for (let i = 0; i < 9000; i++) {
  const y = random() * 2 - 1, a = random() * Math.PI * 2;
  const r = Math.sqrt(1 - y * y);
  const p = new THREE.Vector3(.84 * r * Math.cos(a), 1.10 * y + .14, .69 * r * Math.sin(a) - .20);
  if (p.z > -.06 && p.y < .97) continue;
  surround.push(p);
  const light = .12 + random() * .26;
  surroundColors.push(.35 * light, .68 * light, light);
}
for (let i = 0; i < 1700; i++) {
  const h = random(), a = random() * Math.PI * 2;
  const r = .27 + .12 * h;
  surround.push(new THREE.Vector3(r * Math.cos(a), -.83 - h * .65, r * Math.sin(a) - .18));
  const light = (1 - h) * .40;
  surroundColors.push(.5 * light, .8 * light, light);
}
for (let i = 0; i < 600; i++) {
  const a = random() * Math.PI * 2, r = .95 + random() * .55;
  surround.push(new THREE.Vector3(Math.cos(a) * r, random() * 2.8 - 1.4, Math.sin(a) * r * .55 - .22));
  const light = random() * .3;
  surroundColors.push(.4 * light, .8 * light, light);
}
const halo = new THREE.Points(geometry(surround, surroundColors), new THREE.PointsMaterial({
  size: .011, vertexColors: true, transparent: true, opacity: .65, depthWrite: false,
}));
halo.name = 'Echo_Dissolve';
group.add(halo);

const loops = [
  [33,160,158,133,153,144,33], [263,387,385,362,380,373,263],
  [61,40,37,0,267,270,291,321,314,17,84,91,61],
  [61,78,81,13,311,308,291], [168,6,197,195,5,4,1,19],
];
for (let index = 0; index < loops.length; index++) {
  const points = new THREE.CatmullRomCurve3(loops[index].map(i => vertices[i].clone().add(new THREE.Vector3(0,0,.003)))).getPoints(96);
  const line = new THREE.Line(geometry(points), new THREE.LineBasicMaterial({ color: '#9cddf5', transparent: true, opacity: index === 4 ? .16 : .42, depthWrite: false }));
  line.name = `Echo_Contour_${index}`;
  group.add(line);
}

// GLTFExporter uses FileReader in browsers. No raster textures are involved.
globalThis.FileReader = class {
  readAsArrayBuffer(blob) { blob.arrayBuffer().then(value => { this.result = value; this.onloadend?.(); }); }
};
await fs.mkdir(output, { recursive: true });
const binary = await new GLTFExporter().parseAsync(group, { binary: true, onlyVisible: true });
await fs.writeFile(path.join(output, 'imprint-echo.glb'), Buffer.from(binary));
console.log(`Echo: ${positions.length + surround.length} particles, ${binary.byteLength} bytes`);
