"""QC across the lab: /quality, the standards library and one standard (piece 8).

ia-final §3.5. QA's question is not "can this instrument run?" (the record
answers that) but "what did every check say last, and what is each one
checked against?". Why each part is a test:

* **One verdict column** (judge J3's defect in proposal B). B's QC table had
  a Verdict column and a Control column side by side, so one check could read
  "In spec" and "Out of control" in the same row and QA had to decide which
  one was the answer. The verdict is against the certificate band and it is
  the bench's; a statistical-control finding is provisional and is said as a
  caption under the verdict, only when a rule broke, never as a second word
  in a column of its own.
* **The verdict is the record's.** Each row's verdict is ``ui_live.
  check_verdict``'s, through ``ui_record.checks``, the rule the record's QC
  table and the readiness card judge by. A cross-lab table with its own rule
  could call a check "In spec" that the record calls "QC due".
* **A failed read is never an empty result.** "No QC is assigned to any
  instrument" and "the snapshot has not been read" are two sentences, and only
  one is a statement about the lab. Likewise "no standards defined" and "the
  QC library could not be read", "no instrument reports this test" and "the
  log could not be asked".
* **New standard lands on its page with the truth.** T5 (§8): add a standard
  with one check, check it on Agilent GC 2, Save, and the page says "Agilent
  GC 2 · Flash Point · No verdict yet" at once. The assignment is read from
  LEM's store, where the Save just put it, not from a snapshot that has not
  been rebuilt yet; and a check that has never run says No verdict yet, not
  In spec and not No QC assigned.
* **A standard's lifecycle keeps its links.** A rename moves the
  instruments and the certificate with it (the floor's save-new-then-delete
  rename left every assigned instrument pointing at a name that no longer
  existed); a delete is refused while a certificate is held.
"""
from __future__ import annotations

import io
import json
import re
from datetime import date, datetime
from pathlib import Path

import pytest

import demo_floor
import ui_instruments
import ui_live
import ui_quality
import ui_record
from labcore_gateway import FakeLabCoreGateway
from live_presence import LivePresence
from web_app import create_app

ROOT = Path(__file__).resolve().parent.parent
T = ROOT / "templates"
JS = ROOT / "static" / "js"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "machines_live_2026-10-01.json"
PROD = json.loads(FIXTURE.read_text())
AT = datetime(2026, 10, 1, 13, 18)


def prod_machines():
    return ui_live.judged(PROD["machines"], AT)


def href(uid, section):
    return "/instruments/%s%s" % (uid, ("#" + section) if section else "")


class CountingGateway(FakeLabCoreGateway):
    """LabCore, counted: every road in."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def sql(self, *a, **k):
        self.calls.append(("sql", str(a[0])[:60] if a else ""))
        return super().sql(*a, **k)

    def read_sql(self, *a, **k):
        self.calls.append(("read_sql", str(a[0])[:60] if a else ""))
        return super().read_sql(*a, **k)

    def write(self, *a, **k):
        self.calls.append(("write", ""))
        return super().write(*a, **k)

    def is_running(self, *a, **k):
        self.calls.append(("is_running", ""))
        return super().is_running(*a, **k)


class StubAuth:
    def login(self, u, p):
        return ("Kaden Ortiz", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


def _app(store, tmp_path, labcore=None):
    app = create_app(store, labcore=labcore, authenticator=StubAuth(), secret="s",
                     documents_root=str(tmp_path), live=LivePresence(),
                     live_token="test-token")
    app.config["TESTING"] = True
    return app


def _seeded(tmp_path, labcore=None):
    gw = FakeLabCoreGateway()
    app = _app(gw, tmp_path, labcore=labcore)
    app.config["SNAPSHOTS"].ensure_schema()
    demo_floor.seed(gw, documents_root=str(tmp_path))
    app.config["SNAPSHOTS"].refresh()
    return app, gw


def _signed_in(app):
    c = app.test_client()
    assert c.post("/api/login", json={"username": "kaden", "password": "good"}).status_code == 200
    return c


# ── Latest checks: the pure half ────────────────────────────────────────────

class TestLatestChecksIsTheRecordsVerdict:
    def test_every_row_says_what_the_record_says_for_the_same_check(self):
        out = ui_quality.latest(prod_machines(), href=href)
        assert out["state"] == "ready"
        by_uid = {m["machine_uid"]: m for m in prod_machines()}
        for row in out["rows"]:
            rec = {c["test"]: c for c in ui_record.checks(by_uid[row["uid"]])}
            assert row["verdict"] == rec[row["test"]]["verdict"], (row["title"], row["check"])

    def test_production_has_twenty_checks_worst_first(self):
        """1 Oct's lab, judged at 13:18: Agilent GC 1's 10% and 50% Recovery
        failed; five passes have aged out of their 24 h (Aquamax 3, both
        OptiMPPs' cloud and pour points); Eravap and Viscocity sit on stopped
        benches; eleven checks are in spec. Worst first is what QA reads
        down. (The /qc wall counts 16 "in spec" here because it does not age
        a pass; this table is the record's rule, so it agrees with the
        record row by row, which the test above holds.)"""
        out = ui_quality.latest(prod_machines(), href=href)
        words = [r["verdict"]["word"] for r in out["rows"]]
        assert len(out["rows"]) == 20
        assert words[:2] == ["Out of spec", "Out of spec"]
        assert [r["check"] for r in out["rows"][:2]] == ["10% Recovery", "50% Recovery"]
        ranks = [ui_quality.RANK[(r["verdict"]["key"], r["verdict"]["word"])] for r in out["rows"]]
        assert ranks == sorted(ranks)
        assert (words.count("QC due"), words.count("No verdict yet"), words.count("In spec")) == (5, 2, 11)

    def test_eravap_reads_no_verdict_yet_never_no_qc_assigned(self):
        """§4.1 / J3: Eravap has an assignment and a stopped bench."""
        out = ui_quality.latest(prod_machines(), href=href)
        er = [r for r in out["rows"] if r["title"] == "Eravap"]
        assert len(er) == 1
        assert er[0]["verdict"]["word"] == "No verdict yet"
        assert er[0]["verdict"]["detail"] == "bench stopped"
        assert "No QC assigned" not in json.dumps(out)

    def test_the_head_counts_say_one_fact_each(self):
        out = ui_quality.latest(prod_machines(), href=href)
        assert out["pill"] == {"glyph": "error", "level": "error", "text": "2 checks out of spec"}
        assert out["count"] == "20 checks on 14 instruments"

    def test_rows_link_to_the_records_qc_section(self):
        out = ui_quality.latest(prod_machines(), href=href)
        assert all(r["href"] == "/instruments/%s#qc" % r["uid"] for r in out["rows"])

    def test_a_check_is_named_short_with_its_method_beneath(self):
        out = ui_quality.latest(prod_machines(), href=href)
        kf = next(r for r in out["rows"] if r["title"] == "Aquamax 1")
        assert (kf["check"], kf["method"]) == ("Water, by Karl Fischer", "ASTM D6304")

    def test_the_standard_is_named_from_the_library_when_it_knows_it(self):
        """The bench publishes a check's Lab ID (CP); the library knows that
        lot as "Cloud CRM". The page names the standard, and links to it."""
        lib = [{"name": "Cloud CRM", "sample_id_val": "CP"}, {"name": "AF26", "sample_id_val": "AF26"}]
        out = ui_quality.latest(prod_machines(), href=href, library=lib)
        cp = next(r for r in out["rows"] if r["title"] == "OptiMPP 1" and r["check"].startswith("Cloud"))
        assert cp["standard"] == {"name": "Cloud CRM", "lab_id": "CP",
                                  "href": "/quality/standards/Cloud%20CRM"}
        # one the library does not know is still named, by its Lab ID
        pp = next(r for r in out["rows"] if r["title"] == "OptiMPP 1" and r["check"].startswith("Pour"))
        assert pp["standard"]["name"] == "PP" and pp["standard"]["href"] == "/quality/standards/PP"


class TestOneVerdictAndAProvisionalCaption:
    def _rows(self, values, spec=(10.0, 12.0, 14.0)):
        """QC history rows for one check on one bench, oldest first."""
        out = []
        for i, v in enumerate(values):
            out.append({"machine_uid": "m1", "ts": "2026-09-%02dT08:00:00" % (i + 1), "kind": "qc",
                        "lab_id": "STD", "test_name": "Flash Point", "value": str(v),
                        "detail": json.dumps({"in_spec": spec[0] <= v <= spec[2], "low": spec[0],
                                              "expected": spec[1], "high": spec[2]})})
        return out

    def _machine(self, last, ok):
        return ui_live.judged([{
            "machine_uid": "m1", "title": "PAC Flash 1", "live": True, "module_running": True,
            "qc_targets": [{"sample": "S", "test": "Flash Point"}],
            "effective_specs": [{"test_name": "Flash Point", "sample_id": "STD", "low": 10.0,
                                 "expected": 12.0, "high": 14.0, "last_qc_value": last,
                                 "last_qc_in_spec": ok, "last_qc_at": "2026-09-20T08:00:00"}]}],
            datetime(2026, 9, 20, 9, 0))

    def test_a_broken_rule_is_a_caption_under_an_in_spec_verdict(self):
        # in spec every time, but rising run on run: a trend
        vals = [11.0, 11.2, 11.4, 11.6, 11.8, 12.0, 12.2, 12.4]
        rows = self._rows(vals)
        out = ui_quality.latest(self._machine(vals[-1], True), href=href, rows=rows)
        r = out["rows"][0]
        assert r["verdict"]["word"] == "In spec"
        assert r["control"] and r["control"]["words"].startswith("Trend")
        # the caption is the row's second line; the verdict did not move
        assert r["verdict"] == ui_record.checks(self._machine(vals[-1], True)[0])[0]["verdict"]

    def test_no_control_note_beside_out_of_spec_it_would_say_it_twice(self):
        rows = self._rows([12.0, 11.9, 12.1, 15.0])
        out = ui_quality.latest(self._machine(15.0, False), href=href, rows=rows)
        assert out["rows"][0]["verdict"]["word"] == "Out of spec"
        assert out["rows"][0]["control"] is None

    def test_history_that_could_not_be_read_is_said_not_dropped(self):
        out = ui_quality.latest(self._machine(12.0, True), href=href, rows=None, missing="unread")
        assert out["history"] == "unread"
        assert out["rows"][0]["control"] is None
        assert "could not be read" in out["legend_note"]

    def test_nothing_read_is_not_nothing_assigned(self):
        unread = ui_quality.latest(None, href=href, error="LabCore did not answer")
        assert unread["state"] == "unreadable" and unread["rows"] is None
        never = ui_quality.latest(None, href=href)
        assert never["state"] == "not_read" and never["rows"] is None
        empty = ui_quality.latest([], href=href)
        assert empty["state"] == "ready" and empty["rows"] == []
        assert empty["pill"]["text"] == "No QC assigned anywhere"


# ── the standards library and one standard: the pure half ───────────────────

LIB = [
    {"name": "Diesel - AO25", "sample_id_val": "STD-1",
     "tests": [{"name": "Flash Point", "value_col": "Flash Point", "expected": 63.7, "std_dev": 1.05,
                "k": 2.0, "units": "C", "qc_expire_hours": 0.0},
               {"name": "Sulfur", "value_col": "Sulfur", "expected": 0.0015, "std_dev": 0.0002,
                "k": 2.0, "units": "%m/m", "qc_expire_hours": 48.0}]},
    {"name": "Flash CRM lot 7", "sample_id_val": "FCRM7",
     "tests": [{"name": "Flash Point", "value_col": "Flash Point", "expected": 60.0, "std_dev": 1.0,
                "k": 2.0, "units": "C", "qc_expire_hours": 0.0}]},
]


class Cert:
    def __init__(self, expires, name="coa.pdf", uid="c1"):
        self.uid, self.filename, self.expires_at, self.issued_at = uid, name, expires, ""
        self.uploaded_at, self.uploaded_by, self.size_bytes = "2026-01-02T03:04:05", "ryan", 1234

    def to_dict(self):
        return {"uid": self.uid, "filename": self.filename, "expires_at": self.expires_at,
                "issued_at": self.issued_at, "uploaded_at": self.uploaded_at,
                "uploaded_by": self.uploaded_by, "size_bytes": self.size_bytes}


class TestTheStandardsTable:
    def test_each_standard_says_its_lab_id_checks_use_and_certificate(self):
        targets = {"pac1": [("Diesel - AO25", "Flash Point")], "gc2": [("Diesel - AO25", "Sulfur")]}
        certs = {"Diesel - AO25": [Cert("2027-06-30")]}
        out = ui_quality.standards(LIB, targets, certs, today=date(2026, 10, 2))
        d, f = out["rows"]
        assert (d["name"], d["lab_id"], d["n_checks"], d["used_on"]) == ("Diesel - AO25", "STD-1", 2, 2)
        assert d["certificate"]["key"] == "uploaded"
        assert d["href"] == "/quality/standards/Diesel%20-%20AO25"
        assert (f["used_on"], f["certificate"]["key"], f["certificate"]["word"]) == (0, "needed", "Needed")
        assert out["pill"]["text"] == "1 needs a certificate"

    def test_an_expired_certificate_needs_replacing_and_an_expiring_one_counts_down(self):
        out = ui_quality.standards(LIB[:1], {}, {"Diesel - AO25": [Cert("2026-03-31")]}, today=date(2026, 10, 2))
        assert out["rows"][0]["certificate"]["word"] == "Expired 31 Mar"
        out = ui_quality.standards(LIB[:1], {}, {"Diesel - AO25": [Cert("2026-10-20")]}, today=date(2026, 10, 2))
        assert out["rows"][0]["certificate"]["word"] == "Expires in 18 d"

    def test_an_unreadable_library_is_not_an_empty_one(self):
        out = ui_quality.standards(None, None, None, today=date(2026, 10, 2), error="the store did not answer")
        assert out["state"] == "unreadable" and out["rows"] is None
        out = ui_quality.standards([], {}, {}, today=date(2026, 10, 2))
        assert out["state"] == "ready" and out["rows"] == []


class TestOneStandard:
    def _gc2(self):
        return ui_live.judged([{"machine_uid": "gc2", "title": "Agilent GC 2", "live": True,
                                "module_running": True, "effective_specs": [], "qc_targets": []}],
                              datetime(2026, 10, 2, 9, 0))

    def test_a_new_assignment_reads_no_verdict_yet_before_any_snapshot_knows_it(self):
        """T5's landing: the store holds the assignment, the snapshot does not."""
        out = ui_quality.standard(LIB[1], targets={"gc2": [("Flash CRM lot 7", "Flash Point")]},
                                  certs=[], machines=self._gc2(), today=date(2026, 10, 2), href=href)
        assert out["used_on"]["rows"] == [{
            "uid": "gc2", "title": "Agilent GC 2", "href": "/instruments/gc2#qc",
            "test": "Flash Point", "check": "Flash Point", "method": "",
            "verdict": {"key": "due", "word": "No verdict yet", "glyph": "never", "detail": "never run"},
            "value": None, "at": None, "low": 58.0, "expected": 60.0, "high": 62.0, "units": "°C",
            "line": "Agilent GC 2 · Flash Point · No verdict yet"}]
        assert out["used_on"]["caption"] == "1 instrument · 1 check"
        assert out["head"]["pill"] == {"glyph": "half", "level": "held", "text": "Certificate needed"}

    def test_a_verdict_on_another_standard_is_not_this_ones(self):
        """GC 2 passed Flash Point on AF26 an hour ago; it has never run this
        lot. Borrowing AF26's pass would call an unrun check In spec."""
        m = self._gc2()[0]
        m["effective_specs"] = [{"test_name": "Flash Point", "sample_id": "AF26", "low": 1, "expected": 2,
                                 "high": 3, "last_qc_value": 2, "last_qc_in_spec": True,
                                 "last_qc_at": "2026-10-02T08:00:00"}]
        out = ui_quality.standard(LIB[1], targets={"gc2": [("Flash CRM lot 7", "Flash Point")]},
                                  certs=[], machines=[m], today=date(2026, 10, 2), href=href)
        assert out["used_on"]["rows"][0]["verdict"]["word"] == "No verdict yet"

    def test_certified_values_state_the_band_k_and_window(self):
        out = ui_quality.standard(LIB[0], targets={}, certs=[Cert("2027-06-30")], machines=[],
                                  today=date(2026, 10, 2), href=href)
        fp, s = out["values"]
        assert (fp["low"], fp["expected"], fp["high"], fp["k"], fp["std_dev"]) == (61.6, 63.7, 65.8, 2.0, 1.05)
        assert fp["window"] == "24 h (the lab default)"
        assert s["window"] == "48 h"
        assert out["head"]["pill"]["text"] == "Not in use"

    def test_delete_is_refused_while_a_certificate_is_held_or_it_is_in_use(self):
        held = ui_quality.standard(LIB[0], targets={}, certs=[Cert("2027-06-30")], machines=[],
                                   today=date(2026, 10, 2), href=href)
        assert held["delete"]["blocked"].startswith("It holds a certificate")
        used = ui_quality.standard(LIB[1], targets={"gc2": [("Flash CRM lot 7", "Flash Point")]},
                                   certs=[], machines=self._gc2(), today=date(2026, 10, 2), href=href)
        assert used["delete"]["blocked"].startswith("It is checked on 1 instrument")
        free = ui_quality.standard(LIB[1], targets={}, certs=[], machines=[], today=date(2026, 10, 2), href=href)
        assert free["delete"]["blocked"] is None

    def test_an_assignment_to_an_instrument_lem_does_not_know_is_shown_not_dropped(self):
        out = ui_quality.standard(LIB[1], targets={"ghost": [("Flash CRM lot 7", "Flash Point")]},
                                  certs=[], machines=[], today=date(2026, 10, 2), href=href)
        row = out["used_on"]["rows"][0]
        assert row["title"] == "ghost" and row["verdict"]["word"] == "Not in LEM"


class TestResolvingALink:
    def test_a_lab_id_redirects_to_the_name(self):
        """The record names a check's standard by the Lab ID the bench
        publishes (STD-1); the page lives at the standard's name."""
        assert ui_quality.resolve(LIB, "Diesel - AO25") == (LIB[0], None)
        assert ui_quality.resolve(LIB, "STD-1") == (None, "Diesel - AO25")
        assert ui_quality.resolve(LIB, "std-1") == (None, "Diesel - AO25")
        assert ui_quality.resolve(LIB, "nope") == (None, None)


# ── the routes ──────────────────────────────────────────────────────────────

class TestTheQualityPages:
    def test_quality_is_qcs_home_in_the_nav(self, tmp_path):
        app, _ = _seeded(tmp_path)
        html = app.test_client().get("/quality").get_data(as_text=True)
        assert re.search(r'<a class="nav-item active" href="/quality" data-nav="qc"', html)

    def test_latest_checks_has_exactly_one_verdict_column(self, tmp_path):
        """J3: B's QC table had Verdict and Control side by side."""
        app, _ = _seeded(tmp_path)
        html = app.test_client().get("/quality").get_data(as_text=True)
        heads = re.findall(r"<th[^>]*>(.*?)</th>", html, re.S)
        text = [re.sub(r"<[^>]+>", "", h).strip() for h in heads]
        assert text.count("Verdict") == 1, text
        assert not any(t.lower().startswith("control") or "in control" in t.lower() for t in text), text
        assert "Verdict is against the certificate band. A control note is provisional " \
               "and does not change the verdict." in html

    def test_the_first_paint_carries_the_answer(self, tmp_path):
        app, _ = _seeded(tmp_path)
        html = app.test_client().get("/quality").get_data(as_text=True)
        data = json.loads(re.search(r'<script type="application/json" id="quality-data">(.*?)</script>',
                                    html, re.S).group(1))
        assert data["view"] == "latest" and data["latest"]["state"] == "ready"
        assert data["latest"]["rows"][0]["verdict"]["word"] == "Out of spec"

    def test_the_topbar_offers_new_standard_on_both_views_and_no_primary(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        for url in ("/quality", "/quality/standards"):
            html = c.get(url).get_data(as_text=True)
            top = html.split('<header class="topbar"', 1)[1].split("</header>", 1)[0]
            assert "New standard" in top and "btn-primary" not in top, url
            assert 'aria-current="page"' in top

    def test_drawing_qc_costs_labcore_nothing(self, tmp_path):
        lab = CountingGateway()
        app, _ = _seeded(tmp_path, labcore=lab)
        c = app.test_client()
        c.get("/quality")
        lab.calls.clear()
        for url in ("/quality", "/api/ui/quality", "/quality/standards", "/api/ui/standards",
                    "/quality/standards/Diesel%20-%20AO25", "/api/ui/standards/Diesel%20-%20AO25"):
            assert c.get(url).status_code == 200, url
        assert lab.calls == [], lab.calls

    def test_the_standards_view_lists_the_library(self, tmp_path):
        app, _ = _seeded(tmp_path)
        body = app.test_client().get("/api/ui/standards").get_json()
        names = [r["name"] for r in body["rows"]]
        assert names == ["Diesel - AO25", "Gasoline - RON check"]
        d, g = body["rows"]
        assert d["lab_id"] == "STD-1" and d["certificate"]["key"] == "uploaded"
        assert g["certificate"]["key"] == "expired"
        assert d["used_on"] >= 5

    def test_a_standard_by_its_lab_id_redirects_to_its_page(self, tmp_path):
        app, _ = _seeded(tmp_path)
        r = app.test_client().get("/quality/standards/STD-1")
        assert r.status_code == 302 and r.headers["Location"].endswith("/quality/standards/Diesel%20-%20AO25")

    def test_no_such_standard_is_a_404_with_a_sentence(self, tmp_path):
        app, _ = _seeded(tmp_path)
        r = app.test_client().get("/quality/standards/Nothing%20Here")
        assert r.status_code == 404
        assert "No QC standard" in r.get_data(as_text=True)

    def test_a_library_that_cannot_be_read_is_a_503_not_a_404(self, tmp_path, monkeypatch):
        app, gw = _seeded(tmp_path)
        real = gw.read_sql

        def broken(sql, *a, **k):
            if "lem_qc_samples" in sql:
                return {"error": "database is locked"}
            return real(sql, *a, **k)
        monkeypatch.setattr(gw, "read_sql", broken)
        c = app.test_client()
        r = c.get("/quality/standards/Diesel%20-%20AO25")
        assert r.status_code == 503
        assert "not a missing standard" in r.get_data(as_text=True)
        body = c.get("/api/ui/standards").get_json()
        assert body["state"] == "unreadable" and body["rows"] is None


class TestNewStandard:
    BODY = {"name": "Flash CRM lot 7", "lab_id": "FCRM7",
            "test": {"name": "Flash Point", "expected": 60.0, "std_dev": 1.0, "units": "C"},
            "instruments": ["gc-2"]}

    def test_t5_lands_with_the_assignment_said(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = _signed_in(app)
        r = c.post("/api/qc-samples/new", json=self.BODY)
        assert r.status_code == 200, r.get_json()
        body = r.get_json()
        assert body["href"] == "/quality/standards/Flash%20CRM%20lot%207"
        assert body["assigned"] == ["gc-2"] and body["failed"] == []
        # no snapshot rebuild in between: the page reads the store
        page = c.get("/api/ui/standards/Flash%20CRM%20lot%207").get_json()
        assert [r["line"] for r in page["used_on"]["rows"]] == ["GC-2 · Flash Point · No verdict yet"]
        assert page["values"][0]["k"] == 2.0 and page["values"][0]["window"] == "24 h (the lab default)"
        html = c.get(body["href"]).get_data(as_text=True)
        assert "GC-2 · Flash Point · No verdict yet" in html
        # GC-2 keeps what it was already checked on
        lib_targets = c.get("/api/ui/standards/Diesel%20-%20AO25").get_json()["used_on"]["rows"]
        assert any(r["uid"] == "gc-2" and r["test"] == "Sulfur" for r in lib_targets)

    def test_the_record_shows_the_certified_band_the_standard_page_shows(self, tmp_path):
        """The integration critic's T5: right after "Flash CRM lot 7" is
        assigned to GC-2, the standard's page shows GC-2's band 58.0 – 60.0 –
        62.0, while GC-2's record said "No certified values on file" — false:
        LEM has them, the bench just has not published a band for the new
        check yet. The record shows the same band the standard's page does
        (same numbers, so the two pages cannot disagree) and says where it
        came from: the certified values, not yet picked up by the bench."""
        app, _ = _seeded(tmp_path)
        c = _signed_in(app)
        assert c.post("/api/qc-samples/new", json=self.BODY).status_code == 200
        std = c.get("/api/ui/standards/Flash%20CRM%20lot%207").get_json()
        used = next(r for r in std["used_on"]["rows"] if r["uid"] == "gc-2")
        rec = c.get("/api/ui/instruments/gc-2").get_json()
        row = next(r for r in rec["qc"]["checks"] if r["test"] == "Flash Point")
        assert (row["low"], row["expected"], row["high"]) == (58.0, 60.0, 62.0)
        assert (row["low"], row["expected"], row["high"], row["units"]) == \
            (used["low"], used["expected"], used["high"], used["units"])
        assert row["band_from"] == "library"
        # a band the bench published is the bench's, and says so
        assert {r["band_from"] for r in rec["qc"]["checks"] if r["test"] != "Flash Point"} <= {"bench"}

    def test_signed_out_is_refused(self, tmp_path):
        app, _ = _seeded(tmp_path)
        assert app.test_client().post("/api/qc-samples/new", json=self.BODY).status_code == 401

    @pytest.mark.parametrize("change, words", [
        ({"name": "Diesel - AO25"}, "already a standard called"),
        ({"name": "diesel - ao25"}, "already a standard called"),
        ({"lab_id": "std-1"}, "already the Lab ID of Diesel - AO25"),
        ({"name": "  "}, "needs a name"),
        ({"lab_id": ""}, "needs the Lab ID"),
        ({"test": {"name": "", "expected": 1, "std_dev": 1}}, "Pick the test"),
        ({"test": {"name": "Flash Point", "expected": "", "std_dev": 1}}, "expected value"),
        ({"test": {"name": "Flash Point", "expected": 60, "std_dev": -1}}, "cannot be negative"),
        ({"instruments": ["no-such"]}, "no instrument"),
    ])
    def test_a_bad_standard_is_refused_in_words_and_nothing_is_saved(self, tmp_path, change, words):
        app, _ = _seeded(tmp_path)
        c = _signed_in(app)
        before = c.get("/api/qc-samples").get_json()["samples"]
        r = c.post("/api/qc-samples/new", json=dict(self.BODY, **change))
        assert r.status_code == 400 and words in r.get_json()["error"], r.get_json()
        assert c.get("/api/qc-samples").get_json()["samples"] == before

    def test_a_library_it_could_not_read_refuses_rather_than_risk_a_duplicate(self, tmp_path, monkeypatch):
        app, gw = _seeded(tmp_path)
        c = _signed_in(app)
        real = gw.read_sql
        monkeypatch.setattr(gw, "read_sql", lambda sql, *a, **k: {"error": "locked"}
                            if "lem_qc_samples" in sql else real(sql, *a, **k))
        r = c.post("/api/qc-samples/new", json=self.BODY)
        assert r.status_code == 503 and "Nothing was saved" in r.get_json()["error"]


class TestCheckItOn:
    def test_it_sets_exactly_the_instruments_for_one_check(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = _signed_in(app)
        r = c.post("/api/qc-samples/assign", json={"name": "Diesel - AO25", "test": "Flash Point",
                                                   "instruments": ["gc-2", "pac-flash-1"]})
        assert r.status_code == 200, r.get_json()
        got = r.get_json()
        assert "gc-2" in got["added"]
        rows = c.get("/api/ui/standards/Diesel%20-%20AO25").get_json()["used_on"]["rows"]
        flash = sorted(r["uid"] for r in rows if r["test"] == "Flash Point")
        assert flash == ["gc-2", "pac-flash-1"]
        # pac-flash-2 and pensky-1 were checked on Flash Point; now they are not
        assert set(got["removed"]) >= {"pac-flash-2", "pensky-1"}
        # other checks on those instruments are untouched
        assert any(r["uid"] == "gc-2" and r["test"] == "Sulfur" for r in rows)

    def test_a_test_the_standard_does_not_certify_is_refused(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = _signed_in(app)
        r = c.post("/api/qc-samples/assign", json={"name": "Gasoline - RON check", "test": "Flash Point",
                                                   "instruments": ["gc-2"]})
        assert r.status_code == 400 and "does not certify" in r.get_json()["error"]


class TestTheReportingInstruments:
    def test_instruments_that_already_report_a_test_are_named(self, tmp_path):
        app, _ = _seeded(tmp_path)
        body = app.test_client().get("/api/ui/quality/reporting").get_json()
        assert set(body["tests"]["flash point"]) >= {"pac-flash-1", "pac-flash-2", "pensky-1"}

    def test_a_log_it_could_not_read_is_said(self, tmp_path):
        app, _ = _seeded(tmp_path)

        class Broken:
            def reported_tests(self):
                from labcore_result import LabCoreUnavailable
                raise LabCoreUnavailable("the LEM store is locked")
        app.config["LOG_MIRROR"] = Broken()
        r = app.test_client().get("/api/ui/quality/reporting")
        assert r.status_code == 503 and "locked" in r.get_json()["error"]


class TestRename:
    def test_a_rename_moves_its_instruments_and_its_certificate(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = _signed_in(app)
        before = c.get("/api/ui/standards/Diesel%20-%20AO25").get_json()
        r = c.post("/api/qc-samples/rename", json={"name": "Diesel - AO25", "new_name": "Diesel AO25 lot 3"})
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["href"] == "/quality/standards/Diesel%20AO25%20lot%203"
        after = c.get("/api/ui/standards/Diesel%20AO25%20lot%203").get_json()
        assert after["head"]["lab_id"] == "STD-1"
        key = lambda p: sorted((x["uid"], x["test"]) for x in p["used_on"]["rows"])  # noqa: E731
        assert key(after) == key(before)
        assert [x["filename"] for x in after["certificates"]["rows"]] == \
               [x["filename"] for x in before["certificates"]["rows"]]
        # the Lab ID the record links by now leads to the new name
        r = c.get("/quality/standards/STD-1")
        assert r.status_code == 302 and r.headers["Location"].endswith("/quality/standards/Diesel%20AO25%20lot%203")
        names = [s["name"] for s in c.get("/api/qc-samples").get_json()["samples"]]
        assert "Diesel - AO25" not in names

    def test_a_rename_onto_an_existing_standard_is_refused(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = _signed_in(app)
        r = c.post("/api/qc-samples/rename", json={"name": "Diesel - AO25", "new_name": "Gasoline - RON check"})
        assert r.status_code == 400 and "already" in r.get_json()["error"]


# ── the page code ───────────────────────────────────────────────────────────

class TestThePageCode:
    FILES = ("quality.js", "quality_logic.js", "standard.js")

    def test_server_strings_go_in_through_text_never_html(self):
        for f in self.FILES:
            src = (JS / f).read_text()
            assert "innerHTML" not in src and "insertAdjacentHTML" not in src, f

    def test_no_inline_script_in_the_templates(self):
        for f in ("quality.html", "standard.html", "_new_standard.html"):
            html = (T / f).read_text()
            for m in re.finditer(r"<script(?![^>]*\bsrc=)([^>]*)>", html):
                assert 'type="application/json"' in m.group(1), (f, m.group(0))

    def test_no_band_is_printed_with_toFixed_2(self):
        for f in self.FILES:
            assert "toFixed(2)" not in (JS / f).read_text(), f

    def test_no_dialog_is_a_native_prompt(self):
        for f in self.FILES:
            src = (JS / f).read_text()
            assert not re.search(r"\b(prompt|alert|confirm)\(", src), f




def test_the_records_empty_library_door_opens_new_standard():
    """The record's "Change which standards…" sheet, over an empty library,
    used to send people to the classic floor's library dialog. The door is
    the New standard sheet (/quality/standards?new=1, which quality.js opens
    on arrival). Piece 14 deleted the classic floor, so there is no fallback
    to it left either: a link there would be a 404."""
    src = (JS / "record.js").read_text()
    assert "href: '/quality/standards?new=1', text: 'Add a standard'" in src
    assert "/floor/classic" not in src and "qc-library" not in src
    q = (JS / "quality.js").read_text()
    assert "new=1" in q and "openNew" in q


def test_an_assignment_with_no_published_band_takes_its_band_from_the_library():
    """Eravap is checked on Pentane, and its bench has never published a band
    for it (its module is stopped). "No certified values" would be false: the
    library holds Pentane's values. The row shows them, so the row says
    what a first run will be judged against."""
    lib = [{"name": "Pentane", "sample_id_val": "PENT", "tests": [
        {"name": "ASTM D6378 - Reid Vapor Pressure (VPx)", "value_col": "ASTM D6378 - Reid Vapor Pressure (VPx)",
         "expected": 15.6, "std_dev": 0.2, "k": 2.0, "units": "psi"}]}]
    out = ui_quality.latest(prod_machines(), href=href, library=lib)
    er = next(r for r in out["rows"] if r["title"] == "Eravap")
    assert (er["low"], er["expected"], er["high"], er["units"]) == (15.2, 15.6, 16.0, "psi")
    assert er["verdict"]["word"] == "No verdict yet"


def test_a_library_the_record_could_not_read_is_not_no_certified_values():
    """A failed read is never an empty result. The record takes the library
    from the snapshot; when it has none (not read), a check the bench has not
    published a band for must not claim "no certified values on file" — the
    row says the values were not read ("unread"). Only a library that WAS
    read and does not certify the check is "none". Eravap's Pentane / RVP is
    production's own unpublished assignment."""
    er = next(m for m in prod_machines() if m["title"] == "Eravap")
    row = ui_instruments.instrument(er, "", {}, href)
    lib = [{"name": "Pentane", "sample_id_val": "PENT", "tests": [
        {"name": "ASTM D6378 - Reid Vapor Pressure (VPx)", "expected": 15.6, "std_dev": 0.2,
         "k": 2.0, "units": "psi"}]}]

    def rvp(library):
        rec = ui_record.build(row, er, {}, library=library)
        return next(c for c in rec["qc"]["checks"] if "Reid" in c["test"])
    unread = rvp(None)
    assert (unread["low"], unread["high"], unread["band_from"]) == (None, None, "unread")
    assert rvp([])["band_from"] == "none"
    got = rvp(lib)
    assert (got["low"], got["expected"], got["high"], got["units"], got["band_from"]) == \
        (15.2, 15.6, 16.0, "psi", "library")


# ── Trends: every check's chart, by instrument, on one page ─────────────────
# 2026-10-08, Ryan: "I want a page with like all of them … arrange them
# together by machine and have them show dynamically in one page". /qc had
# them, as a TV wall that pages and is in no menu; this is the desk's.

class TestTrends:
    def test_the_seg_offers_trends_between_latest_and_standards(self, tmp_path):
        app, _ = _seeded(tmp_path)
        html = app.test_client().get("/quality/trends").get_data(as_text=True)
        seg = html[html.index('data-testid="qc-seg"'):]
        seg = seg[:seg.index("</nav>")]
        assert seg.index("Latest checks") < seg.index("Trends") < seg.index("Standards")
        assert 'href="/quality/trends" aria-current="page"' in seg

    def test_the_page_carries_every_check_with_its_history(self, tmp_path):
        app, _ = _seeded(tmp_path)
        c = app.test_client()
        html = c.get("/quality/trends").get_data(as_text=True)
        wall = c.get("/api/ui/wall/qc").get_json()
        island = json.loads(html.split('id="quality-data">', 1)[1].split("</script>", 1)[0])
        cards = island["trends"]["cards"]
        assert len(cards) == len(wall["cards"]) > 10
        assert any(len(k["points"]) > 3 for k in cards)

    def test_it_offers_the_tv_wall(self, tmp_path):
        app, _ = _seeded(tmp_path)
        html = app.test_client().get("/quality/trends").get_data(as_text=True)
        assert 'href="/qc"' in html
