// The in-place sign-in's pure logic (static/js/signin.js, LEMSignInLogic).
//
// Why these are tested here and not only in a browser: each one is either a
// sentence somebody reads at the moment they are blocked, or a guard.
//
// * The sheet is titled for the act. "Sign in to tick" tells a person at the
//   bench why a box appeared and that their tick is not lost; a bare "Sign in"
//   over a round they just tapped reads as "your tap was thrown away". The
//   button says what will happen next ("Sign in and tick"), because pressing
//   it does both.
// * A wrong password, LEM not answering and LabCore not answering are three
//   different problems with three different fixes. One "Invalid credentials"
//   for all three sends people to retype a password that was right.
// * `/signin?next=` is the no-JS road in. `next` comes off a URL anybody can
//   craft, so `//evil.example` or `https://…` must never become a redirect
//   after a successful sign-in (an open redirect hands the lab's session
//   to a look-alike page).
import fs from 'fs';

const src = fs.readFileSync(new URL('../../static/js/signin.js', import.meta.url), 'utf8');
const root = {};
new Function('window', 'module', src)(root, undefined);
const L = root.LEMSignInLogic;

let fails = 0;
const check = (name, got, want) => {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) { fails++; console.log(`  FAIL ${name}\n    got  ${JSON.stringify(got)}\n    want ${JSON.stringify(want)}`); }
  else console.log(`  ok   ${name}`);
};

if (!L) { console.log('  FAIL signin.js does not define window.LEMSignInLogic'); process.exit(1); }

// ── titled for the act ─────────────────────────────────────────────────────
check('plain sign in', [L.words('').title, L.words('').ok], ['Sign in', 'Sign in']);
check('tick', [L.words('tick').title, L.words('tick').ok], ['Sign in to tick', 'Sign in and tick']);
check('mark done', [L.words('mark done').title, L.words('mark done').ok],
      ['Sign in to mark done', 'Sign in and mark done']);
check('the reason says you stay and the act goes ahead',
      /stay on this page/.test(L.words('tick').why) && /goes ahead/.test(L.words('tick').why), true);
check('plain sign-in promises only that you stay',
      /stay on this page/.test(L.words('').why) && !/goes ahead/.test(L.words('').why), true);
check('switch person names who is signed in now',
      [L.words('', { switching: 'Cody' }).title, /Cody/.test(L.words('', { switching: 'Cody' }).why)],
      ['Switch person', true]);

// ── what a gated control is for ────────────────────────────────────────────
check('data-gated wins', L.actOf({ gated: 'mark done', text: 'Done' }), 'mark done');
check('else the button\'s words, first letter lowered', L.actOf({ gated: '', text: '  Assign QC samples…  ' }),
      'assign QC samples');
check('a bare marker falls back to the words', L.actOf({ gated: 'true', text: 'Mark done' }), 'mark done');
check('nothing to say is a plain sign in', L.actOf({ gated: '', text: '' }), '');
check('a long label is not a title', L.actOf({ gated: '', text: 'x'.repeat(60) }), '');

// ── safe next ──────────────────────────────────────────────────────────────
check('a path is kept', L.safeNext('/checklists?slot=opening#r3'), '/checklists?slot=opening#r3');
check('nothing goes home', L.safeNext(''), '/');
check('null goes home', L.safeNext(null), '/');
check('protocol-relative is refused', L.safeNext('//evil.example/x'), '/');
check('backslash trick is refused', L.safeNext('/\\evil.example'), '/');
check('absolute URL is refused', L.safeNext('https://evil.example/'), '/');
check('javascript: is refused', L.safeNext('javascript:alert(1)'), '/');
check('a control character is refused', L.safeNext('/x\n/y'), '/');
check('back to the sign-in page loops, so go home', L.safeNext('/signin?next=/x'), '/');

// ── three failures, three sentences ────────────────────────────────────────
check('LEM did not answer', L.failureText(0, null),
      'Not signed in: LEM did not answer. Check the connection and try again.');
check('wrong password', L.failureText(401, { error: 'Invalid credentials' }),
      'That user name and password were not accepted.');
check('LabCore unreachable is not a wrong password',
      L.failureText(401, { error: 'Connection error: timed out' }),
      'Not signed in: LabCore did not answer, so the password could not be checked. Try again in a moment.');
check('LabCore not connected either', L.failureText(401, { error: 'LabCore is not connected.' }),
      'Not signed in: LabCore did not answer, so the password could not be checked. Try again in a moment.');
check('LabCore\'s own refusal is passed on', L.failureText(401, { error: 'Account disabled' }),
      'Not signed in: Account disabled');
check('a server error says its status', L.failureText(500, null),
      'Not signed in: LEM answered 500. Try again in a moment.');
check('nothing typed', L.failureText(-1, null), 'Type your user name and password, or tap your card.');

if (fails) { console.log(`${fails} failed`); process.exit(1); }
console.log('signin_logic: all passed');
