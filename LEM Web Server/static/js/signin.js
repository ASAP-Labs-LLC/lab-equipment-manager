// Sign in where you are (ia-final §8 T0, piece 3).
//
// One sheet (templates/_signin.html) on every page that has a Sign in, opened
// in place: same document, same URL, same scroll. It is titled for the act
// that needed it ("Sign in to tick", "Sign in to mark done"), and that act
// goes ahead by itself once you are in. Before this, Sign in on four pages
// sent you to /floor with no dialog open and never brought you back, and a
// gated button signed out only printed "Sign in to …".
//
// The pure half (LEMSignInLogic) is node-tested in tests/js/signin_logic.mjs;
// the walk is tests/test_ui_signin.py. No DOM is touched at load except one
// capture listener; every server string goes in through textContent.
//
// The contract a page uses (window.LEMSignIn):
//   LEMSignIn.need(act, then)   signed in: runs then() now. Signed out: opens
//                               "Sign in to <act>" and runs then() after.
//                               Cancel (or Escape) drops it.
//   LEMSignIn.open({act, then}) the sheet, explicitly ({} = plain Sign in)
//   LEMSignIn.switchPerson()    "Switch person": the last person stays signed
//                               in until the next one is
//   LEMSignIn.signOut()         -> Promise<bool>, in place
//   LEMSignIn.user()            '' when signed out
//   'lem:auth' on document      detail {user}, after every change. Pages
//                               repaint from it; the event fires BEFORE a
//                               pending act runs, so the act sees the new user.
// Any control marked data-gated="<act>" (or class "gated") is caught before
// its own handler while signed out, and clicked again after sign-in. It stays
// clickable: dimmed, never pointer-events:none, because a button that does
// nothing is indistinguishable from a broken one.
(function (root) {
    'use strict';

    // ── pure ────────────────────────────────────────────────────────────────
    function words(act, opts) {
        const switching = opts && opts.switching;
        if (switching) {
            return {
                title: 'Switch person',
                ok: 'Sign in',
                why: 'Signed in as ' + switching + '. Sign in as the person here now; '
                    + switching + ' stays signed in if you cancel.',
            };
        }
        if (act) {
            return {
                title: 'Sign in to ' + act,
                ok: 'Sign in and ' + act,
                why: 'Your LabCore name is recorded with it. You stay on this page, '
                    + 'and it goes ahead as soon as you are in.',
            };
        }
        return {
            title: 'Sign in',
            ok: 'Sign in',
            why: 'Your LabCore account, the same one LabStation uses. You stay on this page.',
        };
    }

    // What a gated control does, in words for the title: its data-gated value,
    // else its own label with the first letter lowered ("Assign QC samples…"
    // -> "assign QC samples"). Too long to be a title: a plain Sign in.
    function actOf(c) {
        const g = String((c && c.gated) || '').trim();
        if (g && g !== 'true' && g !== '1') return g;
        let t = String((c && c.text) || '').replace(/\s+/g, ' ').trim().replace(/[….]+$/, '').trim();
        if (!t || t.length > 32) return '';
        return t.charAt(0).toLowerCase() + t.slice(1);
    }

    // Where /signin may send you afterwards: a path on this server, never a
    // URL someone else chose (`//host`, `/\host`, `https:`, `javascript:`).
    function safeNext(s) {
        if (typeof s !== 'string' || !s) return '/';
        if (s.charAt(0) !== '/' || s.charAt(1) === '/' || s.charAt(1) === '\\') return '/';
        if (/[\u0000-\u001f\u007f]/.test(s)) return '/';
        if (/^\/signin(?:[/?#]|$)/.test(s)) return '/';
        return s;
    }

    // Three failures, three fixes: a wrong password (retype it), LEM not
    // answering (the network), LabCore not answering (wait; the password was
    // never checked). -1 = nothing was typed.
    function failureText(status, body) {
        if (status === -1) return 'Type your user name and password, or tap your card.';
        if (!status) return 'Not signed in: LEM did not answer. Check the connection and try again.';
        const err = String((body && body.error) || '');
        if (status === 401) {
            if (/connection error|not connected|labcore returned status/i.test(err)) {
                return 'Not signed in: LabCore did not answer, so the password could not be checked. '
                    + 'Try again in a moment.';
            }
            if (!err || /invalid/i.test(err)) return 'That user name and password were not accepted.';
            return 'Not signed in: ' + err;
        }
        return 'Not signed in: LEM answered ' + status + '. Try again in a moment.';
    }

    const logic = { words, actOf, safeNext, failureText };
    root.LEMSignInLogic = logic;
    if (typeof module !== 'undefined' && module && module.exports) module.exports = logic;
    if (typeof document === 'undefined') return;

    // ── the sheet ───────────────────────────────────────────────────────────
    const $ = (id) => document.getElementById(id);
    let pending = null;
    let switching = false;

    function user() { return (document.body && document.body.dataset.user) || ''; }

    function setUser(name) {
        name = name || '';
        document.body.dataset.user = name;
        document.body.classList.toggle('anon', !name);
        document.dispatchEvent(new CustomEvent('lem:auth', { detail: { user: name } }));
    }

    function here() { return location.pathname + location.search + location.hash; }

    function open(opts) {
        opts = opts || {};
        const dlg = $('signin-sheet');
        if (!dlg || typeof dlg.showModal !== 'function') {
            // no sheet on this page (or a browser with no <dialog>): the no-JS road
            location.href = '/signin?next=' + encodeURIComponent(here());
            return;
        }
        pending = typeof opts.then === 'function' ? opts.then : null;
        switching = !!opts.switching && !!user();
        const w = words(opts.act || '', { switching: switching ? user() : '' });
        $('signin-title').textContent = w.title;
        $('signin-ok').textContent = w.ok;
        $('signin-why').textContent = w.why;
        $('signin-error').hidden = true;
        $('signin-error').textContent = '';
        $('signin-pass').value = '';
        if (switching) $('signin-user').value = '';
        const nx = $('signin-next');
        if (nx) nx.value = here();
        if (!dlg.open) dlg.showModal();
        $('signin-user').focus();
    }

    function need(act, then) {
        if (user()) { then(); return true; }
        open({ act, then });
        return false;
    }

    async function submit(ev) {
        ev.preventDefault();
        const ok = $('signin-ok');
        const err = $('signin-error');
        const name = $('signin-user').value;
        const pass = $('signin-pass').value;
        const say = (text) => { err.textContent = text; err.hidden = false; };
        if (!name.trim() && !pass) { say(failureText(-1)); $('signin-user').focus(); return; }
        ok.disabled = true;
        err.hidden = true;
        let res = null;
        let body = null;
        try {
            res = await fetch('/api/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify({ username: name, password: pass }),
            });
        } catch (_e) { res = null; }
        try { body = res ? await res.json() : null; } catch (_e) { body = null; }
        ok.disabled = false;
        if (res && res.ok && body && body.ok) {
            const run = pending;
            pending = null;
            switching = false;
            $('signin-pass').value = '';
            $('signin-sheet').close();
            setUser(body.user || name);
            if (run) {
                try { run(); } catch (e) { console.error('the act after sign-in failed', e); }
            }
            return;
        }
        say(failureText(res ? res.status : 0, body));
        $('signin-pass').select();
    }

    async function signOut() {
        let ok = false;
        try {
            ok = (await fetch('/api/logout', { method: 'POST', headers: { Accept: 'application/json' } })).ok;
        } catch (_e) { ok = false; }
        if (!ok) return false;
        setUser('');
        return true;
    }

    // A gated control that a repaint replaced while the sheet was up: find
    // its twin by tag and data-* attributes (e.g. the same data-done uid).
    function refind(el) {
        const attrs = Array.from(el.attributes).filter(a => a.name.indexOf('data-') === 0);
        if (!attrs.length) return null;
        const esc = (window.CSS && CSS.escape) ? CSS.escape : (s) => String(s).replace(/["\\]/g, '\\$&');
        const sel = el.tagName.toLowerCase() + attrs.map(a => '[' + a.name + '="' + esc(a.value) + '"]').join('');
        try { return document.querySelector(sel); } catch (_e) { return null; }
    }

    // Capture phase, on document: runs before the control's own handler and
    // before any page's delegated one, so nothing changes signed out.
    document.addEventListener('click', (ev) => {
        if (user()) return;
        const el = ev.target && ev.target.closest && ev.target.closest('[data-gated], .gated');
        if (!el || el.closest('#signin-sheet')) return;
        ev.preventDefault();
        ev.stopImmediatePropagation();
        const act = actOf({ gated: el.getAttribute('data-gated') || '', text: el.textContent });
        open({ act, then: () => {
            const target = el.isConnected ? el : refind(el);
            if (target) target.click();
        } });
    }, true);

    document.addEventListener('DOMContentLoaded', () => {
        const form = $('signin-form');
        if (!form) return;
        const dlg = $('signin-sheet');
        form.addEventListener('submit', submit);
        $('signin-cancel').addEventListener('click', () => dlg.close());
        // Cancel, Escape, or a successful sign-in: whatever was waiting is
        // either done (taken before close) or dropped. Never run later.
        dlg.addEventListener('close', () => { pending = null; switching = false; });
        // Sign in links (the shell's chip, the old pages' header button) open
        // the sheet; without a script they are plain links to /signin?next=.
        document.addEventListener('click', (ev) => {
            const a = ev.target && ev.target.closest && ev.target.closest('[data-signin]');
            if (!a || user()) return;
            ev.preventDefault();
            open({});
        });
    });

    window.LEMSignIn = {
        need, open, signOut, user,
        switchPerson: () => open({ switching: true }),
    };
})(typeof window !== 'undefined' ? window : globalThis);
