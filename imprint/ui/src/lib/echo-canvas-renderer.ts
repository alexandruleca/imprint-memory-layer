import * as THREE from "three";

type Surface = { mesh: THREE.Mesh; indices: number[]; colors: string[]; alpha: number[]; texture?: HTMLCanvasElement; uv?: THREE.BufferAttribute | THREE.InterleavedBufferAttribute };
type Triangle = { x: number[]; y: number[]; z: number; color: string; alpha: number; wire: boolean; texture?: HTMLCanvasElement; uv?: number[] };

/** CPU projection of the bundled textured head for desktops without WebGL2.
 * The same geometry and jaw/blink morphs remain active; no remote/static avatar. */
export class EchoCanvasRenderer {
  readonly domElement = document.createElement("canvas");
  private readonly context: CanvasRenderingContext2D;
  private ratio = 1;
  private readonly projection = new THREE.Matrix4();
  private readonly matrix = new THREE.Matrix4();
  private readonly point = new THREE.Vector3();
  private readonly surfaces = new WeakMap<THREE.Mesh, Surface>();
  constructor() {
    const context = this.domElement.getContext("2d", { alpha: true });
    if (!context) throw new Error("Canvas rendering is unavailable");
    this.context = context;
  }
  setPixelRatio(ratio: number) { this.ratio = ratio; }
  setSize(width: number, height: number) {
    this.domElement.width = Math.max(1, Math.round(width * this.ratio));
    this.domElement.height = Math.max(1, Math.round(height * this.ratio));
    this.domElement.style.width = `${width}px`;
    this.domElement.style.height = `${height}px`;
  }
  dispose() { this.context.clearRect(0, 0, this.domElement.width, this.domElement.height); }
  private surface(mesh: THREE.Mesh): Surface {
    const cached = this.surfaces.get(mesh);
    if (cached) return cached;
    const geometry = mesh.geometry, material = mesh.material as THREE.MeshBasicMaterial;
    const uv = geometry.getAttribute("uv");
    const indices = geometry.index ? Array.from(geometry.index.array) : Array.from({ length: geometry.getAttribute("position").count }, (_, i) => i);
    const colors: string[] = [], alpha: number[] = [];
    let pixels: ImageData | undefined;
    let texture: HTMLCanvasElement | undefined;
    if (material.map?.image && uv) {
      const image = material.map.image as HTMLImageElement;
      const canvas = document.createElement("canvas");
      canvas.width = Math.min(512, image.width); canvas.height = Math.min(512, image.height);
      const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height); pixels = ctx.getImageData(0, 0, canvas.width, canvas.height);
    }
    const tint = material.color.clone().convertLinearToSRGB();
    for (let i = 0; i < indices.length; i += 3) {
      let r = 255, g = 255, b = 255, a = 1;
      if (pixels && uv) {
        const u = (uv.getX(indices[i]) + uv.getX(indices[i + 1]) + uv.getX(indices[i + 2])) / 3;
        const v = (uv.getY(indices[i]) + uv.getY(indices[i + 1]) + uv.getY(indices[i + 2])) / 3;
        const x = Math.max(0, Math.min(pixels.width - 1, Math.floor(u * pixels.width)));
        const y = Math.max(0, Math.min(pixels.height - 1, Math.floor(v * pixels.height)));
        const offset = (y * pixels.width + x) * 4;
        [r, g, b] = Array.from(pixels.data.slice(offset, offset + 3)); a = pixels.data[offset + 3] / 255;
      }
      r *= tint.r; g *= tint.g; b *= tint.b;
      const l = (.2126 * r + .7152 * g + .0722 * b) / 255;
      colors.push(`rgb(${Math.round(r * .38 + 51 * (.16 + l * .95) * .62)},${Math.round(g * .38 + 176 * (.16 + l * .95) * .62)},${Math.round(b * .38 + 245 * (.16 + l * .95) * .62)})`);
      alpha.push(pixels ? material.opacity : material.alphaTest && a < material.alphaTest ? 0 : a * material.opacity);
    }
    if (pixels) {
      texture = document.createElement("canvas"); texture.width = pixels.width; texture.height = pixels.height;
      const data = pixels.data;
      for (let i = 0; i < data.length; i += 4) {
        const r = data[i] * tint.r, g = data[i + 1] * tint.g, b = data[i + 2] * tint.b;
        const l = (.2126 * r + .7152 * g + .0722 * b) / 255;
        data[i] = r * .38 + 51 * (.16 + l * .95) * .62;
        data[i + 1] = g * .38 + 176 * (.16 + l * .95) * .62;
        data[i + 2] = b * .38 + 245 * (.16 + l * .95) * .62;
      }
      texture.getContext("2d")!.putImageData(pixels, 0, 0);
    }
    const surface = { mesh, indices, colors, alpha, texture, uv };
    this.surfaces.set(mesh, surface); return surface;
  }
  render(scene: THREE.Scene, camera: THREE.PerspectiveCamera) {
    const ctx = this.context, width = this.domElement.width, height = this.domElement.height;
    ctx.clearRect(0, 0, width, height);
    scene.updateMatrixWorld(); camera.updateMatrixWorld();
    this.projection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    const triangles: Triangle[] = [];
    scene.traverse(object => {
      if (!(object instanceof THREE.Mesh) || !object.visible) return;
      const { indices, colors, alpha, texture, uv } = this.surface(object);
      const geometry = object.geometry, positions = geometry.getAttribute("position");
      const morphs = geometry.morphAttributes.position || [], influences = object.morphTargetInfluences || [];
      this.matrix.multiplyMatrices(this.projection, object.matrixWorld);
      const projected = new Float32Array(positions.count * 3);
      for (let i = 0; i < positions.count; i++) {
        this.point.fromBufferAttribute(positions, i);
        for (let m = 0; m < morphs.length; m++) {
          const influence = influences[m] || 0;
          if (!influence) continue;
          this.point.x += (morphs[m].getX(i) - (geometry.morphTargetsRelative ? 0 : positions.getX(i))) * influence;
          this.point.y += (morphs[m].getY(i) - (geometry.morphTargetsRelative ? 0 : positions.getY(i))) * influence;
          this.point.z += (morphs[m].getZ(i) - (geometry.morphTargetsRelative ? 0 : positions.getZ(i))) * influence;
        }
        this.point.applyMatrix4(this.matrix);
        projected[i * 3] = (this.point.x * .5 + .5) * width;
        projected[i * 3 + 1] = (-this.point.y * .5 + .5) * height;
        projected[i * 3 + 2] = this.point.z;
      }
      const material = object.material as THREE.MeshBasicMaterial;
      for (let i = 0; i < indices.length; i += 3) {
        if (alpha[i / 3] < .05) continue;
        const x = indices.slice(i, i + 3).map(j => projected[j * 3]);
        const y = indices.slice(i, i + 3).map(j => projected[j * 3 + 1]);
        const z = indices.slice(i, i + 3).reduce((sum, j) => sum + projected[j * 3 + 2], 0) / 3;
        if (z < -1 || z > 1 || (material.side !== THREE.DoubleSide && (x[1] - x[0]) * (y[2] - y[0]) - (y[1] - y[0]) * (x[2] - x[0]) >= 0)) continue;
        triangles.push({ x, y, z, color: colors[i / 3], alpha: alpha[i / 3], wire: /SKIN|HAIR_01/.test(material.name), texture, uv: uv && texture ? indices.slice(i, i + 3).flatMap(j => [uv.getX(j) * texture.width, uv.getY(j) * texture.height]) : undefined });
      }
    });
    triangles.sort((a, b) => b.z - a.z);
    ctx.lineWidth = .5 * this.ratio;
    for (const t of triangles) {
      ctx.beginPath(); ctx.moveTo(t.x[0], t.y[0]); ctx.lineTo(t.x[1], t.y[1]); ctx.lineTo(t.x[2], t.y[2]); ctx.closePath();
      ctx.globalAlpha = t.alpha;
      if (!t.texture || t.wire) {
        ctx.fillStyle = t.color; ctx.fill();
        ctx.strokeStyle = t.color; ctx.stroke(); // cover subpixel seams on opaque surfaces
      }
      if (t.texture && t.uv) {
        const [u0, v0, u1, v1, u2, v2] = t.uv;
        const determinant = (u1 - u0) * (v2 - v0) - (u2 - u0) * (v1 - v0);
        if (Math.abs(determinant) > 1e-5) {
          const a = ((t.x[1] - t.x[0]) * (v2 - v0) - (t.x[2] - t.x[0]) * (v1 - v0)) / determinant;
          const c = ((t.x[2] - t.x[0]) * (u1 - u0) - (t.x[1] - t.x[0]) * (u2 - u0)) / determinant;
          const b = ((t.y[1] - t.y[0]) * (v2 - v0) - (t.y[2] - t.y[0]) * (v1 - v0)) / determinant;
          const d = ((t.y[2] - t.y[0]) * (u1 - u0) - (t.y[1] - t.y[0]) * (u2 - u0)) / determinant;
          ctx.save(); ctx.clip(); ctx.setTransform(a, b, c, d, t.x[0] - a * u0 - c * v0, t.y[0] - b * u0 - d * v0);
          ctx.drawImage(t.texture, 0, 0); ctx.restore();
        }
      }
      if (t.wire) { ctx.globalAlpha = .06; ctx.strokeStyle = "#8bdeff"; ctx.stroke(); }
    }
    ctx.globalAlpha = 1;
  }
}
