"""What people see of the data transfer (transfer spec §14; piece T-P12).

Why these tests exist, in the order a person meets the surfaces:

* **The sidebar foot says where the data is, on every page, for nothing.**
  "Data · 15 of 17 benches reporting · 0 waiting" rides `GET /api/ui/live`,
  which every open page polls every 3 s. It is answered from memory and LEM's
  own store, so LabCore sees 0 ops however many tablets are open: measured
  here with a counting LabCore, not asserted from the code.
* **Every status item opens the page that resolves it.** "2 results need a
  decision" that opens a page with no Decide button is a dead end, the defect
  the UI rules forbid. So each item's link is followed and must answer 200,
  and the page it lands on must be the one with the means to act.
* **A failed read is never an empty result.** A v3.9 bench has never said how
  many readings it holds; its Data transfer rows say "Not reported by this
  bench's module version", never 0. A store that cannot be read is a sentence,
  never "no conflicts".
* **The server never writes results (D1).** Keep and Send record a person's
  decision in LEM's store and hand it to the bench in its next sync answer;
  LabStation files it. A decision costs LabCore 0 ops, and a decision cannot
  be made twice or by nobody.
* **The bridge-off switch refuses in words** while any bench still runs the
  older module (§12.1 step 5), not only while the off-host copy is missing.
* **The journal import (road C) runs a copied folder through the same rules
  as a sync:** CRC checked, contiguous from what LEM holds, nothing twice.
* **`/api/machines` grows by at most 90 bytes per machine** (the bar), with
  the largest `transfer` field a bench can cause.
"""
import io
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

import bench_v2_kit as kit
import custody
import ui_transfer
from bench_v2_kit import Bench, UID, with_crc
from labcore_counter import CountingLabCore
from labcore_gateway import FakeLabCoreGateway

OTHER = "eraspec-nir"


@pytest.fixture
def store():
    s = FakeLabCoreGateway()
    kit.seed_machine(s)
    kit.seed_machine(s, uid=OTHER, title="Eraspec NIR")
    kit.seed_machine(s, uid="v39-bench", title="Old Bench")
    return s


@pytest.fixture
def lab():
    return CountingLabCore()


@pytest.fixture
def app(store, lab):
    return kit.make_app(store, labcore=lab)


@pytest.fixture
def client(app):
    return app.test_client()


def _signin(client):
    r = client.post("/api/login", json={"username": "ryan", "password": "good"})
    assert r.status_code == 200, r.get_json()


def _conflict(seq, epoch, uid=UID, lab="38214", test="IBP", ours="151.9",
              theirs="151.6", who="dana", at="2026-10-01T09:10:00"):
    return {"seq": seq, "epoch": epoch, "uid": uid, "kind": "conflict",
            "ts": "2026-10-01T09:41:00-07:00", "module": "4.0.0",
            "of": ["%s:%d" % (epoch, seq - 1)],
            "cells": [[lab, test, ours, theirs, at, who]]}


def _bench_with_conflict(client, uid=UID):
    b = Bench(client, kit.enroll(client, uid=uid), uid=uid)
    b.journal(1)
    b.journal(1, make=lambda seq, epoch, uid=uid: _conflict(seq, epoch, uid=uid))
    r = b.sync()
    assert r.status_code == 200, r.get_json()
    return b


def _live(client):
    r = client.get("/api/ui/live")
    assert r.status_code == 200
    return r.get_json()


# ── the foot, pure ───────────────────────────────────────────────────────────

M = [{"machine_uid": "a", "title": "Agilent GC 1", "live": True},
     {"machine_uid": "b", "title": "Eraspec NIR", "live": True},
     {"machine_uid": "c", "title": "Eravap", "live": False}]
FACTS = {"error": None, "conflicts": {}, "pending": [], "stats": {}}
HREF = lambda uid, sec: "/instruments/%s%s" % (uid, ("#" + sec) if sec else "")


class TestFootLine:
    def test_the_default_line_counts_benches_reaching_lem_and_what_waits(self):
        now = 1_000_000.0
        reg = {"a": {"seen": now - 4, "unacked": 0}, "b": {"seen": now - 30, "unacked": 3}}
        f = ui_transfer.foot(machines=M, registry=reg, hydrated=True, facts=FACTS,
                             import_status=None, legacy_ok=True, href=HREF, now=now)
        # one line in the 260 px foot: the most urgent fact on screen, the
        # whole sentence as its accessible name
        assert f["line"]["text"] == "Data · 3 waiting at 1 bench"
        assert f["line"]["full"] == ("Data: 2 of 3 benches reporting, 3 readings "
                                     "waiting at the benches.")
        assert f["line"]["href"] == "/settings#transfer"
        assert f["items"] == []
        reg["b"]["unacked"] = 0
        f = ui_transfer.foot(machines=M, registry=reg, hydrated=True, facts=FACTS,
                             import_status=None, legacy_ok=True, href=HREF, now=now)
        assert f["line"]["text"] == "Data · 2 of 3 reporting"
        reg["c"] = {"seen": now - 1, "unacked": 0}
        f = ui_transfer.foot(machines=M, registry=reg, hydrated=True, facts=FACTS,
                             import_status=None, legacy_ok=True, href=HREF, now=now)
        assert f["line"]["text"] == "Data · all 3 reporting · 0 waiting"
        for text in ("Data · 3 waiting at 1 bench", "Data · 2 of 3 reporting",
                     "Data · all 17 reporting · 0 waiting", "Data · 9999 waiting at 17 benches",
                     "Data · 17 of 17 reporting"):
            assert len(text) <= 35, text

    def test_unknown_is_said_as_unknown_never_as_zero(self):
        """Before the registry has read bench_cursor, LEM does not know which
        benches are v2: "0 waiting" would be a statement about benches nobody
        has asked."""
        f = ui_transfer.foot(machines=M, registry={}, hydrated=False, facts=FACTS,
                             import_status=None, legacy_ok=True, href=HREF)
        assert f["line"]["text"] == "Data · not known yet"
        assert "0" not in f["line"]["text"]
        assert ui_transfer.foot(machines=None, registry={}, hydrated=True, facts=FACTS,
                                import_status=None, legacy_ok=True, href=HREF) is None

    def test_a_quiet_bench_holding_readings_is_named_and_links_to_its_section(self):
        now = 1_000_000.0
        reg = {"b": {"seen": now - 12 * 60, "unacked": 148}}
        f = ui_transfer.foot(machines=M, registry=reg, hydrated=True, facts=FACTS,
                             import_status=None, legacy_ok=True, href=HREF, now=now)
        (item,) = f["items"]
        assert item["message"] == ("Eraspec NIR: 148 readings waiting at the bench; "
                                   "it has not reached LEM for 12 min.")
        assert item["href"] == "/instruments/b#transfer"
        assert f["line"]["glyph"] == "held"

    def test_conflicts_rejections_enrolment_and_the_import_each_link_somewhere(self):
        facts = {"error": None, "conflicts": {"a": 2}, "pending": ["c"],
                 "stats": {"b": {"stats": {"rejected": 1}}}}
        f = ui_transfer.foot(machines=M, registry={}, hydrated=True, facts=facts,
                             import_status={"state": "running", "tables_verified": 20,
                                            "tables_total": 33},
                             legacy_ok=True, href=HREF)
        got = {i["message"]: i["href"] for i in f["items"]}
        assert got == {
            "2 results need a decision: a bench's value differs from one a person "
            "entered in LabCore.": "/results/conflicts",
            "1 result rejected by LabCore (Eraspec NIR).": "/results/conflicts#rejected",
            "Eravap asks to enrol with LEM.": "/settings#transfer",
            "Moving the record from LabCore · 60 %.": "/settings#transfer"}

    def test_a_store_that_cannot_be_read_is_said_not_counted_as_none(self):
        facts = {"error": "database is locked", "conflicts": None, "pending": None,
                 "stats": None}
        f = ui_transfer.foot(machines=M, registry={}, hydrated=True, facts=facts,
                             import_status=None, legacy_ok=True, href=HREF)
        assert any("could not read" in i["message"] and "database is locked" in i["message"]
                   for i in f["items"])


# ── the foot, served ─────────────────────────────────────────────────────────

class TestLiveFeed:
    def test_ui_live_carries_the_data_line_and_costs_labcore_nothing(self, client, lab):
        b = Bench(client, kit.enroll(client))
        b.journal(2).sync()
        client.get("/api/machines?fresh=1")
        before = lab.ops
        for _ in range(5):
            body = _live(client)
        assert lab.ops == before, lab.calls[before:]
        line = body["transfer"]
        assert re.fullmatch(r"Data · (\d+ of 3 reporting|all 3 reporting · 0 waiting)",
                            line["text"]), line["text"]
        assert line["full"].endswith("0 readings waiting at the benches.")

    def test_a_conflict_reaches_the_bell_and_the_link_opens_the_decide_page(self, client):
        _bench_with_conflict(client)
        client.get("/api/machines?fresh=1")
        body = _live(client)
        notes = [n for n in body.get("notifications") or [] if n.get("about") == "data"]
        assert [n["message"] for n in notes] == [
            "1 result needs a decision: a bench's value differs from one a person "
            "entered in LabCore."]
        page = client.get(notes[0]["href"])
        assert page.status_code == 200
        html = page.get_data(as_text=True)
        assert 'data-testid="conflicts-page"' in html

    def test_every_status_item_and_the_line_open_a_page_that_answers(self, client, store):
        """No dead ends: the foot's line, and every item the transfer adds,
        is followed here and must answer 200 on a page that has the anchor
        it names."""
        _bench_with_conflict(client)
        store.sql("INSERT INTO bench_token (machine_uid, pending_reenrol_at) "
                  "VALUES ('v39-bench', '2026-10-01T09:00:00')")
        client.application.config["TRANSFER_WATCH"].invalidate()
        _signin(client)
        client.get("/api/machines?fresh=1")
        body = _live(client)
        links = [body["transfer"]["href"]] + [
            n["href"] for n in body.get("notifications") or [] if n.get("about") == "data"]
        assert len(links) >= 3, links
        for href in links:
            path, _, anchor = href.partition("#")
            r = client.get(path)
            assert r.status_code == 200, (href, r.status_code)
            if anchor:
                assert 'id="%s"' % anchor in r.get_data(as_text=True), href


# ── the instrument's section ─────────────────────────────────────────────────

class TestSection:
    def test_a_v2_bench_reports_its_rows(self, client):
        b = Bench(client, kit.enroll(client))
        b.journal(3).sync(stats={"unacked": 0, "road": "public", "held": 2,
                                 "rejected": 0, "digest": kit.DIGEST_ZERO})
        rows = {r["key"]: r for r in client.get("/api/ui/transfer/%s" % UID).get_json()["rows"]}
        assert rows["road"]["value"] == "Internet"
        assert rows["delivered"]["value"].endswith(" ago")
        assert rows["waiting"]["value"] == "0"
        assert "2 waiting for their sample · 0 rejected" in rows["results"]["value"]
        assert rows["decide"]["value"] == "0"
        assert rows["module"]["value"] == "v4.0.0"

    def test_an_older_bench_is_not_reported_never_zero(self, client):
        body = client.get("/api/ui/transfer/v39-bench").get_json()
        assert body["mode"] == "legacy"
        for r in body["rows"][1:]:
            assert r["value"] == ui_transfer.NOT_REPORTED, r
            assert r["value"] != "0"

    def test_a_conflict_is_a_row_with_its_action(self, client):
        _bench_with_conflict(client)
        rows = {r["key"]: r for r in client.get("/api/ui/transfer/%s" % UID).get_json()["rows"]}
        assert rows["decide"]["value"] == "1 result"
        assert rows["decide"]["href"] == "/results/conflicts?machine=%s" % UID
        assert rows["decide"]["action"] == "Decide"

    def test_an_unknown_uid_is_404_not_an_empty_section(self, client):
        assert client.get("/api/ui/transfer/no-such-bench").status_code == 404

    def test_the_record_page_carries_the_section(self, client):
        """Whichever template serves /instruments/<uid>, the Data transfer
        section is on it. If another piece replaces the page, this test is
        the reminder to include templates/_transfer_section.html."""
        html = client.get("/instruments/%s" % UID).get_data(as_text=True)
        assert 'id="transfer"' in html and "Data transfer" in html
        assert "How this bench's readings reach LEM and LabCore." in html


# ── conflicts: decided here, filed by the bench ──────────────────────────────

class TestConflicts:
    def test_listed_with_both_values_who_and_the_print(self, client):
        _bench_with_conflict(client)
        body = client.get("/api/results/conflicts").get_json()
        (c,) = body["open"]
        assert (c["lab_id"], c["test_name"], c["ours"], c["theirs"],
                c["their_operator"]) == ("38214", "IBP", "151.9", "151.6", "dana")
        assert c["title"] == "PAC Flash 1"
        assert c["print_at"], "the print the value came from"

    def test_deciding_needs_a_person(self, client):
        _bench_with_conflict(client)
        ref = client.get("/api/results/conflicts").get_json()["open"][0]["ref"]
        r = client.post("/api/results/conflicts/decide", json={"ref": ref, "choice": "keep"})
        assert r.status_code == 401

    def test_keep_is_recorded_once_and_reaches_the_bench_never_labcore(self, client, lab):
        b = _bench_with_conflict(client)
        _signin(client)
        ref = client.get("/api/results/conflicts").get_json()["open"][0]["ref"]
        before = lab.ops
        r = client.post("/api/results/conflicts/decide",
                        json={"ref": ref, "choice": "send", "seen": "151.6"})
        assert r.status_code == 200, r.get_json()
        assert lab.ops == before, "the server never writes a result"
        again = client.post("/api/results/conflicts/decide",
                            json={"ref": ref, "choice": "keep", "seen": "151.6"})
        assert again.status_code == 409
        assert "already decided" in again.get_json()["error"]
        # the bench's next sync answer hands the decision over
        ans = b.journal(1).sync().get_json()
        assert ans["resolutions"] == [{"conflict_seq": ref, "choice": "send", "by": "ryan"}]
        body = client.get("/api/results/conflicts").get_json()
        assert body["open"] == [] and body["decided"][0]["choice"] == "send"
        assert body["decided"][0]["delivered"] is False

    def test_the_bench_journaling_the_decision_marks_it_delivered(self, client):
        """The module journals `{"kind": "resolution", "conflict_ref": ...}`;
        the server used to read only `conflict_seq`, so a delivered decision
        stayed "waiting for the bench" for ever."""
        b = _bench_with_conflict(client)
        _signin(client)
        ref = client.get("/api/results/conflicts").get_json()["open"][0]["ref"]
        client.post("/api/results/conflicts/decide", json={"ref": ref, "choice": "keep"})
        b.journal(1, make=lambda seq, epoch, uid=UID: {
            "seq": seq, "epoch": epoch, "uid": uid, "kind": "resolution",
            "ts": "2026-10-01T09:45:00-07:00", "module": "4.0.0",
            "conflict_ref": ref, "choice": "keep"})
        assert b.sync().status_code == 200
        (d,) = client.get("/api/results/conflicts").get_json()["decided"]
        assert d["delivered"] is True

    def test_a_value_that_moved_since_the_page_showed_it_is_refused(self, client):
        _bench_with_conflict(client)
        _signin(client)
        ref = client.get("/api/results/conflicts").get_json()["open"][0]["ref"]
        r = client.post("/api/results/conflicts/decide",
                        json={"ref": ref, "choice": "send", "seen": "999"})
        assert r.status_code == 409
        assert client.get("/api/results/conflicts").get_json()["open"], "nothing decided"

    def test_a_bad_choice_is_refused(self, client):
        _bench_with_conflict(client)
        _signin(client)
        ref = client.get("/api/results/conflicts").get_json()["open"][0]["ref"]
        r = client.post("/api/results/conflicts/decide", json={"ref": ref, "choice": "both"})
        assert r.status_code == 400

    def test_the_page_has_its_states_and_a_way_home(self, client):
        html = client.get("/results/conflicts").get_data(as_text=True)
        assert 'data-testid="sidebar"' in html and 'id="app-version"' in html
        assert 'href="/"' in html
        assert "Nothing needs a decision." in html
        assert 'id="rejected"' in html

    def test_a_store_that_cannot_be_read_is_503_not_an_empty_list(self, client, store,
                                                                  monkeypatch):
        real = store.read_sql

        def broken(sql, args=None, **kw):
            if "result_conflict" in sql:
                return {"error": "database is locked"}
            return real(sql, args, **kw)
        monkeypatch.setattr(store, "read_sql", broken)
        r = client.get("/api/results/conflicts")
        assert r.status_code == 503
        assert "database is locked" in r.get_json()["error"]


# ── Settings › Transfer ──────────────────────────────────────────────────────

class TestSettingsTransfer:
    def test_the_section_is_on_settings(self, client):
        html = client.get("/settings").get_data(as_text=True)
        assert 'id="transfer"' in html
        assert 'data-testid="bridge-off"' in html

    def test_overview_names_every_bench_and_why_the_bridge_stays_on(self, client):
        b = Bench(client, kit.enroll(client))
        b.journal(1).sync()
        body = client.get("/api/transfer/overview").get_json()
        uids = [x["machine_uid"] for x in body["benches"]]
        assert set(uids) == {UID, OTHER, "v39-bench"}
        old = next(x for x in body["benches"] if x["machine_uid"] == "v39-bench")
        assert old["mode"] == "legacy" and old["module"] == ui_transfer.NOT_REPORTED
        reasons = " ".join(body["bridge"]["refusals"])
        assert "not reported over v2 yet" in reasons and "Old Bench" in reasons

    def test_bridge_off_is_refused_while_a_bench_runs_the_older_module(
            self, store, tmp_path):
        where = tmp_path / "off"
        where.mkdir()
        cust = custody.Custody(store, backup_dir=str(tmp_path / "b"),
                               offsite_dir=str(where))
        cust.hydrate()
        # off-host is fresh: only the fleet can refuse now
        cust._set({"offsite_last_ok": datetime.now(timezone.utc).isoformat()})
        a = kit.make_app(store)
        custody.attach(a, cust)
        c = a.test_client()
        _signin(c)
        r = c.post("/api/transfer/bridge", json={"on": False})
        assert r.status_code == 409
        assert any("not reported over v2 yet" in x for x in r.get_json()["refusals"])

    def test_reset_revokes_the_token_and_the_next_enrolment_waits(self, client):
        b = Bench(client, kit.enroll(client))
        assert b.journal(1).sync().status_code == 200
        assert client.post("/api/transfer/benches/%s/reset" % UID).status_code == 401
        _signin(client)
        r = client.post("/api/transfer/benches/%s/reset" % UID)
        assert r.status_code == 200, r.get_json()
        assert b.journal(1).sync().status_code == 401
        again = client.post("/api/v2/bench/%s/enroll" % UID,
                            headers={"X-LEM-Token": kit.SHARED_TOKEN},
                            json={"machine_uid": UID})
        assert again.status_code == 202

    def test_dedupe_approvals_are_listed_or_the_read_failure_said(self, client):
        _signin(client)
        r = client.get("/api/dedupe/approvals")
        assert r.status_code == 200
        assert r.get_json()["approvals"] == []


# ── road C: the journal folder, imported ─────────────────────────────────────

def _segment(records):
    return b"".join(json.dumps(with_crc(r), sort_keys=True, separators=(",", ":"))
                    .encode() + b"\n" for r in records)


class TestJournalImport:
    def _post(self, client, data, dry):
        return client.post("/api/transfer/journal-import" + ("?dry=1" if dry else ""),
                           data={"files": [(io.BytesIO(d), n) for n, d in data]},
                           content_type="multipart/form-data")

    def test_preview_then_import_through_the_sync_rules(self, client, store):
        recs = [kit.run_record(s, "ep-9") for s in range(1, 6)]
        files = [("seg-000001.jsonl", _segment(recs))]
        assert self._post(client, files, True).status_code == 401
        _signin(client)
        p = self._post(client, files, True).get_json()
        assert p["benches"][0]["new"] == 5 and p["benches"][0]["held"] == 0
        assert kit.stored_seqs(store) == [], "a preview writes nothing"
        r = self._post(client, files, False)
        assert r.status_code == 200, r.get_json()
        assert sorted(kit.stored_seqs(store)) == [("ep-9", s) for s in range(1, 6)]
        twice = self._post(client, files, False).get_json()
        assert twice["benches"][0]["imported"] == 0
        assert sorted(kit.stored_seqs(store)) == [("ep-9", s) for s in range(1, 6)]

    def test_a_damaged_line_and_a_gap_are_said_and_nothing_past_the_gap_lands(
            self, client, store):
        _signin(client)
        recs = [kit.run_record(s, "ep-9") for s in (1, 2, 4)]
        seg = _segment(recs)
        bad = seg.replace(b'"L-00002"', b'"L-99999"')
        r = self._post(client, [("seg-000001.jsonl", bad)], False).get_json()
        b = r["benches"][0]
        assert b["damaged"] == 1
        assert b["imported"] == 1
        assert "gap" in b["note"]
        assert sorted(kit.stored_seqs(store)) == [("ep-9", 1)]


# ── the bar: /api/machines grows ≤ 90 B per machine ──────────────────────────

def test_machines_payload_grows_at_most_90_bytes_per_machine(client):
    """The largest `transfer` a bench can cause: the longest road word, the
    largest count LEM echoes, a nine-digit age. Measured on the serialised
    machine, as GC hub receives it."""
    import bench_api
    b = Bench(client, kit.enroll(client))
    b.journal(1).sync(stats={"unacked": bench_api.MAX_COUNT * 10, "road": "public",
                             "digest": kit.DIGEST_ZERO})
    reg = client.application.config["BENCH_REGISTRY"]
    reg.note(UID, seen=0.0)          # an age of ~56 years: as long as it gets
    ms = client.get("/api/machines?fresh=1").get_json()["machines"]
    m = next(x for x in ms if x["machine_uid"] == UID)
    with_field = len(json.dumps(m, separators=(",", ":")).encode())
    without = len(json.dumps({k: v for k, v in m.items() if k != "transfer"},
                             separators=(",", ":")).encode())
    assert with_field - without <= 90, (with_field - without, m["transfer"])


# ── bridge-off's bench half, pure (§12.1 step 5) ─────────────────────────────

class TestFleetRefusals:
    """The bridge stops copying LEM's record into LabCore. A bench still on
    the older module reads its settings and writes its readings THERE, so
    turning the bridge off under it would strand it silently. Seven days on
    v2 for every bench, and seven days with no reading pulled from LabCore,
    are the spec's proof that nobody is left on the old road."""

    NOW = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    FLEET = [{"machine_uid": "a", "title": "Agilent GC 1"},
             {"machine_uid": "e", "title": "Eravap"}]

    def _why(self, first, legacy=None):
        return ui_transfer.fleet_refusals(machines=self.FLEET, first_v2=first,
                                          newest_legacy_row=legacy, now=self.NOW)

    def test_every_bench_on_v2_for_8_days_and_no_pull_allows(self):
        eight = (self.NOW - timedelta(days=8)).isoformat()
        assert self._why({"a": eight, "e": eight}) == []

    def test_a_bench_never_on_v2_is_named(self):
        eight = (self.NOW - timedelta(days=8)).isoformat()
        (why,) = self._why({"a": eight})
        assert why.startswith("1 bench has not reported over v2 yet: Eravap.")

    def test_a_bench_on_v2_for_3_days_says_when_it_qualifies(self):
        eight = (self.NOW - timedelta(days=8)).isoformat()
        three = (self.NOW - timedelta(days=3)).isoformat()
        (why,) = self._why({"a": eight, "e": three})
        assert "Eravap has reported over v2 for under 7 days" in why
        assert "on 6 Oct" in why or "on 7 Oct" in why, why   # 3 days ago + 7, local zone

    def test_a_reading_pulled_from_labcore_2_days_ago_refuses(self):
        eight = (self.NOW - timedelta(days=8)).isoformat()
        (why,) = self._why({"a": eight, "e": eight},
                           legacy=(self.NOW - timedelta(days=2)).isoformat())
        assert why.startswith("The bridge brought in a reading from LabCore on")
