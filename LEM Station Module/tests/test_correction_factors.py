"""Correction factors — the offset a bench needs to agree with the reference.

Ryan asked where his correction factor was. Straight answer, established
2026-08-03: it never existed. V5 carried a dead `correction_factor_dir` config
field, and **V4 stored and logged correction factors but never applied one to a
single reading** — nothing in V4's evaluation engine ever read `correction_value`.
So the number could be set, saved and audited while every verdict ignored it.

Decided with Ryan: an **additive offset**, `corrected = raw + correction`, default
0.0. That is what V4's field shape implied (`correction_value`, default 0.0 — a
0.0 default is meaningless for a multiplier) and it is what a bench-vs-reference
adjustment usually is.

Applied at exactly one point: where a parsed value becomes a verdict. From there
the corrected number flows to the pass/fail decision, the front view, the specs
published to the floor, and the QC log — while the RAW value is kept alongside it,
because a log that only records the corrected number cannot be audited.
"""
from datetime import datetime, timedelta

import pytest

import lem_station_module as mod
from lem_station_module import LAB_ID_KEY, Machine, TestSpec

NOW = datetime(2026, 8, 3, 18, 0)


def spec(**over):
    base = dict(name="Flash", value_col="Flash", expected=63.72, std_dev=1.05,
                k=2.0, units="C", sample_id="AO25")
    base.update(over)
    return TestSpec(**base)


def machine(**over):
    base = dict(uid="m1", title="PAC Flash 1", tests=[spec()])
    base.update(over)
    return Machine(**base)


def row(value, lab_id="AO25"):
    return {LAB_ID_KEY: lab_id, "Flash": str(value),
            "parsed_date": "2026-08-03", "parsed_time": "17:50:00"}


def evaluate(m, rows, now=None):
    """Through the real pipeline: correct at the parse boundary, then judge.

    Updated 2026-08-04. These tests used to hand uncorrected rows straight to
    `evaluate_machine` and expect it to apply the offset — which is exactly the
    design that corrected the QC standard and left every customer sample raw. The
    correction now happens once, where the parser produces rows, so a test that
    skips that step is testing a path that no longer exists.
    """
    corrections = m.corrections or {t.name: t.correction for t in m.tests
                                    if t.correction}
    mod.apply_corrections(m, corrections)
    return mod.evaluate_machine(m, mod.apply_row_corrections(rows, m.corrections),
                                now or NOW)


# ── the offset itself ───────────────────────────────────────────────────────

class TestTheOffsetIsApplied:
    def test_zero_changes_nothing(self):
        ev = mod.evaluate_machine(machine(), [row(65.0)], NOW)
        assert ev.test_results[0].value == pytest.approx(65.0)

    def test_a_positive_offset_is_added(self):
        m = machine(tests=[spec(correction=0.5)])
        ev = evaluate(m, [row(65.0)])
        assert ev.test_results[0].value == pytest.approx(65.5)

    def test_a_negative_offset_is_subtracted(self):
        m = machine(tests=[spec(correction=-1.2)])
        ev = evaluate(m, [row(65.0)])
        assert ev.test_results[0].value == pytest.approx(63.8)

    def test_the_raw_reading_is_kept(self):
        """A log that only holds the corrected number cannot be audited."""
        m = machine(tests=[spec(correction=0.5)])
        ev = mod.evaluate_machine(m, [row(65.0)], NOW)
        assert ev.test_results[0].raw_value == pytest.approx(65.0)

    def test_it_is_applied_to_EVERY_test_not_just_the_first(self):
        m = machine(tests=[spec(name="A", value_col="A", correction=1.0),
                           spec(name="B", value_col="B", correction=-2.0)])
        r = {LAB_ID_KEY: "AO25", "A": "10", "B": "20",
             "parsed_date": "2026-08-03", "parsed_time": "17:50:00"}
        ev = evaluate(m, [r])
        by = {t.name: t.value for t in ev.test_results}
        assert by["A"] == pytest.approx(11.0)
        assert by["B"] == pytest.approx(18.0)


# ── it changes the verdict, which is the point ──────────────────────────────

class TestItDecidesPassFail:
    def test_a_correction_can_bring_a_reading_into_spec(self):
        """66.5 is above the 65.82 limit; -1.0 brings it to 65.5, inside."""
        m = machine(tests=[spec(correction=-1.0)])
        ev = evaluate(m, [row(66.5)])
        assert ev.test_results[0].in_spec is True
        assert ev.status == mod.STATUS_GREEN

    def test_a_correction_can_push_a_reading_out_of_spec(self):
        """Cuts both ways, and silently hiding that would be worse."""
        m = machine(tests=[spec(correction=2.0)])
        ev = evaluate(m, [row(65.0)])
        assert ev.test_results[0].in_spec is False
        assert ev.status == mod.STATUS_RED

    def test_the_limits_themselves_are_not_moved(self):
        """The correction adjusts the READING, not the standard's band — moving
        the band instead would misreport what the standard actually is."""
        low, high = mod.spec_band(spec(correction=5.0))
        assert (round(low, 2), round(high, 2)) == (61.62, 65.82)


# ── storage in LabCore ─────────────────────────────────────────────────────

class TestStorage:
    def test_the_table_is_declared_with_a_zero_default(self):
        assert "lem_correction_factors" in mod.CORRECTIONS_DDL
        assert "DEFAULT 0" in mod.CORRECTIONS_DDL.replace("DEFAULT 0.0", "DEFAULT 0")

    def test_it_is_read_per_machine(self):
        sql, args = mod.build_corrections_query("m1")
        assert args == ["m1"]
        assert "lem_correction_factors" in sql

    def test_rows_become_a_lookup(self):
        got = mod.parse_correction_rows([
            {"test_name": "Flash", "correction": "0.5"},
            {"test_name": "Sulfur", "correction": -0.2}])
        assert got == {"Flash": 0.5, "Sulfur": -0.2}

    def test_junk_is_ignored_rather_than_crashing_the_poll(self):
        got = mod.parse_correction_rows([
            {"test_name": "Flash", "correction": "not a number"},
            {"test_name": "", "correction": "1.0"},
            None, "junk",
            {"test_name": "Good", "correction": "2.5"}])
        assert got == {"Good": 2.5}

    def test_they_are_stamped_onto_the_specs(self):
        m = machine(tests=[spec(name="Flash"), spec(name="Sulfur",
                                                    value_col="Sulfur")])
        mod.apply_corrections(m, {"Flash": 0.5})
        by = {s.name: s.correction for s in m.tests}
        assert by["Flash"] == pytest.approx(0.5)
        assert by["Sulfur"] == pytest.approx(0.0)

    def test_removing_one_resets_it_to_zero(self):
        """Deleting a correction must actually stop correcting."""
        m = machine(tests=[spec(correction=0.5)])
        mod.apply_corrections(m, {})
        assert m.tests[0].correction == pytest.approx(0.0)

    def test_a_correction_survives_a_spec_refresh(self):
        """Specs are rebuilt from LabCore every sync with blank corrections."""
        fresh = [spec(name="Flash")]
        mod.carry_last_qc(fresh, [spec(name="Flash", correction=0.5)])
        assert fresh[0].correction == pytest.approx(0.5)


# ── it is visible and auditable ────────────────────────────────────────────

class TestVisibleAndLogged:
    def test_the_front_view_shows_it(self):
        assert "+0.50" in mod.limits_text(spec(correction=0.5))
        assert "-1.20" in mod.limits_text(spec(correction=-1.2))

    def test_no_correction_adds_no_clutter(self):
        assert mod.limits_text(spec()) == "61.62 – 65.82 C"

    def test_the_published_spec_carries_it(self):
        m = machine(tests=[spec(correction=0.5)])
        _delete, (sql, args) = mod.build_effective_specs_publish(m, NOW)
        assert "correction" in sql
        assert 0.5 in args

    def test_a_qc_verdict_records_raw_correction_and_corrected(self):
        detail = mod.qc_log_detail(spec(correction=0.5), raw=65.0, corrected=65.5)
        assert detail["raw_value"] == pytest.approx(65.0)
        assert detail["correction"] == pytest.approx(0.5)
        assert detail["in_spec"] is True
        assert detail["low"] == pytest.approx(61.62)

    def test_the_detail_omits_the_correction_when_there_is_none(self):
        detail = mod.qc_log_detail(spec(), raw=65.0, corrected=65.0)
        assert "correction" not in detail


# ── editing them from the module's own settings ──────────────────────────────

class TestSettingsEditor:
    """Ryan asked for corrections in three places: right-click in the web server,
    the module's settings, and visible on the module front view. This is the
    settings half — a bench tech adjusting their own instrument should not have to
    go to the master view to do it."""

    def test_the_write_is_an_upsert_per_test(self):
        sql, args = mod.build_correction_upsert("m1", "Flash", 0.5, "C",
                                                NOW, "kaden")
        assert "lem_correction_factors" in sql
        assert "ON CONFLICT" in sql.upper()
        assert args[:4] == ["m1", "Flash", 0.5, "C"]

    def test_who_and_when_are_recorded(self):
        _sql, args = mod.build_correction_upsert("m1", "Flash", 0.5, "C",
                                                 NOW, "kaden")
        assert "kaden" in args
        assert NOW.isoformat() in args

    def test_zero_is_written_not_skipped(self):
        """Setting 0 explicitly is a decision; it must overwrite an old offset."""
        _sql, args = mod.build_correction_upsert("m1", "Flash", 0.0, "", NOW, "k")
        assert 0.0 in args

    def test_clearing_one_deletes_the_row(self):
        sql, args = mod.build_correction_delete("m1", "Flash")
        assert sql.strip().upper().startswith("DELETE")
        assert args == ["m1", "Flash"]

    def test_a_blank_entry_reads_as_no_correction(self):
        """An operator clearing the box means "none", not a crash."""
        assert mod.parse_correction_input("") == 0.0
        assert mod.parse_correction_input("   ") == 0.0

    def test_a_signed_number_is_accepted(self):
        assert mod.parse_correction_input("+0.5") == pytest.approx(0.5)
        assert mod.parse_correction_input("-1.25") == pytest.approx(-1.25)

    def test_junk_is_refused_rather_than_silently_zeroed(self):
        with pytest.raises(ValueError):
            mod.parse_correction_input("about half")

    def test_the_dialog_offers_a_row_per_reported_method(self):
        """Reads `machine.corrections`, which is authoritative — the copy on each
        spec is for display only, and only QC-assigned tests have one at all."""
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QtWidgets = pytest.importorskip("PySide6.QtWidgets")
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        m = machine(tests=[spec(name="Flash"), spec(name="Sulfur",
                                                    value_col="Sulfur")])
        mod.apply_corrections(m, {"Flash": 0.5})
        dlg = mod._CorrectionsDialog(m, None)
        assert dlg.rows_for_test("Flash") == "0.5"
        assert dlg.rows_for_test("Sulfur") == "0"

    def test_the_dialog_collects_what_changed(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QtWidgets = pytest.importorskip("PySide6.QtWidgets")
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        m = machine(tests=[spec(name="Flash")])
        mod.apply_corrections(m, {"Flash": 0.5})
        dlg = mod._CorrectionsDialog(m, None)
        dlg.set_row("Flash", "0.9")
        assert dlg.collect() == {"Flash": pytest.approx(0.9)}

    def test_an_unchanged_dialog_writes_nothing(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QtWidgets = pytest.importorskip("PySide6.QtWidgets")
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        m = machine(tests=[spec(name="Flash")])
        mod.apply_corrections(m, {"Flash": 0.5})
        assert mod._CorrectionsDialog(m, None).collect() == {}


class TestReachableFromSettings:
    """Where the entry point lives, and where it must NOT.

    First attempt turned ⚙ into a popup menu ("Parser settings…" / "Correction
    factors…"). That broke three existing tests which pin that ⚙ opens the parser
    dialog directly, and worse, an InstantPopup menu blocks on a modal popup the
    moment anything clicks it — the suite hung rather than failed. The entry lives
    inside the settings dialog instead, which is what "in the settings" meant.
    """

    def qt(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        QtWidgets = pytest.importorskip("PySide6.QtWidgets")
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_the_settings_dialog_has_the_button(self, tmp_path):
        self.qt()
        m = machine()
        m.csv_path = str(tmp_path / "x.csv")
        dlg = mod._MachineDialog(m, None, on_corrections=lambda _m: None)
        assert dlg.corrections_button.isEnabled()

    def test_it_hands_off_to_the_module(self, tmp_path):
        self.qt()
        seen = []
        m = machine()
        dlg = mod._MachineDialog(m, None, on_corrections=seen.append)
        dlg.corrections_button.click()
        assert seen == [m]

    def test_it_is_disabled_with_no_handler_rather_than_dead(self, tmp_path):
        """A button that looks live and does nothing is worse than a greyed one."""
        self.qt()
        dlg = mod._MachineDialog(machine(), None)
        assert not dlg.corrections_button.isEnabled()

    def test_the_gear_still_opens_the_parser_dialog_directly(self):
        """No popup menu on ⚙ — a plain clicked() connection."""
        import inspect
        src = inspect.getsource(mod._MachineCard.__init__)
        gear = src[src.index("self.settings_button"):]
        assert "clicked.connect" in gear
        assert "InstantPopup" not in gear.split("header.addWidget")[0]


class TestFromThisPointOn:
    """Ryan, 2026-08-03: "you can still apply a correction factor before it parses
    anything and then it should just apply it going forward (aka from this point on
    correct 0.2+)".

    Two guarantees. It can be set on a test that has never produced a reading, and
    it applies to readings from then on — it does **not** reach back and restate
    what was already measured and logged. A correction that silently rewrote
    history would make the QC record unauditable.
    """

    def test_it_can_be_set_before_anything_has_been_parsed(self):
        m = machine(tests=[spec()])
        assert m.tests[0].last_qc_at == ""          # never run
        mod.apply_corrections(m, {"Flash": 0.2})
        assert m.tests[0].correction == pytest.approx(0.2)

    def test_the_front_view_shows_it_before_any_reading(self):
        m = machine(tests=[spec()])
        mod.apply_corrections(m, {"Flash": 0.2})
        assert "+0.20" in mod.limits_text(m.tests[0])

    def test_it_is_published_before_any_reading(self):
        """So the floor shows the pending correction too."""
        m = machine(tests=[spec()])
        mod.apply_corrections(m, {"Flash": 0.2})
        _delete, (_sql, args) = mod.build_effective_specs_publish(m, NOW)
        assert pytest.approx(0.2) in args

    def test_the_next_reading_is_corrected(self):
        m = machine(tests=[spec()])
        mod.apply_corrections(m, {"Flash": 0.2})
        ev = evaluate(m, [row(65.0)])
        assert ev.test_results[0].value == pytest.approx(65.2)
        assert ev.test_results[0].raw_value == pytest.approx(65.0)

    def test_a_remembered_verdict_is_NOT_restated(self):
        """The verdict from before the correction stays as it was recorded. It was
        measured and judged under the old rule, and rewriting it would mean the
        log and the screen disagreed about the same run."""
        earlier = (NOW - timedelta(hours=2)).isoformat()
        m = machine(tests=[spec(last_qc_at=earlier, last_qc_value=65.0,
                                last_qc_in_spec=True)])
        mod.apply_corrections(m, {"Flash": 0.2})
        assert m.tests[0].last_qc_value == pytest.approx(65.0)
        assert m.tests[0].last_qc_at == earlier

    def test_the_machine_stays_green_across_the_change(self):
        """Setting a correction must not knock a passing bench off green."""
        m = machine(tests=[spec(last_qc_at=(NOW - timedelta(hours=2)).isoformat(),
                                last_qc_value=65.0, last_qc_in_spec=True)])
        mod.apply_corrections(m, {"Flash": 0.2})
        assert mod.evaluate_machine(m, [], NOW).status == mod.STATUS_GREEN

    def test_an_already_logged_verdict_keeps_its_own_correction(self):
        """Each log row records the offset in force when it was made, so history
        stays readable after the offset changes."""
        before = mod.qc_log_detail(spec(), raw=65.0, corrected=65.0)
        after = mod.qc_log_detail(spec(correction=0.2), raw=65.0, corrected=65.2)
        assert "correction" not in before
        assert after["correction"] == pytest.approx(0.2)
        assert after["raw_value"] == pytest.approx(65.0)


class TestPastedMinusSigns:
    """PAC Flash 2's real correction is -3.0, so negatives are routine. A plain
    hyphen always worked; a Unicode minus (U+2212) or a dash — what you get pasting
    from a document or a chat — looked identical and `float()` refused it."""

    @pytest.mark.parametrize("typed", ["-3.0", "-3", " -3.0 ", "-3.00"])
    def test_a_plain_negative_parses(self, typed):
        assert mod.parse_correction_input(typed) == pytest.approx(-3.0)

    @pytest.mark.parametrize("typed", ["−3.0", "–3.0", "—3.0"])
    def test_a_pasted_minus_parses(self, typed):
        assert mod.parse_correction_input(typed) == pytest.approx(-3.0)

    def test_a_non_breaking_space_is_tolerated(self):
        assert mod.parse_correction_input(" -3.0 ") == pytest.approx(-3.0)

    def test_the_two_real_values_parse(self):
        assert mod.parse_correction_input("-3.0") == pytest.approx(-3.0)
        assert mod.parse_correction_input("1.45") == pytest.approx(1.45)

    def test_a_negative_correction_actually_lowers_the_reading(self):
        """Flash 2's -3.0 on a raw 66.5 has to land at 63.5."""
        m = machine(tests=[spec(correction=-3.0)])
        ev = evaluate(m, [row(66.5)])
        assert ev.test_results[0].value == pytest.approx(63.5)
        assert ev.test_results[0].raw_value == pytest.approx(66.5)

    def test_junk_is_still_refused(self):
        for bad in ("about half", "-", "--3", "3-"):
            with pytest.raises(ValueError):
                mod.parse_correction_input(bad)


# ── the stored number is a lab result, not a binary artefact ────────────────

class TestTheCorrectedValueDoesNotGrowDigits:
    """Reported from the floor 2026-08-13: sulfur results "infinitely
    extending". Lab IDs 37712 and 37709.

    `corrected = raw + correction` was plain binary float addition, so a
    reading the instrument printed to four decimals came back with seventeen:
    0.0015 + -0.0003 == 0.0012000000000000001, and `str()` of that is what was
    written to LabCore and shown on the floor. Sulfur is where it surfaced
    because those readings sit around 0.001-0.05, small enough that the
    representation error lands inside the digits somebody reads.

    The result must carry the precision of the reading and the offset — no
    more. ISO/IEC 17025 §7.8.2 asks for the measurement result; seventeen
    significant figures on a four-figure reading is not one.
    """

    @pytest.mark.parametrize("raw,offset,expected", [
        ("0.0015", -0.0003, "0.0012"),      # the reported case
        ("0.1", 0.2, "0.3"),
        ("2.675", 0.01, "2.685"),
        ("0.0067", 0.0009, "0.0076"),
        ("62.0", -3.0, "59.0"),            # the module's own worked example
        ("37.71", -0.29, "37.42"),
        ("0.0453", -0.0021, "0.0432"),
    ])
    def test_the_stored_value_reads_like_the_instrument_printed_it(
            self, raw, offset, expected):
        out = mod.apply_row_corrections(
            [{LAB_ID_KEY: "37712", "Sulfur": raw}], {"Sulfur": offset})
        assert str(out[0]["Sulfur"]) == expected

    def test_a_long_tail_never_reaches_labcore(self):
        """The op actually sent carries the same string, because the write
        stringifies whatever the row holds."""
        out = mod.apply_row_corrections(
            [{LAB_ID_KEY: "37709", "Sulfur": "0.0015"}], {"Sulfur": -0.0003})
        assert "0.0012000000000000001" not in str(out[0]["Sulfur"])
        assert len(str(out[0]["Sulfur"])) <= 8, str(out[0]["Sulfur"])

    def test_the_value_is_still_a_number_downstream(self):
        """Nothing downstream may have to learn a new type: the QC band
        comparison, `_safe_float` and the CSV all take a float today."""
        out = mod.apply_row_corrections(
            [{LAB_ID_KEY: "37712", "Sulfur": "0.0015"}], {"Sulfur": -0.0003})
        value = out[0]["Sulfur"]
        assert isinstance(value, float)
        assert 0.0 <= value <= 1.0

    def test_the_raw_reading_is_still_recorded_exactly(self):
        """§7.5.1 — the record must reconstruct the measurement, so rounding
        the REPORTED value must not quietly rewrite what the bench read."""
        out = mod.apply_row_corrections(
            [{LAB_ID_KEY: "37712", "Sulfur": "0.0015"}], {"Sulfur": -0.0003})
        assert out[0][mod.RAW_KEY]["Sulfur"] == 0.0015
        assert out[0][mod.CORRECTION_KEY]["Sulfur"] == -0.0003

    def test_a_reading_the_parser_gave_in_scientific_notation_is_handled(self):
        out = mod.apply_row_corrections(
            [{LAB_ID_KEY: "37712", "Sulfur": "1e-3"}], {"Sulfur": -0.0003})
        assert str(out[0]["Sulfur"]) == "0.0007"

    def test_whitespace_around_the_reading_does_not_defeat_it(self):
        out = mod.apply_row_corrections(
            [{LAB_ID_KEY: "37712", "Sulfur": "  0.0015 "}], {"Sulfur": -0.0003})
        assert str(out[0]["Sulfur"]) == "0.0012"
