"""Adoption's decision, on lists of lines and multisets of recorded rows.

Transfer spec §10.2. The first time a v4 module starts on a file bench, the
file already holds everything v3.9 read — and v3.9 kept no record of WHICH
lines it read except an offset saved whenever somebody last pressed OK in
Settings (09-23 and 09-24 on the floor today). Read from the top, the file is
a K6-sized replay: A's prototype logged all 30 lines of a fully-logged file
again and sent all 30 cells again. Skip to the end, and a print made while
LabStation was down for the upgrade is lost without a trace.

So the bench asks the record. Every line after the stored offset (the
"boundary") is matched against the uid's recorded rows on (lab_id, RAW
values), by multiset. This file pins the decision itself, with no module, no
file and no LabCore:

  * the key is the RAW reading, so a correction factor changed since the row
    was logged cannot unmatch it (U3);
  * the k-th identical line matches the k-th identical row, so a QC standard
    printed every morning is counted, not collapsed;
  * an unmatched line AFTER a match is a print that never reached the record:
    `recovered` (U2) — and the boundary is itself such a match, because v3.9
    logged everything it read before saving it;
  * an unmatched line BEFORE the first match on a file read from the top is
    pre-LEM history: counted once, never an alarm (U4);
  * when nothing matches at all, the file's age decides: older than LEM's
    first ingest is history, anything else is recovered — listed for a
    person, never silently dropped and never auto-filed.
"""
import hashlib
import json
from collections import Counter

import pytest

import lem_station_module as mod


def key(lab, values):
    return mod.adoption_key(lab, values)


def run_line(i, lab, value, offset=None):
    """A reading line as adoption sees it: where it is, and its keys."""
    return mod.AdoptionLine(offset=i * 20 if offset is None else offset,
                            part=0, pk="pk%d" % i, lab_id=lab,
                            run_key=key(lab, {"Density": value}), qc_keys=())


def header(i):
    return mod.AdoptionLine(offset=i * 20, part=0, pk="pk%d" % i, lab_id="",
                            run_key=None, qc_keys=())


def recorded(*pairs):
    return Counter(key(lab, {"Density": v}) for lab, v in pairs)


# ── the key ──────────────────────────────────────────────────────────────────

def test_the_key_is_lab_id_and_raw_values_with_numbers_compared_as_numbers():
    """The file says "0.8000"; a corrected row's `detail.raw` holds the float
    0.8 that `apply_row_corrections` stored. They are the same reading, and a
    key that compared their spellings would call every corrected line
    unrecorded. Numbers are therefore written in one canonical form."""
    assert key("L-1", {"Density": "0.8000"}) == key("L-1", {"Density": 0.8})
    assert key("L-1", {"Flash": "41"}) == key("L-1", {"Flash": 41.0})
    assert key("L-1", {"Density": "0.8000"}) != key("L-1", {"Density": "0.8001"})
    assert key("L-1", {"Density": "0.8"}) != key("L-2", {"Density": "0.8"})
    assert key("L-1", {"Note": " ok "}) == key("L-1", {"Note": "ok"})


def test_the_recipe_is_the_one_the_server_publishes():
    """The server hashes its recorded rows and the bench hashes its lines;
    the two must be the same function or nothing ever matches. Spelled out
    here byte for byte so a change to either side has to change this."""
    want = hashlib.sha256(json.dumps(
        ["L-1", {"Density": "0.8", "Flash": "41"}], sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:32]
    assert key("L-1", {"Density": "0.8000", "Flash": 41.0}) == want


def test_a_corrected_run_row_is_keyed_on_its_raw_reading():
    """U3's mechanism. v3.9 logged `values` corrected and `raw` beside them.
    The factor has changed since; the line in the file is the raw reading.
    Keyed on raw, the row still matches the line."""
    row = {"kind": "run", "lab_id": "L-1", "test_name": "", "value": "",
           "detail": json.dumps({"values": {"Density": "0.8100",
                                            "Sulfur": "12"},
                                 "raw": {"Density": 0.8},
                                 "corrections": {"Density": 0.01}})}
    assert mod.legacy_row_adoption_key(row) == key(
        "L-1", {"Density": "0.8000", "Sulfur": "12"})


def test_a_qc_row_is_keyed_on_its_test_and_raw_value():
    """A QC standard's print logs one `qc` row per verdict, not a `run`: the
    test name and the raw reading (`raw_value` when the spec corrected it,
    otherwise the value the verdict was made on)."""
    plain = {"kind": "qc", "lab_id": "QC-1", "test_name": "Density",
             "value": "0.8", "detail": json.dumps({"in_spec": True})}
    corrected = {"kind": "qc", "lab_id": "QC-1", "test_name": "Density",
                 "value": "0.81", "detail": {"raw_value": 0.8,
                                             "correction": 0.01}}
    assert mod.legacy_row_adoption_key(plain) == key("QC-1", {"Density": "0.8"})
    assert mod.legacy_row_adoption_key(corrected) == key("QC-1",
                                                         {"Density": "0.8"})


def test_a_row_that_cannot_be_read_is_not_a_key():
    """A detail that is not JSON is not evidence that any line was recorded."""
    assert mod.legacy_row_adoption_key(
        {"kind": "run", "lab_id": "L-1", "detail": "{not json"}) is None


# ── the plan ─────────────────────────────────────────────────────────────────

def test_a_fully_logged_file_is_adopted_on_its_last_twenty_lines():
    """U1's shape and the fast path: every line is in the record, so the
    newest twenty match and the bench adopts at the end of the file without
    classifying the rest. Nothing is recovered, nothing is history."""
    lines = [run_line(i, "L-%d" % i, "0.8") for i in range(30)]
    plan = mod.plan_adoption(lines, 0, recorded(*[("L-%d" % i, "0.8")
                                                  for i in range(30)]))
    assert plan.kind == "fast"
    assert plan.recovered == [] and plan.pre_history == 0
    assert plan.matched == 20 and plan.presumed == 10


def test_a_print_made_during_the_upgrade_is_the_one_recovered():
    """U2: the newest line is not in the record. The fast path fails on it,
    the full match places it after the first matched line — a print that
    never reached the record — and it alone is recovered."""
    lines = [run_line(i, "L-%d" % i, "0.8") for i in range(31)]
    plan = mod.plan_adoption(lines, 0, recorded(*[("L-%d" % i, "0.8")
                                                  for i in range(30)]))
    assert plan.kind == "full"
    assert [l.pk for l in plan.recovered] == ["pk30"]
    assert plan.matched == 30 and plan.pre_history == 0


def test_a_gap_in_the_middle_is_recovered_too():
    lines = [run_line(i, "L-%d" % i, "0.8") for i in range(10)]
    rec = recorded(*[("L-%d" % i, "0.8") for i in range(10) if i != 4])
    plan = mod.plan_adoption(lines, 0, rec)
    assert [l.pk for l in plan.recovered] == ["pk4"]


def test_identical_lines_match_identical_rows_one_for_one():
    """A QC standard printed three times and recorded twice: the k-th line
    matches the k-th row, and the third — the newest — is the one the record
    never got."""
    lines = [run_line(i, "QC-1", "0.8") for i in range(3)]
    plan = mod.plan_adoption(lines, 0, recorded(("QC-1", "0.8"),
                                                ("QC-1", "0.8")))
    assert [l.pk for l in plan.recovered] == ["pk2"]


def test_replayed_rows_do_not_make_a_line_unrecorded():
    """The floor's record holds each Agilent line many times over (the daily
    replays, G1). More rows than lines is still every line recorded."""
    lines = [run_line(i, "L-%d" % i, "0.8") for i in range(25)]
    rec = recorded(*[("L-%d" % i, "0.8") for i in range(25)] * 4)
    plan = mod.plan_adoption(lines, 0, rec)
    assert plan.recovered == [] and plan.matched == 20


def test_lines_older_than_the_first_match_are_history_not_alarms():
    """U4: a file whose first lines predate LEM on this bench. They match
    nothing, and they come before the first line LEM recorded, so they are
    history — one count in the adoption record, never a recovered reading."""
    old = [run_line(i, "OLD-%d" % i, "0.7") for i in range(12)]
    new = [run_line(12 + i, "L-%d" % i, "0.8") for i in range(15)]
    plan = mod.plan_adoption(old + new, 0,
                             recorded(*[("L-%d" % i, "0.8") for i in range(15)]))
    assert plan.kind == "full"
    assert plan.pre_history == 12 and plan.recovered == []
    assert plan.matched == 15


def test_the_fast_path_classifies_nothing_older_than_its_twenty_lines():
    """When the newest twenty lines are all in the record the bench adopts
    at the end of the file (one lookup, §10.2 step 2). What lies before them
    is presumed recorded — not counted as history and not recovered: the
    fast path does not look, and says so in `presumed`."""
    old = [run_line(i, "OLD-%d" % i, "0.7") for i in range(12)]
    new = [run_line(12 + i, "L-%d" % i, "0.8") for i in range(25)]
    plan = mod.plan_adoption(old + new, 0,
                             recorded(*[("L-%d" % i, "0.8") for i in range(25)]))
    assert plan.kind == "fast"
    assert plan.matched == 20 and plan.presumed == 17
    assert plan.pre_history == 0 and plan.recovered == []


def test_history_and_a_late_print_are_told_apart_in_one_file():
    old = [run_line(i, "OLD-%d" % i, "0.7") for i in range(5)]
    mid = [run_line(5 + i, "L-%d" % i, "0.8") for i in range(5)]
    late = [run_line(10, "NEW-1", "0.9")]
    plan = mod.plan_adoption(old + mid + late, 0,
                             recorded(*[("L-%d" % i, "0.8") for i in range(5)]))
    assert plan.pre_history == 5
    assert [l.pk for l in plan.recovered] == ["pk10"]


def test_lines_before_the_boundary_are_presumed_and_never_asked_about():
    """v3.9 logged every line before the offset it saved. Those lines are not
    matched at all — and the boundary itself counts as the record reaching
    that point, so an unmatched line just after it is a missed print, not
    history (otherwise a single print made during the upgrade, on a bench
    whose offset is fresh, would be filed away as history and lost)."""
    lines = [run_line(i, "L-%d" % i, "0.8") for i in range(10)]
    boundary = lines[9].offset            # everything but the last line
    plan = mod.plan_adoption(lines, boundary, Counter())
    assert plan.presumed == 9
    assert [l.pk for l in plan.recovered] == ["pk9"]
    assert plan.pre_history == 0


def test_with_nothing_matching_an_old_file_is_history():
    lines = [run_line(i, "OLD-%d" % i, "0.7") for i in range(4)]
    plan = mod.plan_adoption(lines, 0, recorded(("L-1", "0.8")),
                             file_predates_history=True)
    assert plan.pre_history == 4 and plan.recovered == []


def test_with_nothing_matching_a_newer_file_is_recovered_never_dropped():
    """A rotated file holding only prints made during the upgrade matches
    nothing either. Calling it history would lose real readings; recovered
    keeps them for a person to file."""
    lines = [run_line(i, "NEW-%d" % i, "0.9") for i in range(3)]
    plan = mod.plan_adoption(lines, 0, recorded(("L-1", "0.8")),
                             file_predates_history=False)
    assert plan.pre_history == 0
    assert [l.pk for l in plan.recovered] == ["pk0", "pk1", "pk2"]


def test_a_line_that_is_not_a_reading_is_neither_matched_nor_recovered():
    """A header row has no Lab ID and no values. It is consumed (its key is
    remembered so it never comes back as a reading) and nothing else."""
    lines = [header(0)] + [run_line(i, "L-%d" % i, "0.8") for i in range(1, 4)]
    plan = mod.plan_adoption(lines, 0, recorded(*[("L-%d" % i, "0.8")
                                                  for i in range(1, 4)]))
    assert plan.recovered == [] and plan.other == 1


def test_a_line_whose_lab_id_was_not_asked_about_is_unchecked_not_recovered():
    """Under a v3.9 server the record is asked about at most 15 times
    (§10.2). Lines whose Lab ID did not fit are NOT treated as unrecorded —
    that would turn a read budget into a flood of false recoveries. They are
    counted as unchecked and said in the adoption record."""
    lines = [run_line(i, "L-%d" % i, "0.8") for i in range(6)]
    rec = recorded(*[("L-%d" % i, "0.8") for i in range(3, 6)])
    plan = mod.plan_adoption(lines, 0, rec,
                             asked={"L-3", "L-4", "L-5"})
    assert plan.unchecked == 3 and plan.recovered == []


def test_a_qc_line_matches_its_qc_rows():
    """A QC standard's line carries one key per spec it is a standard for.
    It is recorded when its verdict rows are."""
    qc = mod.AdoptionLine(offset=0, part=0, pk="q", lab_id="QC-1",
                          run_key=key("QC-1", {"Density": "0.8"}),
                          qc_keys=((key("QC-1", {"Density": "0.8"}),),))
    plan = mod.plan_adoption([qc], 0, Counter([key("QC-1", {"Density": "0.8"})]))
    assert plan.matched == 1 and plan.recovered == []
