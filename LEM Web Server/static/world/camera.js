/* camera.js — how the floor is looked at.
 *
 * An orbit rig, written here rather than pulled from three's addons because
 * this one has to do three things the stock controls do not: refuse to go
 * under the ground, fly to an instrument when one is selected, and keep a slow
 * drift going on an unattended wall display so the map never reads as a frozen
 * screenshot.
 */
import * as THREE from 'three';

const DEG = Math.PI / 180;

export class CameraRig {
  constructor(camera, dom, opts = {}) {
    this.camera = camera;
    this.dom = dom;
    this.target = new THREE.Vector3(0, 0, 0);
    this.goalTarget = this.target.clone();

    this.distance = opts.distance ?? 330;
    this.goalDistance = this.distance;
    this.minDistance = opts.minDistance ?? 26;
    this.maxDistance = opts.maxDistance ?? 900;

    this.yaw = opts.yaw ?? -0.7;
    this.pitch = opts.pitch ?? 0.46;
    this.goalYaw = this.yaw;
    this.goalPitch = this.pitch;
    this.minPitch = 6 * DEG;         // never below the horizon: no underside
    this.maxPitch = 78 * DEG;

    this.idleDrift = opts.idleDrift !== false;
    this.idleAfter = 22;             // seconds of no input before the drift
    this.sinceInput = 0;
    this.enabled = true;

    this._drag = null;
    this._bind();
    this.apply(1);
  }

  _bind() {
    const dom = this.dom;
    this._onDown = e => {
      if (!this.enabled || e.button === 2) return;
      /* Left button may be a machine drag; the world claims it first and sets
       * `suspended` if so. Middle/right-with-shift always pans. */
      if (this.suspended) return;
      this._drag = {x: e.clientX, y: e.clientY,
                    pan: e.button === 1 || e.shiftKey};
      dom.setPointerCapture?.(e.pointerId);
      this.sinceInput = 0;
    };
    this._onMove = e => {
      this.sinceInput = 0;
      if (!this._drag) return;
      const dx = e.clientX - this._drag.x, dy = e.clientY - this._drag.y;
      this._drag.x = e.clientX; this._drag.y = e.clientY;
      if (this._drag.pan) {
        const k = this.distance * 0.0016;
        const right = new THREE.Vector3(Math.cos(this.yaw), 0, -Math.sin(this.yaw));
        const fwd = new THREE.Vector3(Math.sin(this.yaw), 0, Math.cos(this.yaw));
        this.goalTarget.addScaledVector(right, -dx * k);
        this.goalTarget.addScaledVector(fwd, -dy * k);
      } else {
        this.goalYaw -= dx * 0.0042;
        this.goalPitch = Math.max(this.minPitch,
          Math.min(this.maxPitch, this.goalPitch + dy * 0.0032));
      }
    };
    this._onUp = e => {
      this._drag = null;
      dom.releasePointerCapture?.(e.pointerId);
    };
    this._onWheel = e => {
      if (!this.enabled) return;
      e.preventDefault();
      this.sinceInput = 0;
      const k = Math.exp(e.deltaY * 0.0011);
      this.goalDistance = Math.max(this.minDistance,
        Math.min(this.maxDistance, this.goalDistance * k));
    };
    dom.addEventListener('pointerdown', this._onDown);
    window.addEventListener('pointermove', this._onMove);
    window.addEventListener('pointerup', this._onUp);
    dom.addEventListener('wheel', this._onWheel, {passive: false});
  }

  /** Frame a point — used when an instrument is selected on the floor. */
  flyTo(point, distance) {
    this.goalTarget.copy(point);
    if (distance) this.goalDistance = Math.max(this.minDistance,
      Math.min(this.maxDistance, distance));
    this.sinceInput = 0;
  }

  /** Frame a bounding box — the whole lab, on first paint and on re-layout. */
  frame(box, {pad = 1.25} = {}) {
    const size = box.getSize(new THREE.Vector3());
    const centre = box.getCenter(new THREE.Vector3());
    const radius = Math.max(size.x, size.z) * 0.5 * pad;
    const fov = this.camera.fov * DEG;
    this.goalTarget.copy(centre);
    this.goalTarget.y = box.min.y + size.y * 0.18;
    this.goalDistance = Math.max(this.minDistance,
      Math.min(this.maxDistance, radius / Math.tan(fov * 0.5) * 0.95));
  }

  update(dt) {
    this.sinceInput += dt;
    if (this.idleDrift && this.sinceInput > this.idleAfter && !this._drag) {
      this.goalYaw += dt * 0.012;     // a slow turn, ~9 minutes for a full lap
    }
    this.apply(1 - Math.pow(0.0016, dt));
  }

  apply(k) {
    this.yaw += (this.goalYaw - this.yaw) * k;
    this.pitch += (this.goalPitch - this.pitch) * k;
    this.distance += (this.goalDistance - this.distance) * k;
    this.target.lerp(this.goalTarget, k);
    const cp = Math.cos(this.pitch), sp = Math.sin(this.pitch);
    this.camera.position.set(
      this.target.x + Math.sin(this.yaw) * cp * this.distance,
      this.target.y + sp * this.distance,
      this.target.z + Math.cos(this.yaw) * cp * this.distance);
    this.camera.lookAt(this.target);
  }

  dispose() {
    this.dom.removeEventListener('pointerdown', this._onDown);
    window.removeEventListener('pointermove', this._onMove);
    window.removeEventListener('pointerup', this._onUp);
    this.dom.removeEventListener('wheel', this._onWheel);
  }
}
