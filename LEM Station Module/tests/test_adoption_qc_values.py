"""Adoption of a QC standard's prints whose verdicts kept no raw reading.

Transfer spec §10.2, and the hole round 2's critic found on Agilent GC 1.

v3.9 logs a QC standard's print as one `qc` verdict row per test, not a
`run`. It keeps `raw_value` only when the SPEC has a correction; otherwise the
row holds `value` alone: the number the verdict judged, written with %g, which
is the raw reading plus the bench's machine-level factor for that test (if
any) at the time it was logged. Every morning restart re-reads everything
after the stored marker, so each print after the marker is logged again on
each restart. On the Agilent's record that comes to 27 verdicts per AF26 test
against about 12 AF26 prints after the marker.

Round 2 matched these rows by a count per (standard, test). Because the
replays inflate that count, a spare verdict always "matched" a new print.
An AF26 standard printed while LabStation was down for the upgrade was then
silently lost on both roads (0 recovered, 0 rows).

What a verdict without a raw reading still says is its VALUE. Where no factor
applied, that is the raw reading itself at %g. Where today's factor applied,
it is the raw plus today's factor. The file holds every raw reading, so the
verdict can be matched on that value exactly. What stays uncertain is a
factor that has changed since the verdict was logged. Such a verdict's value
is then explained by no reading under any factor the bench knows. It is
matched only where the print's OTHER tests pin it (most of them match
exactly), or else, oldest print first, once per DISTINCT such value. A replay
logs the same value again and must not count as another print. The tests
below pin each half of this, on the shape of the real record: five tests, four
of them never factored, one whose factor went 0 -> 0.4 -> 0.7, and restarts
that replay everything after the marker.
"""
import json
from collections import Counter

import lem_station_module as mod

LAB = "AF26"
TESTS = ["IBP", "T10", "T50", "T90", "FBP"]


def af26(i):
    """The i-th AF26 print's raw readings (two decimals, as the GC prints
    them), distinct from every other print's in every test."""
    base = {"IBP": 155.01, "T10": 187.63, "T50": 251.37, "T90": 334.90,
            "FBP": 363.58}
    return {t: "%.2f" % (v + 0.13 * i + 0.01 * k)
            for k, (t, v) in enumerate(base.items())}


def verdict_rows(raws, factors, ts):
    """What v3.9's `_queue_run_events` logs for one print: a qc row per test,
    `value` = the corrected reading at %g, no raw kept (no spec correction)."""
    out = []
    for t in TESTS:
        f = factors.get(t, 0)
        n = mod.corrected_value(raws[t], f) if f else mod._safe_float(raws[t])
        out.append({"ts": ts, "kind": "qc", "lab_id": LAB, "test_name": t,
                    "value": f"{n:g}",
                    "detail": json.dumps({"in_spec": True, "operator": None,
                                          "calibration_id": None})})
    return out


def qc_line(i, raws, offset=None):
    """The file line of an AF26 print, as adoption sees it."""
    return mod.AdoptionLine(
        offset=(i * 100) if offset is None else offset, part=0, pk="af%d" % i,
        lab_id=LAB, run_key=mod.adoption_key(LAB, raws),
        values=tuple(raws.items()))


def floor_record(prints, factor_at, restarts_every=3):
    """v3.9 on the floor: print i logged when it is made, under the factor of
    that moment; a restart every `restarts_every` prints replays every print
    so far (the marker is at the top), under the factor of THAT moment."""
    rows = []
    for i in range(prints):
        rows += verdict_rows(af26(i), factor_at(i), "d%03d-orig" % i)
        if (i + 1) % restarts_every == 0:
            for j in range(i + 1):
                rows += verdict_rows(af26(j), factor_at(i), "d%03d-replay" % i)
    return rows


def factor_history(i):
    """T10's machine-level factor: none, then +0.4 from print 4."""
    return {"T10": 0.4} if i >= 4 else {}


TODAY = {"T10": 0.7}            # and changed again since, in LEM


def plan(lines, rows, corrections=TODAY, fast_lines=0):
    rec = mod.legacy_adoption_counts(rows)
    return mod.plan_adoption(lines, 0, rec.counts, fast_lines=fast_lines,
                             unreadable=rec.unreadable, qc=rec.qc,
                             corrections=corrections)


def test_the_record_is_inflated_as_on_the_floor():
    """The premise: replays leave many more verdicts per test than prints, so
    a count per (standard, test) can never come up short."""
    rows = floor_record(12, factor_history)
    per_test = Counter(r["test_name"] for r in rows)
    assert per_test["IBP"] >= 2 * 12


def test_a_standard_logged_through_restarts_and_a_factor_change_is_all_matched():
    """As-is: every print of the standard reached the record, some under a
    factor that has since changed twice. 0 recovered."""
    rows = floor_record(12, factor_history)
    lines = [qc_line(i, af26(i)) for i in range(12)]
    got = plan(lines, rows)
    assert (got.matched, got.recovered) == (12, [])


def test_a_standard_printed_while_labstation_was_down_is_recovered():
    """The critic's break. One more AF26 print with new numbers, never
    logged. However many spare verdicts the replays left, none of them holds
    THIS print's values, so it is the one recovered. Round 2's count matched
    it and lost it."""
    rows = floor_record(12, factor_history)
    lines = [qc_line(i, af26(i)) for i in range(12)]
    lines.append(qc_line(12, af26(12)))
    got = plan(lines, rows)
    assert got.matched == 12
    assert [l.pk for l in got.recovered] == ["af12"]


def test_the_downtime_print_is_recovered_on_the_fast_path_too():
    """With the fast path on, the newest twenty lines must all match; the
    downtime print does not, so the full match runs and recovers it."""
    rows = floor_record(12, factor_history)
    lines = [qc_line(i, af26(i)) for i in range(13)]
    got = plan(lines, rows, fast_lines=20)
    assert got.kind == "full" and [l.pk for l in got.recovered] == ["af12"]


def test_a_reprocessed_standard_whose_numbers_changed_is_recovered():
    """The GC webapp re-processed an AF26 injection and rewrote its row with
    new numbers. v3.9 never read them, so the record lacks them. Round 2
    reported 0 and listed this as a known limit; it is 1."""
    rows = floor_record(12, factor_history)
    lines = [qc_line(i, af26(i)) for i in range(12)]
    redone = {t: "%.2f" % (float(v) + 0.37) for t, v in af26(7).items()}
    lines[7] = qc_line(7, redone)
    got = plan(lines, rows)
    assert [l.pk for l in got.recovered] == ["af7"]


def test_one_value_in_common_with_an_old_print_does_not_match_a_new_print():
    """Standards repeat, so a new print can share one test's value with an
    old one. A print is recorded when its verdicts are, so one coincidence
    out of five does not count as a match."""
    rows = floor_record(12, factor_history)
    lines = [qc_line(i, af26(i)) for i in range(12)]
    new = dict(af26(12), IBP=af26(3)["IBP"])
    lines.append(qc_line(12, new))
    got = plan(lines, rows)
    assert [l.pk for l in got.recovered] == ["af12"]


def test_a_test_added_to_the_standard_later_does_not_unmatch_older_prints():
    """Prints from before FBP was assigned have four verdicts, not five. They
    are still recorded."""
    rows = []
    for i in range(6):
        made = verdict_rows(af26(i), {}, "t%d" % i)
        rows += [r for r in made if i >= 3 or r["test_name"] != "FBP"]
    lines = [qc_line(i, af26(i)) for i in range(6)]
    got = plan(lines, rows, corrections={})
    assert (got.matched, got.recovered) == (6, [])


def test_a_single_test_standard_whose_factor_changed_is_still_matched():
    """U3's shape. One test, logged once under +0.01; the factor is +0.02
    today. The verdict's value fits no reading under any factor the bench
    knows, and no other test pins it. It is still that standard's verdict,
    so it matches the standard's one print, oldest first."""
    rows = [{"ts": "t0", "kind": "qc", "lab_id": "QC-D", "test_name": "Density",
             "value": "0.86", "detail": "{}"}]
    line = mod.AdoptionLine(offset=0, part=0, pk="q0", lab_id="QC-D",
                            run_key=mod.adoption_key("QC-D", {"Density": "0.85"}),
                            values=(("Density", "0.8500"),))
    got = plan([line], rows, corrections={"Density": 0.02})
    assert (got.matched, got.recovered) == (1, [])


def test_replays_of_one_unexplained_value_vouch_for_one_print_only():
    """The same single-test standard, its one print replayed four times
    under the old factor (four identical verdicts), and a second print made
    while LabStation was down. A replay is the same print, not another one,
    so the four verdicts vouch for the oldest print and the downtime print is
    recovered."""
    rows = [{"ts": "t%d" % k, "kind": "qc", "lab_id": "QC-D",
             "test_name": "Density", "value": "0.86", "detail": "{}"}
            for k in range(4)]

    def line(i, raw):
        return mod.AdoptionLine(offset=i * 20, part=0, pk="q%d" % i,
                                lab_id="QC-D",
                                run_key=mod.adoption_key("QC-D", {"Density": raw}),
                                values=(("Density", raw),))
    got = plan([line(0, "0.8500"), line(1, "0.8420")], rows,
               corrections={"Density": 0.02})
    assert got.matched == 1 and [l.pk for l in got.recovered] == ["q1"]


def test_an_unfactored_single_test_standard_printed_in_downtime_is_recovered():
    """No factor ever: the value IS the raw reading. Six replayed verdicts of
    the old print do not match the new print's value."""
    rows = [{"ts": "t%d" % k, "kind": "qc", "lab_id": "QC-D",
             "test_name": "Density", "value": "0.85", "detail": "{}"}
            for k in range(6)]

    def line(i, raw):
        return mod.AdoptionLine(offset=i * 20, part=0, pk="q%d" % i,
                                lab_id="QC-D",
                                run_key=mod.adoption_key("QC-D", {"Density": raw}),
                                values=(("Density", raw),))
    got = plan([line(0, "0.8500"), line(1, "0.8420")], rows, corrections={})
    assert got.matched == 1 and [l.pk for l in got.recovered] == ["q1"]


def test_both_sides_describe_the_verdicts_identically():
    """The bench reads LabCore's rows itself under a v3.9 server. Under a v4
    server it reads the server's digest. Both must say the same thing about
    the same rows, or the two roads would decide differently."""
    import importlib
    import os
    import sys
    web = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "LEM Web Server")
    sys.path.insert(0, web)
    try:
        bench_api = importlib.import_module("bench_api")
    finally:
        sys.path.remove(web)
    from datetime import datetime
    rows = floor_record(6, factor_history)
    rows.append({"ts": "x", "kind": "qc", "lab_id": "QC-S", "test_name": "Flash",
                 "value": "61", "detail": json.dumps({"raw_value": 64.0,
                                                      "correction": -3.0})})
    digest = bench_api.adoption_digest(rows, datetime(2026, 10, 2))
    assert mod.qc_verdicts_from_digest(digest) == \
        mod.legacy_adoption_counts(rows).qc
