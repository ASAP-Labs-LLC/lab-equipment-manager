/* wall.js: boots whichever walls this page carries (ia-final §3.8).

   /floor and /qc carry one view; /wall carries both and shows one at a
   time, alternating every ?every= seconds (default 60) in the ?show= order.
   One live client (live.js, GETs only) and one one-second clock drive every
   view: the stale rule, the footer clock, the rotations. Nothing here
   POSTs, ever: a wall is open forever, and a timer-driven POST would keep
   /healthz idle_seconds near zero so no release would deploy (A.7). */
(function () {
    'use strict';
    const L = window.LEMWallLogic;
    const kiosk = L.parseKiosk(window.location.search);

    function boot() {
        const views = [];
        const floor = document.getElementById('view-floor');
        const qc = document.getElementById('view-qc');
        if (window.LEMLive) window.LEMLive.start();
        if (floor && window.LEMWallFloor) views.push(window.LEMWallFloor.mount(floor, kiosk));
        if (qc && window.LEMWallQC) views.push(window.LEMWallQC.mount(qc, kiosk));
        if (!views.length) return;

        // /wall: one view at a time, in the ?show= order
        const order = L.wallSequence(kiosk.show).map(n => views.find(v => v.name === n)).filter(Boolean);
        const seq = order.length ? order : views;
        let current = 0;
        const reduced = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
        const alt = L.rotator({ count: seq.length, every: kiosk.every * 1000, now: Date.now(),
                                enabled: kiosk.rotate && !reduced });
        views.forEach(v => { if (v !== seq[0]) v.hide(); });
        if (seq.length > 1) {
            // pausing the alternation while somebody points at the wall
            document.addEventListener('pointerenter', () => alt.pause(Date.now()), true);
            document.documentElement.addEventListener('pointerleave', () => alt.resume(Date.now()));
        }
        setInterval(() => {
            const now = Date.now();
            alt.tick(now);
            if (alt.index !== current) {
                seq[current].hide();
                current = alt.index;
                seq[current].show();
            }
            seq[current].tick(now);
        }, 1000);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
    else boot();
})();
