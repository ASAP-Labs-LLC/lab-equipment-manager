#!/usr/bin/env python3
"""blind.py — put our render next to the reference with the labels stripped.

A critic that knows which image is ours will find reasons to like it. So the
pairing is done here, by the lead, and the answer key is written somewhere the
critic is never given:

    python3 blind.py pair <id> <ours.png> <reference.jpg>
        → pairs/<id>/A.png and pairs/<id>/B.png, in an order decided by a hash
          of the id, and a line in .keys/<id> saying which is which.

    python3 blind.py reveal <id> <A|B>
        → prints OURS or REFERENCE, and whether that means we won.

The order is derived from the id rather than from randomness so a rerun of the
same round is reproducible — and it is not guessable from the images, which is
all the blinding needs to be. Both sides are rewritten to the same size and the
same format, because a critic can otherwise tell them apart from a JPEG's
artefacts alone.
"""
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAIRS = os.path.join(ROOT, 'pairs')
KEYS = os.path.join(ROOT, '.keys')


def _normalise(src, dst, width=1440):
    """Same size, same format, no metadata — so nothing but the picture
    distinguishes them."""
    subprocess.run(['sips', '-s', 'format', 'png', '-Z', str(width),
                    src, '--out', dst], capture_output=True, check=True)


WORLD = os.path.expanduser("~/LAB-lem/LEM Web Server/static/world")


def _fresh_enough(ours):
    """Refuse to judge a screenshot older than the code it is meant to show.

    Round three was judged on shots captured at 11:44 against a rail.js last
    written at 13:48 — so four critics carefully described track that had been
    rebuilt two hours earlier, and the verdicts measured nothing. A stale
    artefact produces confident, detailed, worthless findings, which is worse
    than no findings at all.
    """
    try:
        shot = os.path.getmtime(ours)
    except OSError:
        return "cannot read %s" % ours
    newest, newest_name = 0, ""
    for name in os.listdir(WORLD):
        if not name.endswith(".js"):
            continue
        t = os.path.getmtime(os.path.join(WORLD, name))
        if t > newest:
            newest, newest_name = t, name
    if newest > shot:
        return ("%s was captured %d minutes BEFORE %s was last changed — "
                "re-shoot before judging" %
                (os.path.basename(ours), (newest - shot) / 60, newest_name))
    return None


def pair(pair_id, ours, ref):
    stale = _fresh_enough(ours)
    if stale:
        sys.exit("REFUSED: " + stale)
    out = os.path.join(PAIRS, pair_id)
    os.makedirs(out, exist_ok=True)
    os.makedirs(KEYS, exist_ok=True)
    flip = hashlib.sha1(pair_id.encode()).digest()[0] & 1
    a_src, b_src = (ref, ours) if flip else (ours, ref)
    for name, src in (('A.png', a_src), ('B.png', b_src)):
        path = os.path.join(out, name)
        if os.path.exists(path):
            os.chmod(path, 0o644)
        _normalise(src, path)
        # Read-only once written. In round two a critic reported that one of the
        # two images had been replaced mid-review by a 1x1 grey placeholder —
        # `sips` rewrites in place unless given --out, so any tool run against
        # the pair can silently destroy the thing being judged, and the verdict
        # still comes back looking authoritative. A judged artefact must not be
        # writable by the judge.
        os.chmod(path, 0o444)
    with open(os.path.join(KEYS, pair_id), 'w') as fh:
        fh.write('A=%s\nB=%s\nours=%s\nref=%s\n'
                 % ('reference' if flip else 'ours',
                    'ours' if flip else 'reference', ours, ref))
    print(out)


def _check(pair_id):
    """A verdict on a corrupted image is worse than no verdict: it reads as
    evidence. Refuse to report one."""
    for name in ('A.png', 'B.png'):
        path = os.path.join(PAIRS, pair_id, name)
        if not os.path.exists(path) or os.path.getsize(path) < 50_000:
            print('!! %s/%s is missing or truncated — this pair was judged '
                  'against a damaged image and the verdict is void' %
                  (pair_id, name))
            return False
    return True


def reveal(pair_id, choice):
    _check(pair_id)
    key = os.path.join(KEYS, pair_id)
    if not os.path.exists(key):
        sys.exit('no key for ' + pair_id)
    fields = dict(line.split('=', 1) for line in
                  open(key).read().strip().split('\n'))
    picked = fields.get(choice.strip().upper(), '?')
    print('%s → %s   (%s)' % (choice.upper(), picked.upper(),
                              'WE WIN' if picked == 'ours' else 'we lose'))


if __name__ == '__main__':
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    if sys.argv[1] == 'pair':
        pair(sys.argv[2], sys.argv[3], sys.argv[4])
    elif sys.argv[1] == 'reveal':
        reveal(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
