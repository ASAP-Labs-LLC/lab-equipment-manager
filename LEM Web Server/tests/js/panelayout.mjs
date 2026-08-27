// The equipment rail's LAYOUT, as opposed to what it says.
//
// Ryan, 27 Aug, walking the panel top to bottom: the QC actions are at the
// bottom of the tab you scroll to reach them, the four tabs run together, the
// identity block is five different kinds of fact in one grey column, the
// Shewhart statistics repeat in full for every method, and the QC checks are
// stacked under the control chart rather than beside it.
//
// "More visually separated" is not a testable sentence, so nothing here tries
// to test it. What these assert is the STRUCTURE the separation needs — an
// element exists, a block is rendered once rather than N times, one thing
// comes before another in document order — and the look itself is judged in a
// screenshot. A test that cannot fail is worse than no test, so where the ask
// is purely taste this file says so and stays out of the way.
//
// The panel is built as one big template literal in `select()`, so document
// order IS source order inside it. Reading the template out of floor.html and
// comparing indexes is exact, needs no DOM, and fails loudly on a rename.
import fs from 'fs';

const html = fs.readFileSync(new URL('../../templates/floor.html', import.meta.url), 'utf8');

let fails = 0;
function claim(name, ok, note) {
  if (ok) { console.log(`  ok   ${name}`); return; }
  fails++;
  console.log(`  FAIL ${name}${note ? `\n         ${note}` : ''}`);
}

/* The `$('#railL').innerHTML = ` template, verbatim. Everything about document
 * order is decided inside it. */
function railTemplate() {
  const at = html.indexOf("$('#railL').innerHTML = `");
  if (at === -1) {
    console.log('FAIL: the rail template moved — $(\'#railL\').innerHTML = ` not found');
    process.exit(1);
  }
  const from = html.indexOf('`', at) + 1;
  // The template ends at the first backtick that closes it. Nested ${} may
  // contain backticks of their own, so walk rather than indexOf.
  let depth = 0;
  for (let i = from; i < html.length; i++) {
    const c = html[i];
    if (c === '\\') { i++; continue; }
    if (c === '$' && html[i + 1] === '{') { depth++; i++; continue; }
    if (c === '}' && depth > 0) { depth--; continue; }
    if (c === '`' && depth === 0) return html.slice(from, i);
  }
  console.log('FAIL: the rail template never closes');
  process.exit(1);
}

const RAIL = railTemplate();
const before = (a, b) => RAIL.indexOf(a) !== -1 && RAIL.indexOf(b) !== -1
  && RAIL.indexOf(a) < RAIL.indexOf(b);

console.log('the equipment rail — layout');

// ── 1. the QC actions move to the top of the QC tab ──────────────────────
//
// They were the last thing in #tab-qc, under the chart and under the full
// checks list. On an instrument with five methods you scroll past fifty lines
// of statistics to reach the two buttons you opened the tab for.
claim('the QC actions come before the control chart',
  before('id="actQc"', 'id="trend"'),
  'actQc/actQcLib still render after #trend');

claim('the standards library button moves with them',
  before('id="actQcLib"', 'id="trend"'));

// ── 2. the four tabs are separated by more than colour ───────────────────
//
// Structure only. Whether it LOOKS separated is a screenshot question; what is
// testable is that the separation survives greyscale and a colour-blind
// reader, which means it cannot be carried by colour alone.
{
  const css = html.slice(0, html.indexOf('</style>'));
  const tabRule = /\.tabs\s*\{[^}]*\}/.exec(css);
  const onRule = /\.tab\.on\s*\{[^}]*\}/.exec(css);
  claim('the tab strip carries a rule of its own',
    !!tabRule && /border/.test(tabRule[0]),
    'no border on .tabs — the strip runs into the pane below it');
  claim('the selected tab is marked by more than colour',
    !!onRule && /(border|box-shadow|outline|background)/.test(onRule[0]),
    'the selected .tab differs only in text colour');
}

// ── 4. the identity block is grouped, not a run of five .meta divs ───────
//
// A verdict, a data recency, a module heartbeat, a source and an identifier
// are five KINDS of fact. Drawn as five identical siblings they read as one
// paragraph. The uid is the one nobody reads unless they are debugging, so it
// goes below a separator rather than in the run.
claim('the identity facts are grouped rather than five bare siblings',
  /class="(idblock|railfacts)"/.test(RAIL),
  'no grouping element around last data / module / watching');

claim('the machine uid is demoted below a separator',
  /class="[^"]*(uidline|railuid)/.test(RAIL),
  'the uid is still a plain .meta in the same run as the status');

// ── 3. the uncertainty line sits ABOVE the module status ─────────────────
//
// The first UI `uncertainty.py` has ever had. Above `module …` and the
// check-in time, per the ask.
claim('the uncertainty line renders above the module status',
  before('id="panelU"', 'module ${moduleStateText'),
  '#panelU is missing or below the module line');

// ── 6. the QC pane splits chart from checks ──────────────────────────────
claim('the QC pane has a chart sub-pane and a checks sub-pane',
  /id="qcsub-chart"/.test(RAIL) && /id="qcsub-checks"/.test(RAIL),
  'QC checks are still stacked under the control chart');

claim('exactly one QC sub-pane is open at a time',
  /id="qcsub-checks"[^>]*hidden/.test(RAIL),
  'the checks pane does not start hidden, so both render at once');

// ── 5. the Shewhart statistics do not render expanded for every method ───
//
// ~10 lines per method; five methods is fifty lines of statistics above
// everything else. Collapse, never delete: the PROVISIONAL warning and the
// "cannot be called repeatability" note are the honest core of the QC work and
// a warning that vanishes when collapsed is a warning that was deleted.
{
  const at = html.indexOf('function trendSeriesHtml');
  const body = at === -1 ? '' : html.slice(at, html.indexOf('\n}', at) + 2);
  claim('trendSeriesHtml() still exists to be judged', !!body);
  claim('the statistics block is collapsible',
    /<details/.test(body),
    'the stats are rendered open, once per method, with no way to fold them');
  claim('and it is folded by default',
    !/<details[^>]*\bopen\b/.test(body),
    'the block still renders expanded for every single QC');
  claim('the PROVISIONAL warning is still in the markup when folded',
    /PROVISIONAL/i.test(body),
    'collapsing deleted the warning rather than folding it');
}

// ── 7. the idle diagnosis is short, and does not repeat the source path ──
//
// 190 characters, 78 of them a UNC path that is already on the `watching` line
// directly below it.
{
  const at = html.indexOf('function diagnosis(');
  const body = at === -1 ? '' : html.slice(at, html.indexOf('\n  }\n}', at) + 6);
  const idle = /Module is running but has parsed nothing|nothing parsed for/.test(body);
  claim('the silent-module diagnosis is still there to be read', idle);
  claim('it does not reprint the source path that `watching` already shows',
    !/parsed nothing[\s\S]{0,200}m\.watching/.test(body),
    'the diagnosis still interpolates m.watching');
  /* The WHOLE sentence, not one backtick of it.
   *
   * The message is three template literals joined by `+`, so matching a single
   * `...` chunk measured 55 characters of a 190-character sentence and passed
   * against the very message being complained about. Take everything from the
   * `warn(` that starts the branch to its closing `);` and strip the JS. */
  const branch = /warn\(\s*'yellow',([\s\S]*?)\);/.exec(
    body.slice(body.search(/if \(!parsed \|\| silentFor/)));
  const rendered = branch
    ? branch[1].replace(/\$\{[^}]*\}/g, '13')   // interpolations -> a number
               .replace(/`/g, '').replace(/\s*\+\s*/g, '')
               .replace(/\s+/g, ' ').trim()
    : '';
  claim('and it is under 80 characters',
    !!rendered && rendered.length < 80,
    rendered ? `${rendered.length} chars: ${rendered}` : 'branch not found');
}

// ── 9. the floor can actually reach the certificate routes ───────────────
claim('the QC standards library can upload a certificate',
  html.includes('/api/qc-standards/certificates'),
  'the routes shipped with no caller in any template');

/* The rename has to SAY it is a rename.
 *
 * The server repoints certificates only when the DELETE carries `renamed_to`,
 * and it deliberately never infers a rename from a delete that happened to
 * look like one. So a client that sends the bare `{name}` gets the old
 * behaviour — the standard is renamed and every certificate on it is orphaned
 * — and every server-side test still passes, because they call the route
 * directly. This is the only place that can catch that gap. */
{
  const at = html.indexOf('A rename is save-new-then-delete-old');
  const body = at === -1 ? '' : html.slice(at, at + 700);
  claim('the rename path exists to be checked', !!body);
  claim('a rename tells the server it is a rename',
    /renamed_to/.test(body),
    'the DELETE sends only {name}, so the repoint never fires and the '
    + 'certificate is orphaned on every rename');
}

// ── 3. what the uncertainty line SAYS ────────────────────────────────────
//
// The placement is asserted above; this is the behaviour, and every rule here
// comes from `uncertainty.py`'s own tests rather than from taste.
{
  const at = html.indexOf('function uncertaintyLine(');
  const body = at === -1 ? '' : html.slice(at, html.indexOf('\n}', at) + 2);
  claim('there is a uncertaintyLine() to judge', !!body,
    'the panel has a #panelU slot and nothing that fills it');

  if (body) {
    const fn = new Function(`
      const esc = s => String(s == null ? '' : s);
      ${body}; return uncertaintyLine;`)();

    const est = (over = {}) => Object.assign({
      estimate_id: 'e1', test_name: 'Flash Point', u_expanded: 1.24, k: 2,
      rw_route: 'control_sample', u_rw_label: 'u(Rw)',
      approved_at: '2026-08-20T10:00:00', approved_by: 'ryan',
      replace_by: '', is_partial: false,
    }, over);

    // An approved estimate shows its number and its coverage factor.
    const ok = fn({estimates: [est()]});
    claim('an approved estimate shows U and k',
      /1\.24/.test(ok.text) && /k\s*=?\s*2/.test(ok.text), ok.text);

    // A computed-but-unapproved number has not been anybody's judgement yet.
    // `TestComputeNeverAutoApproves` is the whole reason approval is separate.
    const un = fn({estimates: [est({approved_at: '', approved_by: ''})]});
    claim('an unapproved estimate is not reported as the number',
      !/1\.24/.test(un.text), un.text);

    // Blank reads as "uncertainty is zero", which is the one value it can
    // never be.
    const none = fn({estimates: []});
    claim('no estimate says so rather than rendering empty',
      !!none.text.trim(), 'empty node');
    claim('and it is marked as an absence, not as a value',
      none.cls === 'none', none.cls);

    // `test_the_interim_route_is_labelled_as_interim`: an interim target must
    // never be presented as a measured u(Rw).
    const interim = fn({estimates: [est({
      rw_route: 'target_limits', u_rw_label: 'u(Rw), interim target',
      replace_by: '2026-12-01'})]});
    claim('an interim estimate is labelled interim',
      /interim/i.test(interim.text), interim.text);
    claim('and is marked so the styling can differ',
      interim.cls === 'interim', interim.cls);

    // `TestAReadThatFailedIsNotAnEmptyRegister`: an outage is not "no
    // estimate". This is the one that turns into a finding if it is wrong.
    const failed = fn(null);
    claim('a failed read is not reported as no estimate',
      failed.cls === 'failed' && !/no estimate/i.test(failed.text),
      `${failed.cls}: ${failed.text}`);
  }
}

console.log(fails ? `\n${fails} failed` : '\nall passed');
process.exit(fails ? 1 : 0);
