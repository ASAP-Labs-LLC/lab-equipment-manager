"""expectations.json: every §9 scenario, and today's numbers copied, not typed.

The v3.9 rows of the 29 baseline scenarios and W1–W4 must be phase 1's
faults.json / web.json VERBATIM — a row hand-edited to match a drifted
harness would make the drift check meaningless, so this test compares them
field by field with the files phase 1 wrote. The v4 rows are spot-checked
against the §9 tables, including the bounds §9 writes as "≤".
"""
import json
import os

from gharness.target import BASELINE

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXP = json.load(open(os.path.join(HERE, "expectations.json"), encoding="utf-8"))["scenarios"]

SPEC_9 = ("S0 K1 K2 K3 K4 K5 K6 K7 K8 K9 K9c K9r N1 N2 N3 N4 N5 N6 F1 F2 F3 F4 F5 "
          "R1 R2 R3 R4 R5 R6 "
          "K1b K7b K8r R6deep X1 X2 X3 X3r X4 R7p Q1 A1 A1w A3 A4 A5 B1 E0 E1 E2 E3 "
          "T1 T2 T3 T4 T4b T5 D1 CF1 CF2 L1 L2 U1 U2 U3 U4 U5 M1 M2 M3 M4 M5 M6 "
          "DG1 DG2 W1 W2 W2b W3 W4").split()
# Beyond §9: phase-1 F1 and N2 re-run on the legacy road (a server answering
# 404), added with P8 so the LabCore log drain a v4 bench still uses against
# an old server keeps its guards (two P0 mutations of it would survive else).
LEGACY_REPLAYS = ["F1L", "N2L"]
# Beyond §9, added with P8 round 2 from the critic's two gaps: CF2 across a
# restart, and a bench whose v2 state is unknown (roads down / 503) — only a
# 404 is an old server.
ROUND_2 = ["CF2r", "N404", "N503"]
# Round 3: T2 with lost answers, and the store asked directly whether it
# refuses a (uid, epoch, seq) it holds (unique_seq_off had no row to fail).
ROUND_3 = ["T2L"]

BASELINE_19 = set("K1 K2 K3 K4 K5 K6 K7 K8 K9 N3 N4 F3 F4 F5 R2 R3 R4 R5 R6".split())


#: Rows the harness adds beyond §9, each with the reason in its own v4_spec.
#: A1j: A1 with the journal lost — the guard read alone (A1 cannot show it:
#: the journal stops the re-read before the guard is asked).
HARNESS_ONLY = ["A1j"]


def test_every_section_9_scenario_has_both_rows():
    assert sorted(EXP) == sorted(SPEC_9 + LEGACY_REPLAYS + ROUND_2
                                 + ROUND_3 + HARNESS_ONLY)
    for sid, row in EXP.items():
        assert "v3.9" in row and "v4" in row and row["v4"], sid
        assert row.get("v4_spec"), sid


def test_baseline_v39_rows_are_faults_json_verbatim():
    faults = json.load(open(os.path.join(BASELINE, "faults.json")))
    assert len(faults) == 29
    for rec in faults:
        sid = rec["scenario"].split()[0]
        row = EXP[sid]["v3.9"]
        for k, v in rec.items():
            assert row[k] == v, (sid, k)


def test_web_v39_rows_are_web_json_verbatim():
    web = json.load(open(os.path.join(BASELINE, "web.json")))
    names = {"W1": "W1_snapshot", "W2": "W2_correction_lost_response",
             "W3": "W3_api_status_cost", "W4": "W4_log_mirror_resume"}
    for sid, name in names.items():
        assert EXP[sid]["v3.9"] == web[name], sid
    assert EXP["W1"]["v3.9"]["after_one_watchdog"]["extra_ops_vs_healthy"] == 426.0
    assert EXP["W4"]["volatile"] == ["state.filled_at"]


def test_the_19_baseline_scenarios_v4_rejects_are_exactly_the_ones_today_misses():
    from gharness.expect import compare
    failing = {sid for sid in SPEC_9[:29]
               if compare(EXP[sid]["v4"], EXP[sid]["v3.9"])}
    assert failing == BASELINE_19


def test_bounds_only_where_section_9_writes_them():
    assert EXP["F2"]["v4"]["cell_dup_sends"] == {"le": 3}
    assert EXP["F3"]["v4"]["cell_dup_sends"] == {"le": 60}
    assert EXP["F4"]["v4"]["cell_dup_sends"] == {"le": 120}
    assert EXP["F5"]["v4"]["cell_dup_sends"] == {"le": 250}
    assert EXP["E1"]["v4"]["labcore_ops_per_filing_poll"] == {"le": 2}
    for sid in ("K1", "K2", "R2", "R4", "K8r", "X3r", "R7p", "A5"):
        for v in EXP[sid]["v4"].values():
            assert not isinstance(v, dict), (sid, v)


def test_stated_residuals_are_exact_not_zero():
    assert (EXP["K8r"]["v4"]["lost"], EXP["K8r"]["v4"]["dup"]) == (1, 0)
    assert EXP["R7p"]["v4"]["lost"] == 1
    assert EXP["A5"]["v4"]["analyst_overwritten"] == 1
    assert (EXP["X3r"]["v4"]["dup"], EXP["X3r"]["v4"]["labelled_dup"]) == (3, 3)


def test_todays_numbers_the_spec_quotes_are_the_measured_ones():
    # §9.2 quotes "today" for some new scenarios; the measured v3.9 rows agree.
    assert EXP["A1"]["v3.9"]["analyst_overwritten"] == 5
    assert EXP["A3"]["v3.9"]["analyst_overwritten"] == 3
    assert EXP["E0"]["v3.9"]["idle_labcore_ops_per_min"] == 4.855      # "4.86/min"
    assert EXP["E1"]["v3.9"]["extra_ops_per_filing_poll"] == 3.0       # "+3"
    assert (EXP["E2"]["v3.9"]["first_poll_reads"],
            EXP["E2"]["v3.9"]["first_poll_writes"]) == (8, 15)
