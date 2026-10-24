import * as THREE from "three";

/** CPU projection of the same glTF geometry, for desktops without WebGL2.
 * This keeps the head rotatable/morphable; it is not a static image fallback. */
export class EchoCanvasRenderer {
  readonly domElement = document.createElement("canvas");
  private readonly context: CanvasRenderingContext2D;
  private ratio = 1;
  private projection = new THREE.Matrix4();
  private matrix = new THREE.Matrix4();
  private point = new THREE.Vector3();
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
  render(scene: THREE.Scene, camera: THREE.PerspectiveCamera) {
    const ctx = this.context, width = this.domElement.width, height = this.domElement.height;
    ctx.clearRect(0, 0, width, height);
    scene.updateMatrixWorld(); camera.updateMatrixWorld();
    this.projection.multiplyMatrices(camera.projectionMatrix, camera.matrixWorldInverse);
    ctx.globalCompositeOperation = "lighter";
    scene.traverse(object => {
      if (!(object instanceof THREE.Points || object instanceof THREE.Line) || !object.visible) return;
      const geometry = object.geometry, positions = geometry.getAttribute("position");
      const material = object.material as THREE.PointsMaterial | THREE.LineBasicMaterial;
      const colors = geometry.getAttribute("color");
      const morphs = geometry.morphAttributes.position || [];
      const influences = object.morphTargetInfluences || [];
      this.matrix.multiplyMatrices(this.projection, object.matrixWorld);
      ctx.strokeStyle = `#${material.color.getHexString(THREE.SRGBColorSpace)}`;
      ctx.fillStyle = "#94d3f2";
      ctx.lineWidth = .65 * this.ratio;
      if (object instanceof THREE.Line) ctx.beginPath();
      for (let index = 0; index < positions.count; index++) {
        this.point.fromBufferAttribute(positions, index);
        for (let m = 0; m < morphs.length; m++) {
          const influence = influences[m] || 0;
          if (!influence) continue;
          const morph = morphs[m];
          this.point.x += (morph.getX(index) - (geometry.morphTargetsRelative ? 0 : positions.getX(index))) * influence;
          this.point.y += (morph.getY(index) - (geometry.morphTargetsRelative ? 0 : positions.getY(index))) * influence;
          this.point.z += (morph.getZ(index) - (geometry.morphTargetsRelative ? 0 : positions.getZ(index))) * influence;
        }
        this.point.applyMatrix4(this.matrix);
        if (this.point.z < -1 || this.point.z > 1) continue;
        const x = (this.point.x * .5 + .5) * width, y = (-this.point.y * .5 + .5) * height;
        if (object instanceof THREE.Points) {
          const light = colors ? (colors.getX(index) + colors.getY(index) + colors.getZ(index)) / 3 : 1;
          ctx.globalAlpha = Math.max(0, Math.min(1, material.opacity * light * .18));
          const size = .8 * this.ratio;
          ctx.fillRect(x, y, size, size);
        } else {
          ctx.globalAlpha = material.opacity;
          if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
        }
      }
      if (object instanceof THREE.Line) ctx.stroke();
    });
    ctx.globalAlpha = 1;
  }
}
