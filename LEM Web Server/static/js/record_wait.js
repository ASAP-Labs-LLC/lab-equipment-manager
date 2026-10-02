/* record_wait.js: a record LEM could not show yet (503) shows itself once it
   can. It asks /api/ui/instruments/<uid> (memory only, 0 LabCore ops) each
   time the live feed reports a new snapshot, and reloads when the answer is
   no longer "could not ask": 200 draws the record, 404 says it is missing.
   GETs only; nothing on a timer of its own. */
(function () {
    'use strict';
    let uid = '';
    try { uid = JSON.parse((document.getElementById('record-wait') || {}).textContent || '{}').uid || ''; } catch (_e) { uid = ''; }
    if (!uid || !window.LEMLive) return;
    let busy = false;
    window.LEMLive.subscribe(() => {
        if (busy) return;
        busy = true;
        window.LEMLive.bgFetch('/api/ui/instruments/' + encodeURIComponent(uid))
            .then(r => { if (r.status === 200 || r.status === 404) location.reload(); })
            .catch(() => {})
            .finally(() => { busy = false; });
    });
})();
