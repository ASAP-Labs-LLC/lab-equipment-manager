/* Reproduce trains.js's wheel transform exactly and see which way the rim
 * turns. A correctly rolling wheel has its TOP moving in the direction of
 * travel and its CONTACT POINT moving backwards. */
import * as THREE from 'file:///Users/rynatical/LAB-lem/LEM%20Web%20Server/static/vendor/three.module.min.js';

const up  = new THREE.Vector3(0, 1, 0);
const fwd = new THREE.Vector3(0, 0, 1);            // travelling towards +Z
const side = new THREE.Vector3().crossVectors(up, fwd).normalize();
const nrm  = new THREE.Vector3().crossVectors(fwd, side).normalize();
console.log('fwd', fwd.toArray(), ' side = up x fwd =', side.toArray());

const R = 0.5;
const _q = new THREE.Quaternion(), _m4 = new THREE.Matrix4(), bm = new THREE.Matrix4();

function topOfWheelAfter(angle) {
  // exactly as trains.js does it
  _q.setFromAxisAngle(side, angle);
  _m4.makeBasis(side, nrm, fwd);
  bm.makeRotationFromQuaternion(_q);
  bm.premultiply(_m4);
  bm.setPosition(0, R, 0);
  // the point that starts at the top of the tyre, in the wheel's own frame
  return new THREE.Vector3(0, R, 0).applyMatrix4(bm);
}

for (const [label, sign] of [['code as written  (angle -= v*dt/R)', -1],
                             ['sign flipped     (angle += v*dt/R)', +1]]) {
  const a0 = topOfWheelAfter(0);
  const a1 = topOfWheelAfter(sign * 0.05);          // one small step
  const d  = a1.clone().sub(a0);
  const alongTravel = d.dot(fwd);
  console.log(`${label}: top of wheel moves ${alongTravel > 0 ? 'FORWARD' : 'BACKWARD'}` +
              ` (${alongTravel.toFixed(4)} along travel) -> ${alongTravel > 0 ? 'correct' : 'WRONG WAY'}`);
}
