/* floor_map.js: the Instruments page's Floor map view (/?view=map, piece 12).

   instruments.js owns the data (the same /api/ui/instruments answer the list
   draws) and hands it here on every render; this file owns the map's own
   state and draws it with static/js/plan.js:

     the plan          the bays somebody placed, on one level, sized to their
                       bounding box; each bay is a link to its record
     the level seg     only when more than one level holds instruments
     not on the map    "Not on the map: Agilent GC 2 · Place it"
     ?focus=<uid>      that bay is ringed and scrolled to (the record's
                       "Show on the floor map")
     ?cause=<key>      a Needs-you tile steps the other bays back
     Arrange…          an explicit mode, entered signed in and left with
                       Done. Outside it nothing can be dragged (P15: the old
                       floor's drag lock defaulted to UNLOCKED on sign-in)

   A move is one POST /api/machines/<uid>/position, drawn at once and put
   back, in words, when the server refuses it. The lab-wide freeze (/api/map)
   is respected: a frozen floor says so and offers to unfreeze, and a lock
   state that could not be read is said as such, never taken as "unlocked".
   Every string goes in through textContent. */
(function () {
    'use strict';
    const P = window.LEMPlan;
    const S = window.LEMShell;
    const $ = (id) => document.getElementById(id);
    const page = $('main');
    if (!P || !S || !page || page.dataset.view !== 'map') return;
    const h = S.h;

    let data = null;
    let failedAt = null;
    let view = P.parseMapView(location.search);
    let arranging = false;
    let picked = null;          // uid picked up in Arrange (click, then a bay)
    let lay = null;             // the layout on screen
    let err = '';               // the last refused move, in words
    let note = '';              // the last good move, in words
    let gate = null;            // {kind: 'frozen'|'unknown', text}: why Arrange could not start
    let scrolledTo = '';
    const moved = {};           // uid -> [x, y] saved, while the server catches up
    const saving = new Set();
    let drag = null;
    let suppressClick = false;

    const user = () => (window.LEMSignIn && window.LEMSignIn.user ? window.LEMSignIn.user() : (document.body.dataset.user || ''));
    const announce = () => document.dispatchEvent(new CustomEvent('lem:map-view'));

    function setUrl(push) {
        const url = '/' + P.mapQuery(view);
        if (location.pathname + location.search === url) return;
        if (push) history.pushState(null, '', url); else history.replaceState(null, '', url);
    }

    // ── the plan ──────────────────────────────────────────────────────────
    function rowsHere(level) {
        return P.onLevel((data && data.instruments) || [], level, data);
    }

    function sizes(cols, rows) {
        // the plan fills the height of the window below its own top, less
        // the line under it: one uniform cell, never below a readable bay
        // and never taller than it is wide; a wide floor packs tighter
        const plan = $('plan');
        plan.hidden = false;
        // a phone's bays may be taller than wide: a verdict and its detail
        // wrap there rather than being cut (2026-10-07)
        const d = P.density(plan.clientWidth, cols, arranging, window.innerWidth < 700);
        const top = plan.getBoundingClientRect().top + window.scrollY;
        const foot = $('plan-unplaced').hidden ? 0 : 52;
        const below = 24 /* card padding */ + 32 /* page padding */ + foot + 2 * d.gap;
        const width = (plan.clientWidth - 2 * d.gap - d.gap * (cols - 1)) / Math.max(1, cols);
        return { compact: d.compact, gap: d.gap,
                 cellH: P.cellHeight(window.innerHeight - top - below, rows, d.gap, width, d.minH) };
    }

    function levelName(level) {
        const lv = ((data && data.levels) || []).find(l => l.uid === level);
        if (lv) return lv.name;
        const r = ((data && data.instruments) || [])[0];
        const w = r && r.where && r.where.level;
        return w && w !== 'No level' ? w : 'Floor plan';
    }

    function drawLevels(level) {
        const seg = $('level-seg');
        const levels = (data && data.levels) || [];
        seg.hidden = levels.length < 2;
        $('plan-title').hidden = levels.length >= 2;
        $('plan-title').textContent = levelName(level);
        // with a cause chosen, a dot marks every level that has it, so the
        // instruments a tile is about are never on a floor nobody looks at
        const withCause = new Set(view.cause ? ((data && data.instruments) || [])
            .filter(r => !P.dimmed(r, view.cause)).map(r => r.level_uid) : []);
        // each tab carries its level's worst state and how many there need
        // you (2026-10-07, as the wall's tabs do): a problem upstairs is seen
        // from the ground floor's tab
        const marks = new Map(P.levelMarks(data).map(m => [m.uid, m]));
        seg.replaceChildren(...levels.map(lv => {
            const m = marks.get(lv.uid) || {};
            return h('a', {
                href: '/' + P.mapQuery({ level: lv.uid, cause: view.cause, arrange: view.arrange }),
                className: [withCause.has(lv.uid) ? 'has-cause' : '', m.state ? 's-' + m.state : ''].join(' ').trim() || null,
                title: withCause.has(lv.uid) ? 'Has an instrument with this problem' : null,
                'data-state': m.state || null,
                // the name is said even where a phone shows only the glyph
                'aria-label': lv.name + (m.need ? ', ' + m.need + ' need you' : ''),
                'aria-current': lv.uid === level ? 'page' : null, 'data-level': lv.uid },
                m.state ? h('span', { className: 'glyph ' + m.glyph, 'aria-hidden': 'true' }) : null,
                h('span', { className: 'lv-name' }, lv.name),
                m.need ? h('span', { className: 'lv-n', title: m.need + ' need you' }, String(m.need)) : null);
        }));
    }

    function drawUnplaced(unplaced) {
        const box = $('plan-unplaced');
        box.hidden = !unplaced.length;
        if (!unplaced.length) { box.replaceChildren(); return; }
        const kids = [h('span', { className: 'pu-head' }, 'Not on the map:')];
        unplaced.forEach((r, i) => {
            if (i) kids.push(h('span', { className: 'pu-sep', 'aria-hidden': 'true' }, ','));
            if (arranging) {
                kids.push(h('button', { type: 'button', className: 'pu-pick' + (picked === r.uid ? ' picked' : ''),
                    'aria-pressed': picked === r.uid ? 'true' : 'false', 'data-uid': r.uid, 'data-testid': 'place-pick',
                    onclick: () => pick(picked === r.uid ? null : r.uid) }, r.title));
            } else {
                kids.push(h('span', { className: 'pu-item' },
                    h('a', { className: 'pu-name', href: r.href }, r.title),
                    h('span', { className: 'pu-dot', 'aria-hidden': 'true' }, '·'),
                    h('a', { className: 'link pu-place', 'data-testid': 'place-it',
                             href: '/' + P.mapQuery({ level: view.level, focus: r.uid, arrange: true }) }, 'Place it')));
            }
        });
        if (arranging) kids.push(h('span', { className: 'caption pu-hint' }, picked && unplaced.some(r => r.uid === picked)
            ? 'Now choose an empty bay.' : 'Pick one, then an empty bay.'));
        box.replaceChildren(...kids);
    }

    function caption() {
        if (failedAt) return 'Couldn’t refresh · showing the plan as last read';
        if (note) return note;
        return '';
    }

    function draw() {
        if (!data || data.state !== 'ready') return;
        const level = P.currentLevel(data, view);
        const here = rowsHere(level);
        lay = P.layout(here, { ring: arranging ? 1 : 0, moved });
        if (arranging && !lay.w) {
            lay = { w: 4, h: 2, origin: [0, 0], bays: [], unplaced: lay.unplaced };   // room to place the first
        }
        drawLevels(level);
        drawUnplaced(lay.unplaced);

        const empty = $('plan-empty');
        const all = (data.instruments || []).length;
        let msg = '';
        if (!all) msg = 'LabCore answered with no instruments. They are added in LabStation › LEM module › New machine…';
        else if (!lay.bays.length && !arranging) msg = 'Nobody has placed an instrument on ' + (level ? levelName(level) : 'the floor') + ' yet. Arrange… to place them.';
        if (view.focus && all && !(data.instruments || []).some(r => r.uid === view.focus)) {
            msg = (msg ? msg + ' ' : '') + 'There is no instrument “' + view.focus + '” in LEM, so nothing is marked.';
        }
        empty.textContent = msg;
        empty.hidden = !msg;

        const plan = $('plan');
        plan.hidden = !lay.w;
        if (lay.w) {
            P.draw(plan, lay, Object.assign({ arranging, focus: view.focus, cause: view.cause, picked, saving,
                                              wrapLines: true }, sizes(lay.w, lay.h)));
        } else plan.replaceChildren();
        $('plan-caption').textContent = caption();
        drawArrange();
        if (view.focus && scrolledTo !== view.focus) {
            const b = plan.querySelector('.bay[data-uid="' + CSS.escape(view.focus) + '"]');
            if (b) { scrolledTo = view.focus; b.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
        }
    }

    // ── Arrange ───────────────────────────────────────────────────────────
    function drawArrange() {
        const btn = $('arrange');
        const bar = $('arrange-bar');
        const open = arranging || !!gate;
        btn.hidden = open;
        btn.setAttribute('aria-pressed', arranging ? 'true' : 'false');
        bar.hidden = !open;
        $('floor-map').classList.toggle('is-arranging', arranging);
        const retry = $('arrange-retry');
        if (gate) {
            $('arrange-head').textContent = gate.kind === 'frozen' ? 'The floor is frozen' : 'Couldn’t check the floor';
            $('arrange-text').textContent = gate.text;
            retry.hidden = false;
            retry.textContent = gate.kind === 'frozen' ? 'Unfreeze and arrange' : 'Try again';
            $('arrange-done').textContent = 'Cancel';
        } else {
            $('arrange-head').textContent = 'Arranging';
            $('arrange-text').textContent = picked
                ? 'Now choose an empty bay for ' + titleOf(picked) + ', or press Escape to put it down.'
                : 'Drag an instrument to an empty bay, or pick it and then a bay. Each move is saved at once.';
            retry.hidden = true;
            $('arrange-done').textContent = 'Done';
        }
        const e = $('arrange-error');
        e.hidden = !err;
        e.textContent = err;
    }

    function titleOf(uid) {
        const r = ((data && data.instruments) || []).find(x => x.uid === uid);
        return r ? r.title : uid;
    }

    async function enter() {
        gate = null; err = ''; note = '';
        let j = null;
        try {
            const r = await fetch('/api/map', { headers: { 'X-LEM-Background': '1' } });
            if (!r.ok) throw new Error('HTTP ' + r.status);
            j = await r.json();
        } catch (e) {
            j = { known: false, error: 'LEM did not answer (' + ((e && e.message) || 'no answer') + ')' };
        }
        if (!j || j.known === false) {
            gate = { kind: 'unknown', text: 'LEM could not read whether the floor is frozen, so nothing can move yet. ' +
                     ((j && j.error) ? j.error : '') };
        } else if (j.locked) {
            gate = { kind: 'frozen', text: 'Somebody froze the floor plan for everyone, so nothing can move. Unfreeze it to arrange.' };
        } else {
            arranging = true;
            const here = rowsHere(P.currentLevel(data, view));
            picked = view.focus && here.some(r => r.uid === view.focus) ? view.focus : null;
        }
        view.arrange = arranging;
        setUrl(false);
        draw();
        if (arranging) {
            const target = picked ? document.querySelector('.bay.picked, .pu-pick.picked') : $('arrange-done');
            if (target) target.focus({ preventScroll: true });
        }
    }

    async function unfreeze() {
        try {
            const r = await fetch('/api/map', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                                body: JSON.stringify({ locked: false }) });
            const b = await r.json().catch(() => ({}));
            if (!r.ok || b.locked) {
                if (r.status === 401) { window.LEMSignIn.need('unfreeze the floor', unfreeze); return; }
                gate = { kind: 'frozen', text: 'Not unfrozen: ' + (b.error || ('HTTP ' + r.status)) + '. The floor is still frozen.' };
                draw();
                return;
            }
        } catch (_e) {
            gate = { kind: 'frozen', text: 'Not unfrozen: LEM did not answer. The floor is still frozen.' };
            draw();
            return;
        }
        enter();
    }

    function done() {
        arranging = false; picked = null; gate = null; err = '';
        view.arrange = false;
        setUrl(false);
        draw();
        $('arrange').focus({ preventScroll: true });
    }

    function start() {
        if (!window.LEMSignIn) return;
        window.LEMSignIn.need('arrange the floor', enter);
    }

    function pick(uid) {
        picked = uid;
        err = '';
        draw();
        if (uid) {
            const el = document.querySelector('.bay[data-uid="' + CSS.escape(uid) + '"], .pu-pick[data-uid="' + CSS.escape(uid) + '"]');
            if (el) el.focus({ preventScroll: true });
        }
    }

    async function move(uid, x, y) {
        if (!P.canDrag({ arranging, user: user() })) return;
        if (!lay || !P.isFree(lay, x, y)) return;
        const to = P.toSaved(x, y, lay.origin);
        const had = Object.prototype.hasOwnProperty.call(moved, uid) ? moved[uid] : undefined;
        moved[uid] = [to.x, to.y];
        saving.add(uid);
        picked = null; err = ''; note = '';
        draw();
        const putBack = (why) => {
            if (had === undefined) delete moved[uid]; else moved[uid] = had;
            saving.delete(uid);
            err = 'Not saved: ' + why + ' ' + titleOf(uid) + ' is back where it was.';
            draw();
        };
        let r;
        try {
            r = await fetch('/api/machines/' + encodeURIComponent(uid) + '/position', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(to) });
        } catch (_e) {
            putBack('LEM did not answer.');
            return;
        }
        if (r.status === 401) {
            putBack('you are signed out.');
            window.LEMSignIn.need('move it', () => move(uid, x, y));
            return;
        }
        if (!r.ok) {
            const b = await r.json().catch(() => ({}));
            putBack(String(b.error || ('HTTP ' + r.status)).replace(/\.?$/, '.'));
            return;
        }
        saving.delete(uid);
        note = titleOf(uid) + ' moved.';
        draw();
    }

    // ── pointer: drag in Arrange only ─────────────────────────────────────
    function cellAt(cx, cy) {
        const plan = $('plan');
        const rect = plan.getBoundingClientRect();
        const cs = getComputedStyle(plan);
        const left = rect.left + parseFloat(cs.paddingLeft), top = rect.top + parseFloat(cs.paddingTop);
        const width = rect.width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
        const height = rect.height - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
        const gap = parseFloat(cs.columnGap) || 0;
        const cw = (width - gap * (lay.w - 1)) / lay.w;
        const ch = (height - gap * (lay.h - 1)) / lay.h;
        const x = Math.floor((cx - left + gap / 2) / (cw + gap));
        const y = Math.floor((cy - top + gap / 2) / (ch + gap));
        if (x < 0 || y < 0 || x >= lay.w || y >= lay.h) return null;
        return { x, y };
    }
    function markOver(c) {
        for (const el of document.querySelectorAll('#plan .cell.over')) el.classList.remove('over');
        if (!c) return;
        const el = document.querySelector('#plan .cell[data-x="' + c.x + '"][data-y="' + c.y + '"]');
        if (el) el.classList.add('over');
    }

    const plan = $('plan');
    plan.addEventListener('pointerdown', (ev) => {
        const bay = ev.target.closest('.bay');
        if (!bay || ev.button !== 0) return;
        if (!P.canDrag({ arranging, user: user() })) return;     // P15: viewing never drags
        drag = { uid: bay.dataset.uid, el: bay, sx: ev.clientX, sy: ev.clientY, started: false, over: null };
        bay.setPointerCapture(ev.pointerId);
    });
    plan.addEventListener('pointermove', (ev) => {
        if (!drag) return;
        const dx = ev.clientX - drag.sx, dy = ev.clientY - drag.sy;
        if (!drag.started && Math.hypot(dx, dy) < 5) return;
        drag.started = true;
        drag.el.classList.add('dragging');
        drag.el.style.transform = 'translate(' + dx + 'px,' + dy + 'px)';
        drag.over = cellAt(ev.clientX, ev.clientY);
        markOver(drag.over && P.isFree(lay, drag.over.x, drag.over.y) ? drag.over : null);
    });
    function endDrag(ev, cancelled) {
        if (!drag) return;
        const d = drag;
        drag = null;
        markOver(null);
        if (!d.started) return;                       // a click: handled by 'click'
        suppressClick = true;
        setTimeout(() => { suppressClick = false; }, 0);
        d.el.classList.remove('dragging');
        d.el.style.transform = '';
        const c = cancelled ? null : cellAt(ev.clientX, ev.clientY);
        if (c && P.isFree(lay, c.x, c.y)) { move(d.uid, c.x, c.y); return; }
        if (c && !cancelled) {
            const who = P.occupant(lay, c.x, c.y);
            if (who && who !== d.uid) { err = 'That bay is taken by ' + titleOf(who) + '. Move it first, or choose an empty bay.'; draw(); }
        }
    }
    plan.addEventListener('pointerup', (ev) => endDrag(ev, false));
    plan.addEventListener('pointercancel', (ev) => endDrag(ev, true));
    plan.addEventListener('click', (ev) => {
        if (!arranging) return;                        // a bay is a link to its record
        ev.preventDefault();
        if (suppressClick) return;
        const bay = ev.target.closest('.bay');
        if (bay) { pick(picked === bay.dataset.uid ? null : bay.dataset.uid); return; }
        const cell = ev.target.closest('.cell');
        if (cell && picked) move(picked, Number(cell.dataset.x), Number(cell.dataset.y));
    });
    // the browser's own drag of a link or an image is never a move
    plan.addEventListener('dragstart', (ev) => ev.preventDefault());

    // ── levels as pages: a swipe or an arrow key turns the level ─────────
    // Like the wall (2026-10-07). Never in Arrange, where a sideways drag is
    // a move, and never under the bay a swipe ends on.
    function turn(step) {
        const levels = (data && data.levels) || [];
        if (arranging || gate || levels.length < 2 || !step) return;
        const next = P.stepLevel(data, P.currentLevel(data, view), step);
        window.LEMFloorMap.go({ level: next, cause: view.cause, focus: '' });
    }
    let swipe = null, swallow = false;
    plan.addEventListener('pointerdown', (ev) => {
        // a plan that scrolls sideways (a wide floor on a phone) is scrolled, not swiped
        const scrolls = plan.scrollWidth > plan.clientWidth + 1;
        swipe = arranging || scrolls ? null : { x: ev.clientX, y: ev.clientY, id: ev.pointerId };
    });
    plan.addEventListener('pointerup', (ev) => {
        if (!swipe || swipe.id !== ev.pointerId || arranging) { swipe = null; return; }
        const step = P.swipeStep(ev.clientX - swipe.x, ev.clientY - swipe.y);
        swipe = null;
        if (!step) return;
        swallow = true;
        setTimeout(() => { swallow = false; }, 400);
        turn(step);
    });
    plan.addEventListener('pointercancel', () => { swipe = null; });
    plan.addEventListener('click', (ev) => {
        if (swallow) { ev.preventDefault(); ev.stopPropagation(); swallow = false; }
    }, true);
    document.addEventListener('keydown', (ev) => {
        if (ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey) return;
        if (ev.key !== 'ArrowRight' && ev.key !== 'ArrowLeft') return;
        const t = ev.target;
        if (t && (t.closest('input, textarea, select, [contenteditable="true"]') || document.querySelector('dialog[open]'))) return;
        if (arranging || gate || !(data && data.levels && data.levels.length > 1)) return;
        ev.preventDefault();
        turn(ev.key === 'ArrowRight' ? 1 : -1);
    });

    $('arrange').addEventListener('click', () => { if (!arranging) start(); });
    $('arrange-done').addEventListener('click', done);
    $('arrange-retry').addEventListener('click', () => {
        if (gate && gate.kind === 'frozen') unfreeze(); else enter();
    });
    document.addEventListener('keydown', (ev) => {
        if (ev.key !== 'Escape' || (!arranging && !gate)) return;
        if (document.querySelector('dialog[open]')) return;     // the sign-in sheet's own Escape
        if (picked) { pick(null); return; }
        done();
    });
    document.addEventListener('lem:auth', () => {
        if (arranging && !user()) done();             // signed out mid-arrange: nothing can move
        else draw();
    });
    let resizeTimer = null;
    window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(draw, 80); });

    // ── what instruments.js calls ─────────────────────────────────────────
    let first = true;
    window.LEMFloorMap = {
        view: () => view,
        layout: () => lay,                         // what is on screen (read-only; tests read it)
        tileHref(key, active) { return '/' + P.mapQuery({ level: view.level, cause: active ? '' : key }); },
        render(d, opts) {
            data = d;
            failedAt = (opts && opts.failedAt) || null;
            // a move the server now reports is no longer ours to hold
            for (const uid of Object.keys(moved)) {
                if (saving.has(uid)) continue;
                const r = ((d && d.instruments) || []).find(x => x.uid === uid);
                const p = r && r.where && r.where.pos;
                if (!r || (p && P.bayIndex(p[0]) === P.bayIndex(moved[uid][0]) && P.bayIndex(p[1]) === P.bayIndex(moved[uid][1]))) delete moved[uid];
            }
            if (drag) return;                          // never redraw under a hand
            draw();
            if (first) {
                first = false;
                if (view.arrange) {
                    // the record's "Move on the map": Arrange, with it picked
                    view.arrange = false;
                    setUrl(false);
                    start();
                }
            }
        },
        go(v, opts) {
            const push = !(opts && opts.push === false);
            const wantArrange = !!v.arrange;
            view = Object.assign({ level: '', cause: '', focus: '', arrange: false }, v, { arrange: arranging });
            if (v.focus !== scrolledTo) scrolledTo = '';
            setUrl(push);
            draw();
            announce();
            if (wantArrange && !arranging) {
                if (view.focus) picked = null;
                start();
            } else if (wantArrange && arranging && view.focus) {
                pick(view.focus);
            }
        },
    };
})();
