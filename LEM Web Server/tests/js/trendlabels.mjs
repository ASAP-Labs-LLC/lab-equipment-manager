/* The control chart says which quantity it drew, run against the shipped
 * function in floor.html.
 *
 * Two labels went wrong the moment the limits stopped being self-fitted
 * (3 Sep 2026), and both are the same class of error the chart's own comment
 * warns about: "Draw one and label it the other and the chart says the
 * opposite of what the process is doing."
 *
 * 1. "observed mean" is not the observed mean any more. It is the standard's
 *    assigned value, which came from the certificate and from none of these
 *    results. Printing a certificate figure under the word "observed" on a
 *    record an assessor reads is a false statement about provenance.
 *
 * 2. "The 3s zones reach past the pass band — this equipment's own spread is
 *    wider than the standard allows for" is a claim about the EQUIPMENT. With
 *    certificate limits it is a claim about k and nothing else: the band is
 *    expected +/- k*sigma and the zones are +/- 3*sigma, so for every k below
 *    3 the zones reach past the band ALWAYS, on a perfect instrument, with one
 *    reading or ten thousand. Nine of this lab's twelve methods run at k=2.
 *    Left in, it would have printed a permanent, meaningless warning on almost
 *    every chart in the building.
 */
import fs from 'fs';

const html = fs.readFileSync(new URL('../../templates/floor.html', import.meta.url), 'utf8');
const src = n => { const a = html.indexOf(`function ${n}(`);
  if (a === -1) { console.log(`FAIL: ${n}() not found`); process.exit(1); }
  return html.slice(a, html.indexOf('\n}', a) + 2); };
const deps = `const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));\n`
  + ['trendNum','trendZoneStyle','trendBandStyle'].map(src).filter(Boolean).join('\n') + '\n';
const trendSeriesHtml = new Function(
  `${deps}${src('trendSeriesHtml')}; return trendSeriesHtml;`)();

let fails = 0;
const check = (name, ok) => {
  if (!ok) { fails++; console.log(`  FAIL ${name}`); } else console.log(`  ok   ${name}`);
};
const text = h => h.replace(/<svg[\s\S]*?<\/svg>/g, '')
  .replace(/<[^>]+>/g, ' ').replace(/&#39;/g, "'").replace(/\s+/g, ' ');

// Multitek S on AF26, exactly as /api/machines/<uid>/qc-trend now serves it:
// expected 2.76, certificate sigma 0.34, k=2 so the band IS the 2s zone.
const CERT = {
  test_name: 'ASTM D5453 - Sulfur', sample_id: 'AF26', runs: 1, failures: 0,
  unjudged: 0, superseded: false, limits_source: 'certificate',
  self_fitted: false, zones_within_band: false,
  low: 2.08, high: 3.44, expected: 2.76,
  pass_band: {low: 2.08, high: 3.44, expected: 2.76},
  observed: {mean: 2.76, s: 0.34, n: 0, df: 0, self_fitted: false,
             zones: {'1s': {low: 2.42, high: 3.10},
                     '2s': {low: 2.08, high: 3.44},
                     '3s': {low: 1.74, high: 3.78}}},
  points: [{ts: '2026-09-02T17:06:41', value: 2.875, in_spec: true}],
  violations: [], coverage: {caveat: '', n_operators: 0, n_days: 1,
                             n_calibrations: 0}, spread_basis: 'unknown',
};
const selfFitted = JSON.parse(JSON.stringify(CERT));
selfFitted.limits_source = 'none';
selfFitted.self_fitted = true;
selfFitted.observed.self_fitted = true;
selfFitted.observed.n = 36; selfFitted.observed.df = 35;

console.log('the chart names the quantity it actually drew');

const cert = text(trendSeriesHtml(CERT));
check('a certificate centre is not called the observed mean',
  !/observed mean/.test(cert));
check('it says the value came from the standard',
  /assigned value/.test(cert));

check('no "this equipment\'s own spread is wider" on certificate limits',
  !/own spread is wider than the standard allows/.test(cert));
check('it explains the k relationship instead',
  /k =|k=|the pass band is/.test(cert));

// The self-fitted chart keeps every word it had: this is still the right
// sentence when the points really did write the limits they are judged by.
const own = text(trendSeriesHtml(selfFitted));
check('a self-fitted chart still says "observed mean"',
  /observed mean/.test(own));
check('a self-fitted chart still warns when its spread outgrows the band',
  /own spread is wider than the standard allows/.test(own));

// A retired lot must not read as this instrument's current control.
const retired = JSON.parse(JSON.stringify(CERT));
retired.superseded = true; retired.sample_id = 'AO25';
check('a superseded series is labelled retired',
  /retired standard AO25/.test(text(trendSeriesHtml(retired))));

console.log(fails ? `\n${fails} FAILED` : '\nall passed');
process.exit(fails ? 1 : 0);
