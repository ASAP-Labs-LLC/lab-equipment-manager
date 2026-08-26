"""`--dev --seed` has to put a floor on the screen that a real lab could produce.

Two separate failures live here, and only the first one is obvious.

**The floor was empty.** `--seed` wrote the *config* side — `lem_samples`,
`lem_boxes`, `lem_qc_specs`, through `DbConfigStore` — which is what
`/api/status` reads. The floor reads the *bench* side: `lem_machine_status`,
`lem_machine_specs`, `lem_machine_layout` and the three level arms, all written
out on the instruments by the station module. Nothing seeded those, so
`/api/machines` answered `{"machines": [], "levels": []}` and every level,
document and corrective-action feature shipped in this branch rendered a blank
room. The seeder's own docstring already said a half-seeded demo "produces the
confusing empty floor the seeder exists to avoid".

**A fixture that cannot happen produces findings that do not matter.** Twice in
one day a critic reported a real contradiction it saw on screen and the cause
was the demo data, not the app: a bench seeded RED with no QC assigned (RED
*comes from* a QC failure, so real LEM cannot produce that), and a `low: -16`
invented onto `qc_specs`, a list that has never carried a band. Both burned a
round. So the invariants below are not decoration — they are the difference
between a demo floor and a fixture that lies. Every one of them asserts
something the lab's own physics guarantees.
"""

import importlib.util
import pathlib

import pytest

import snapshot_service
from labcore_gateway import FakeLabCoreGateway
from web_app import create_app

import demo_floor


MODULE_PATH = (pathlib.Path(__file__).resolve().parent.parent.parent
               / "LEM Station Module" / "lem_station_module.py")


def station_module():
    """The station module, loaded the way LabStation loads it.

    Same trick as `test_qc_window.module_rule` and for the same reason: this
    package cannot import that file normally, and it is the authority on what
    the bench actually writes. Skipped rather than failed when it is not
    alongside — CI archives `LEM Web Server/` on its own.
    """
    if not MODULE_PATH.exists():
        pytest.skip("station module not present next to the web server")
    spec = importlib.util.spec_from_file_location("_lem_mod_for_demo", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ddl_columns(ddl: str) -> set:
    """Column names out of a CREATE TABLE, ignoring table-level constraints."""
    body = ddl.split("(", 1)[1].rsplit(")", 1)[0]
    cols, depth, current = set(), 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            cols.add(current.strip().split()[0] if current.strip() else "")
            current = ""
        else:
            current += ch
    if current.strip():
        cols.add(current.strip().split()[0])
    return {c for c in cols
            if c and c.upper() not in ("PRIMARY", "UNIQUE", "FOREIGN", "CHECK")}


@pytest.fixture
def seeded(tmp_path):
    gw = FakeLabCoreGateway()
    snapshot_service.SnapshotService(gw).ensure_schema()
    demo_floor.seed(gw, documents_root=str(tmp_path / "documents"))
    return gw


def rows_of(gw, sql):
    """`read_sql` answers `{"ok", "rows", "columns"}`; the rows are the half
    every caller wants, and a refusal must not read as an empty table."""
    answer = gw.read_sql(sql) or {}
    assert not answer.get("error"), answer
    return answer.get("rows") or []


def floor(gw):
    app = create_app(gw, secret="t")
    app.config.update(TESTING=True)
    return app.test_client().get("/api/machines?fresh=1").get_json()


# ── 1. there is a floor, and it is stacked ───────────────────────────────────

class TestTheFloorIsPopulated:
    def test_the_fleet_arrives_on_api_machines(self, seeded):
        """The bug this file exists for: the floor's own endpoint was empty."""
        body = floor(seeded)
        assert len(body["machines"]) >= 8, body["machines"]

    def test_there_is_more_than_one_level(self, seeded):
        body = floor(seeded)
        assert len(body["levels"]) >= 3, body["levels"]

    def test_every_level_has_equipment_standing_on_it(self, seeded):
        """An empty level is a legitimate state in the app and a pointless one
        in a demo — cycling onto a blank plane shows nothing off."""
        body = floor(seeded)
        occupied = {m["level_uid"] for m in body["machines"]}
        for level in body["levels"]:
            assert level["uid"] in occupied, f"{level['name']} is empty"

    def test_a_default_level_is_set_and_names_a_real_one(self, seeded):
        body = floor(seeded)
        assert body["default_level"] in {l["uid"] for l in body["levels"]}


# ── 2. every seeded state is one the lab could actually produce ──────────────

class TestNothingSeededCouldNotHappen:
    def test_no_instrument_is_RED_without_a_failed_QC_behind_it(self, seeded):
        """The exact fixture that burned a round.

        RED is a CONSEQUENCE of a QC reading landing outside its band. A bench
        seeded RED with nothing failing is a state real LEM cannot reach, and a
        critic reading the screen correctly reports a contradiction that tells
        us nothing about the app.
        """
        for m in floor(seeded)["machines"]:
            if m["status"] != "RED":
                continue
            failed = [s for s in m["effective_specs"]
                      if s.get("last_qc_in_spec") is False]
            assert failed, f"{m['machine_uid']} is RED with no failing check"

    def test_a_RED_instrument_says_RED_on_its_QC_pill_too(self, seeded):
        for m in floor(seeded)["machines"]:
            if m["status"] == "RED":
                assert m["sub_statuses"]["qc"] == "RED", m["machine_uid"]

    def test_no_instrument_is_GREEN_with_a_failing_check(self, seeded):
        for m in floor(seeded)["machines"]:
            if m["status"] != "GREEN":
                continue
            for s in m["effective_specs"]:
                assert s.get("last_qc_in_spec") is not False, m["machine_uid"]

    def test_an_unassigned_instrument_has_no_specs_at_all(self, seeded):
        """"No QC assigned" is the honest grey state, and it means exactly what
        it says: no targets, no specs, no band. A bench with a spec but no
        assignment is the auto-detection bug this app deliberately removed."""
        for m in floor(seeded)["machines"]:
            if m["qc_targets"] or m["effective_specs"] or m["qc_specs"]:
                continue
            assert m["status"] in ("UNKNOWN", "DEAD-LINE", "SERVICE"), (
                f"{m['machine_uid']} has a colour with nothing judging it")

    def test_at_least_one_instrument_has_no_QC_assigned(self, seeded):
        """The grey state is a real part of this lab — most benches have no
        assignment — and a demo without one hides how it reads."""
        assert any(not m["qc_targets"] and not m["effective_specs"]
                   for m in floor(seeded)["machines"])

    def test_every_band_is_expected_plus_or_minus_k_std_dev(self, seeded):
        """`low`/`high` are not free-form. They are `spec_band` — the same
        arithmetic the module judges with — and a demo band that does not match
        its own expected/k/std_dev is a number no instrument could publish."""
        mod = station_module()
        for m in floor(seeded)["machines"]:
            for s in m["effective_specs"]:
                if s.get("std_dev") in (None, "") or s.get("k") in (None, ""):
                    continue
                spec = mod.TestSpec(name=s["test_name"],
                                    value_col=s["test_name"],
                                    expected=float(s["expected"]),
                                    std_dev=float(s["std_dev"]),
                                    k=float(s["k"]))
                low, high = mod.spec_band(spec)
                assert abs(float(s["low"]) - float(low)) < 1e-9, s
                assert abs(float(s["high"]) - float(high)) < 1e-9, s

    def test_a_reading_in_band_is_not_flagged_out_of_it(self, seeded):
        """`last_qc_in_spec` must agree with `last_qc_value` against the band,
        or the card and the badge tell two different stories."""
        for m in floor(seeded)["machines"]:
            for s in m["effective_specs"]:
                value, flag = s.get("last_qc_value"), s.get("last_qc_in_spec")
                if value is None or flag is None:
                    continue
                inside = float(s["low"]) <= float(value) <= float(s["high"])
                assert inside is bool(flag), (m["machine_uid"], s)

    def test_no_two_instruments_are_saved_on_the_same_bay(self, seeded):
        """Two machines on one bay is a real production bug (OptiMPP 2 and PAC
        Flash 2, both 4.1,0) whose spill fix lives in the severed world module.
        Seeding a collision would demo the bug, not the floor."""
        by_level = {}
        for m in floor(seeded)["machines"]:
            bay = (m["level_uid"], tuple(m["pos"]))
            assert bay not in by_level, f"{m['machine_uid']} shares {bay}"
            by_level[bay] = m["machine_uid"]


# ── 3. the shapes are the bench's, not ones we made up ───────────────────────

class TestTheSeedWritesWhatTheBenchWrites:
    """The whole failure mode this branch keeps hitting is a fixture whose
    shape agrees with the UI rather than with the instrument. These hold the
    seeder against the station module's own DDL, so a column the bench does not
    publish cannot appear on the demo floor."""

    @pytest.mark.parametrize("table", [
        "lem_machine_status", "lem_machine_specs", "lem_machine_substatus",
        "lem_machine_log",
    ])
    def test_the_seeder_only_writes_columns_the_module_declares(self, seeded, table):
        mod = station_module()
        ddl = {
            "lem_machine_status": mod.STATUS_TABLE_DDL,
            "lem_machine_specs": mod.EFFECTIVE_SPECS_DDL,
            "lem_machine_substatus": mod.SUBSTATUS_TABLE_DDL,
            "lem_machine_log": mod.LOG_TABLE_DDL,
        }[table]
        allowed = ddl_columns(ddl)
        rows = rows_of(seeded, f"SELECT * FROM {table} LIMIT 1")
        assert rows, f"{table} was never written"
        assert set(rows[0]) <= allowed, set(rows[0]) - allowed

    def test_qc_specs_rows_carry_no_band(self, seeded):
        """`low`/`high` on `qc_specs` is the invented shape that hid the NaN
        tooltip for a full round. The band is published to lem_machine_specs by
        the module; lem_qc_specs is an input and holds expected/std_dev/k."""
        for m in floor(seeded)["machines"]:
            for s in m["qc_specs"]:
                assert "low" not in s and "high" not in s, s

    def test_a_status_the_module_cannot_emit_is_never_seeded(self, seeded):
        mod = station_module()
        allowed = {mod.STATUS_GREEN, mod.STATUS_YELLOW, mod.STATUS_RED,
                   mod.STATUS_DEAD, mod.STATUS_SERVICE, mod.STATUS_UNKNOWN}
        for m in floor(seeded)["machines"]:
            assert m["status"] in allowed, m["status"]

    def test_every_log_event_uses_a_kind_the_module_documents(self, seeded):
        kinds = {"run", "qc", "status_change", "override", "comment", "pm",
                 "calibration", "config"}
        rows = rows_of(seeded, "SELECT DISTINCT kind FROM lem_machine_log")
        assert rows
        for row in rows:
            assert row["kind"] in kinds, row


# ── 4. the same floor every boot ─────────────────────────────────────────────

class TestItIsTheSameFloorEveryTime:
    def test_seeding_twice_produces_an_identical_fleet(self, tmp_path):
        """A demo that reshuffles per restart makes every finding against it
        unreproducible — you cannot ask a second pair of eyes to look at the
        thing you just looked at. Random *content*, fixed *seed*."""
        fleets = []
        for i in range(2):
            gw = FakeLabCoreGateway()
            snapshot_service.SnapshotService(gw).ensure_schema()
            demo_floor.seed(gw, documents_root=str(tmp_path / f"d{i}"))
            fleets.append([(m["machine_uid"], m["title"], m["status"],
                            tuple(m["pos"])) for m in floor(gw)["machines"]])
        assert fleets[0] == fleets[1]


# ── 5. the tabs this branch built have something in them ─────────────────────

class TestTheEquipmentRecordIsNotEmpty:
    def test_some_equipment_has_documents(self, seeded):
        rows = rows_of(seeded, "SELECT machine_uid FROM lem_equipment_documents")
        assert rows, "the documents tab demos nothing"

    def test_a_seeded_document_is_really_on_disk(self, tmp_path):
        """Bytes on disk, metadata in LabCore. A row pointing at a file that is
        not there is the one shape that makes the tab look broken."""
        gw = FakeLabCoreGateway()
        snapshot_service.SnapshotService(gw).ensure_schema()
        root = tmp_path / "documents"
        demo_floor.seed(gw, documents_root=str(root))
        assert list(root.rglob("*.pdf")), "no document bytes were written"

    def test_every_seeded_document_actually_landed(self, seeded):
        """The store deduplicates on `(machine_uid, content_hash)`, so two
        byte-identical demo files against one instrument collapse into one and
        the second upload silently disappears. It did: both of PAC Flash 1's
        documents were the same placeholder bytes, and the tab showed one."""
        rows = rows_of(seeded, "SELECT machine_uid FROM lem_equipment_documents")
        assert len(rows) == len(demo_floor.DOCUMENTS), (
            f"{len(demo_floor.DOCUMENTS)} seeded, {len(rows)} landed")

    def test_a_seeded_document_is_a_pdf_a_reader_can_open(self, seeded):
        """A certificate that opens blank is indistinguishable from a broken
        download, so the generated file carries a real xref and a page."""
        pdf = demo_floor._pdf("Calibration certificate")
        assert pdf.startswith(b"%PDF-")
        assert pdf.rstrip().endswith(b"%%EOF")
        assert b"startxref" in pdf and b"/Type/Page" in pdf
        start = int(pdf.rsplit(b"startxref", 1)[1].split()[0])
        assert pdf[start:start + 4] == b"xref", "startxref points at nothing"

    def test_some_equipment_has_an_open_corrective_action(self, seeded):
        app = create_app(seeded, secret="t")
        app.config.update(TESTING=True)
        body = app.test_client().get("/api/equipment/open-actions").get_json()
        assert body["total"] >= 1, body

    def test_there_is_history_to_look_at(self, seeded):
        rows = rows_of(seeded, "SELECT machine_uid FROM lem_machine_log")
        assert len(rows) >= 20, "the timeline demos nothing"
