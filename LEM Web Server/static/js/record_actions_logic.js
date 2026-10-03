/* record_actions_logic.js: the record's action sections, their pure half
   (ia-final §3.1 table, piece 6). No DOM. Node-tested in
   tests/js/record_actions.mjs; drawn by record_actions.js.

   Every sheet on the record refuses in words before it sends anything, and
   every rule it refuses by is here, where a test reads it:
     schedule      presets in DAYS (§8: no calendar months in a MINOR), one
                   chip sets kind + interval + name, a typed name is kept
     corrections   a number or nothing; a reason is kept with every change
     actions       the next step the lifecycle allows, and only those steps
                   (equipment_history.LIFECYCLE)
     documents     PDF, PNG or JPEG, up to 25 MB (equipment_documents)
     remove        the exact name, and the password unless unlocked */
(function (root) {
    'use strict';

    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const pad = (n) => String(n).padStart(2, '0');
    const clean = (s) => String(s === undefined || s === null ? '' : s).trim();

    function localDay(nowMs) {
        const d = new Date(nowMs === undefined ? Date.now() : nowMs);
        return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
    }
    function dateOf(iso) {
        const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ''));
        return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
    }

    // ── schedule (§3.1 #2, §8 T6) ──────────────────────────────────────────
    const PRESETS = [
        { key: 'monthly-pm', label: 'Monthly PM', kind: 'pm', days: 30 },
        { key: 'quarterly-pm', label: 'Quarterly PM', kind: 'pm', days: 91 },
        { key: 'annual-cal', label: 'Annual calibration', kind: 'calibration', days: 365 },
    ];
    const PERIOD = { 30: 'Monthly', 91: 'Quarterly', 365: 'Annual' };
    const KIND_WORD = { pm: 'PM', calibration: 'calibration' };

    function presetChip(p) { return p.label + ' · ' + p.days + ' days'; }
    function autoName(days, kind) {
        const k = KIND_WORD[kind] || 'PM';
        if (PERIOD[days]) return PERIOD[days] + ' ' + k;
        const head = k.charAt(0).toUpperCase() + k.slice(1);
        return head + (days === 1 ? ' every day' : ' every ' + days + ' days');
    }
    /** A new schedule, or one task to edit. An edited task's name is the
        person's own, so chips never rename it. */
    function scheduleState(task) {
        if (task) {
            return { uid: String(task.uid || ''), kind: task.kind === 'calibration' ? 'calibration' : 'pm',
                days: Number(task.every) || 30, name: String(task.name || ''),
                lastDone: String(task.last_done || ''), touched: true };
        }
        return { uid: '', kind: 'pm', days: 30, name: autoName(30, 'pm'), lastDone: '', touched: false };
    }
    function renamed(s) { return s.touched ? s : Object.assign({}, s, { name: autoName(s.days, s.kind) }); }
    function applyPreset(s, p) { return renamed(Object.assign({}, s, { kind: p.kind, days: p.days })); }
    function applyKind(s, kind) { return renamed(Object.assign({}, s, { kind: kind === 'calibration' ? 'calibration' : 'pm' })); }
    function applyDays(s, days) { return renamed(Object.assign({}, s, { days: days })); }
    function typedName(s, name) {
        const t = clean(name);
        return t ? Object.assign({}, s, { name: String(name), touched: true })
            : renamed(Object.assign({}, s, { touched: false }));
    }
    function pressedPreset(s) {
        const p = PRESETS.find(x => x.kind === s.kind && x.days === s.days);
        return p ? p.key : null;
    }
    function scheduleProblem(s, nowMs) {
        if (!clean(s.name)) return 'Give the task a name.';
        const d = Number(s.days);
        if (!Number.isInteger(d) || d < 1 || d > 3650) return 'Say how often, in whole days (1 to 3650).';
        if (s.lastDone) {
            if (!dateOf(s.lastDone)) return 'Say which day it was last done, or leave it empty.';
            if (s.lastDone > localDay(nowMs)) return 'It cannot have been done on a day that has not come yet.';
        }
        return '';
    }
    function duplicateNote(tasks, s) {
        const want = clean(s.name).toLowerCase();
        const twin = (tasks || []).find(t => t.uid !== s.uid && clean(t.name).toLowerCase() === want);
        if (!twin) return '';
        return 'It already has ' + (/^[aeiou]/i.test(twin.name) ? 'an ' : 'a ') + twin.name +
            (twin.every ? ' ' + everyText(twin.every) : '') + '; saving adds a second one.';
    }
    function scheduleBody(s) {
        return { uid: s.uid || '', name: clean(s.name), kind: s.kind, interval_days: Number(s.days), last_done: s.lastDone || '' };
    }

    // ── a task row ─────────────────────────────────────────────────────────
    function everyText(days) {
        const n = Number(days);
        if (!n) return '—';
        return n === 1 ? 'every day' : 'every ' + n + ' days';
    }
    function dayText(d, nowMs) {
        const n = new Date(nowMs === undefined ? Date.now() : nowMs);
        return d.getDate() + ' ' + MONTHS[d.getMonth()] + (d.getFullYear() === n.getFullYear() ? '' : ' ' + d.getFullYear());
    }
    /** When a task is due, in words: "was due 26 Sep", "due today", "due in
        5 days", "due 23 Jul 2027", "never done". */
    function dueText(t, nowMs) {
        const due = dateOf(t && t.next_due);
        if (!due) return 'never done';
        const today = dateOf(localDay(nowMs));
        const days = Math.round((due - today) / 86400000);
        if (days < 0) return 'was due ' + dayText(due, nowMs);
        if (days === 0) return 'due today';
        if (days === 1) return 'due tomorrow';
        if (days <= 30) return 'due in ' + days + ' days';
        return 'due ' + dayText(due, nowMs);
    }

    // ── correction factors (§3.1 #3) ───────────────────────────────────────
    const UNITS = { C: '°C', F: '°F', degC: '°C', degF: '°F' };
    function offsetText(v, units) {
        const n = Number(v);
        if (!isFinite(n)) return '—';
        const u = clean(units);
        const mag = String(Math.abs(n));
        const s = n === 0 ? '0' : (n < 0 ? '−' : '+') + mag;
        return u ? s + ' ' + (UNITS[u] || u) : s;
    }
    const NUMBER = /^[+\-−]?(\d+([.,]\d*)?|[.,]\d+)$/;
    function correctionProblem(value, reason) {
        if (!NUMBER.test(clean(value))) return 'Enter the offset as a number, like -1.5.';
        if (!clean(reason)) return 'Say why. The reason is kept with the change (17025 §7.8.2).';
        return '';
    }
    /** The number the route takes: a comma decimal and a typographic minus
        are what a person types, not a different number. */
    function correctionNumber(value) {
        return clean(value).replace('−', '-').replace(',', '.');
    }
    function historyLabel(n) {
        return typeof n === 'number' && isFinite(n) ? 'Change history (' + n + ')' : 'Change history';
    }

    // ── corrective actions (§3.1 #4) ───────────────────────────────────────
    // equipment_history.LIFECYCLE, as steps a person takes. Assign and note
    // ride alongside: assign on any unfinished action, a note at any time.
    const NEXT = { open: 'record', actioned: 'verify', verified: 'close' };
    const ALLOWED = {
        open: ['record', 'assign', 'note', 'withdraw'],
        actioned: ['verify', 'record', 'assign', 'note', 'withdraw'],
        verified: ['close', 'assign', 'note', 'withdraw'],
        closed: ['note'],
        withdrawn: ['note'],
    };
    function actionSteps(state) {
        return { next: NEXT[state] || null, steps: (ALLOWED[state] || ['note']).slice() };
    }
    const STEP_BUTTON = { record: 'Record what was done…', verify: 'Verify it worked…', close: 'Close it…',
        withdraw: 'Withdraw…', assign: 'Assign…', note: 'Add a note…' };
    const STEP_TAB = { record: 'Record', verify: 'Verify', close: 'Close', withdraw: 'Withdraw', assign: 'Assign', note: 'Note' };
    const STEP_GO = { record: 'Record it', verify: 'Mark verified', close: 'Close it', withdraw: 'Withdraw it', assign: 'Save', note: 'Add the note' };
    function stepButton(step) { return STEP_BUTTON[step] || step; }
    const STATE_WORD = { open: 'Open', actioned: 'Recorded', verified: 'Verified', closed: 'Closed', withdrawn: 'Withdrawn' };
    function stateWord(state) { return STATE_WORD[state] || state; }
    function stepProblem(step, f) {
        const text = clean(f && f.text);
        if (step === 'record' && !text) return 'Say what was done.';
        if (step === 'verify' && !text) return 'Say how you checked that it worked.';
        if (step === 'withdraw' && !text) return 'Say why it is withdrawn.';
        if (step === 'note' && !text) return 'Write the note.';
        if (step === 'assign' && !clean(f && f.who) && !clean(f && f.due)) return 'Name who owns it, or give a due date.';
        return '';
    }
    function stepRequest(step, uid, f) {
        const base = '/api/equipment/actions/' + encodeURIComponent(uid) + '/';
        const text = clean(f && f.text);
        if (step === 'record') return [base + 'record', { action_taken: text }];
        if (step === 'verify') return [base + 'verify', { note: text }];
        if (step === 'close') return [base + 'close', { note: text }];
        if (step === 'withdraw') return [base + 'withdraw', { reason: text }];
        if (step === 'assign') return [base + 'assign', { assigned_to: clean(f.who), due_at: clean(f.due), priority: clean(f.priority) || 'normal' }];
        return [base + 'note', { note: text }];
    }
    const STEP_TOAST = { record: 'Recorded what was done', verify: 'Verified', close: 'Corrective action closed',
        withdraw: 'Corrective action withdrawn', assign: 'Assignment saved', note: 'Note added' };

    // ── documents (§3.1 #5) ────────────────────────────────────────────────
    const MAX_BYTES = 25 * 1024 * 1024;
    function sizeText(n) {
        const b = Number(n) || 0;
        if (b < 1024) return b + ' B';
        if (b < 1024 * 1024) return (Math.round(b / 102.4) / 10) + ' KB';
        return (Math.round(b / (1024 * 102.4)) / 10) + ' MB';
    }
    function uploadProblem(file) {
        if (!file) return 'Choose a file.';
        if (!file.size) return 'That file is empty.';
        if (!/\.(pdf|png|jpe?g)$/i.test(String(file.name || ''))) return 'Only PDF, PNG or JPEG files are kept here.';
        if (file.size > MAX_BYTES) return 'That file is over 25 MB, the most one document can be.';
        return '';
    }

    // ── remove (§3.1 #9) ───────────────────────────────────────────────────
    function removeProblem(typed, title, needPassword, password) {
        if (clean(typed) !== clean(title)) return 'Type the name exactly as it is shown: ' + clean(title) + '.';
        if (needPassword && !String(password || '')) return 'Enter your password to unlock removing.';
        return '';
    }

    // ── words ──────────────────────────────────────────────────────────────
    function toast(what, who, at) { return what + (who ? ' · ' + who : '') + (at ? ' · ' + at : ''); }
    function whoWhen(by, at, nowMs) {
        const parts = [];
        if (clean(by)) parts.push(clean(by));
        const d = at ? new Date(String(at).length <= 10 ? String(at) + 'T00:00:00' : String(at)) : null;
        if (d && !isNaN(d)) {
            const n = new Date(nowMs === undefined ? Date.now() : nowMs);
            const same = d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate();
            parts.push((same ? 'today' : d.getDate() + ' ' + MONTHS[d.getMonth()] + (d.getFullYear() === n.getFullYear() ? '' : ' ' + d.getFullYear())) +
                ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes()));
        }
        return parts.join(' · ');
    }

    const api = {
        PRESETS, presetChip, autoName, scheduleState, applyPreset, applyKind, applyDays, typedName, pressedPreset,
        scheduleProblem, duplicateNote, scheduleBody, everyText, dueText, localDay,
        offsetText, correctionProblem, correctionNumber, historyLabel,
        actionSteps, stepButton, stateWord, stepProblem, stepRequest, STEP_TAB, STEP_GO, STEP_TOAST,
        sizeText, uploadProblem, removeProblem, toast, whoWhen,
    };
    root.LEMRecordActions = api;
    if (typeof module !== 'undefined' && module && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
