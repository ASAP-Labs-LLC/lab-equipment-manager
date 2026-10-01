"""The ground-truth print ledger and the §9.4 tally.

The world knows what the instrument really printed; the store knows what LEM
kept. The tally is the difference, and nothing else:

    lost         = Σ_k max(0, truth[k] − stored_effective[k])
    dup          = Σ_k max(0, stored_effective[k] − truth[k])
    labelled_dup = Σ_k min(that key's dup, rows of k labelled ambiguous_repeat)

over keys k = (lab_id, raw value), multisets on both sides. Every `emit` of a
genuine print registers one truth entry. Rewrites, trims and rotations register
NOTHING — they are the file changing, not the instrument printing. A genuine
repeat (the same line printed again: a QC standard, X1's second L1) registers
again, which is exactly why the key is a multiset and not a set.

Forbidden here, and caught by mutation T0 (delete one stored row → the gate
must go red): any term multiplied by 0, any count zeroed by hand, any "skip
this scenario's lost". The functions below have no switches for that.

Results are tallied against the LATEST truth per (lab_id, test): the last
genuine print, unless a person typed the cell after it (`analyst_set`), in
which case the person's value is the truth — LEM writing over it is the
`analyst_overwritten` failure, and the cell also counts as `res_wrong`.
"""
from collections import Counter

AMBIGUOUS = "ambiguous_repeat"


class Ledger:
    def __init__(self, test="Density"):
        self.test = test
        self.prints = []           # (truth_id, lab_id, value)
        self._latest = {}          # lab_id -> value of the last genuine print
        self._analyst = {}         # lab_id -> value a person typed after it

    def register(self, lab_id, value):
        tid = len(self.prints)
        self.prints.append((tid, str(lab_id), str(value)))
        self._latest[str(lab_id)] = str(value)
        self._analyst.pop(str(lab_id), None)
        return tid

    def analyst_set(self, lab_id, value):
        self._analyst[str(lab_id)] = str(value)

    def truth(self):
        return Counter((lab, val) for _tid, lab, val in self.prints)

    def labs(self):
        return set(self._latest)

    def expected_cell(self, lab_id):
        lab_id = str(lab_id)
        if lab_id in self._analyst:
            return self._analyst[lab_id]
        return self._latest.get(lab_id)

    def __len__(self):
        return len(self.prints)


def tally(truth, rows):
    """`rows`: iterable of dicts {lab_id, value, labels (set), hidden (bool)}.
    Hidden rows (marked duplicates the effective view excludes) are not
    stored_effective. Returns lost / dup / labelled_dup / labelled_rows /
    stored / truth."""
    stored = Counter()
    labelled = Counter()
    for r in rows:
        if r.get("hidden"):
            continue
        k = (str(r["lab_id"]), str(r["value"]))
        stored[k] += 1
        if AMBIGUOUS in (r.get("labels") or ()):
            labelled[k] += 1
    keys = set(truth) | set(stored)
    lost = sum(max(0, truth[k] - stored[k]) for k in keys)
    dup = sum(max(0, stored[k] - truth[k]) for k in keys)
    labelled_dup = sum(min(max(0, stored[k] - truth[k]), labelled[k]) for k in keys)
    return {"lost": lost, "dup": dup, "labelled_dup": labelled_dup,
            "labelled_rows": sum(labelled.values()),
            "stored": sum(stored.values()), "truth": sum(truth.values())}


def results(ledger, cells):
    """`cells`: {lab_id: value in sample_tests}. Returns res_lost / res_wrong
    over every lab the instrument printed."""
    lost = wrong = 0
    for lab in ledger.labs():
        want = ledger.expected_cell(lab)
        got = cells.get(lab)
        if got in (None, ""):
            lost += 1
        elif str(got) != str(want):
            wrong += 1
    return {"res_lost": lost, "res_wrong": wrong}
