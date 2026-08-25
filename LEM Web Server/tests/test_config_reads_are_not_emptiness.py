#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The read half of db_config_store — and how this branch turned it into loss.

`fix/confirm-every-write` converted every WRITE in the app and left this
store's READS alone. They judged an answer by hand:

    if res.get("ok") and res.get("rows"):   # _read_meta
    if not res.get("ok"): return []         # _read_rows

Look at what that rule is. It is "require an acknowledgement" — the exact rule
`labcore_result` documents as unsafe — sitting in a READ. Every answer that is
not a positive `ok` becomes `{}` or `[]`, and the EVIDENCED refusal

    {"error": "LabCore is busy…", "busy": true, "retry_after": n}

is one of them. So a busy queue made `load()` answer "this lab has no QC
standards, no boxes, no users, no checklists" — confidently, in the shape of a
real config.

On its own that is the familiar blank-floor bug. What made it a blocker is that
THIS branch added the prune. `_rewrite_rows` now upserts the wanted rows and
then deletes everything else, which is right when the list it is given is real.
`POST /api/boxes` builds that list by loading the config, appending one box and
saving it back. Feed it a config invented from a refused read and the prune does
exactly what it is told: it deletes every QC standard, user and checklist in the
lab to match the emptiness — and answers `{"ok": true}`.

Upsert-then-prune was the right fix for the write path. Combined with a read
that could not tell "could not ask" from "nothing there", it is a config
shredder that only fires when LabCore is already having a bad day.

The rule these tests pin: both reads go through `labcore_result.rows`. A table
that does not exist yet is still honestly empty — that is the one error a read
may swallow — and every other failure raises, all the way out through `load()`,
so no caller can build a save out of a read that never happened.
"""
import json

import pytest

from db_config_store import DbConfigStore
from labcore_gateway import FakeLabCoreGateway
from labcore_result import LabCoreError
from models import AppConfig, BoxConfig, SampleSpec, SampleTestSpec, UserSpec

# The measured refusal (notes.md, lem_station_module.py:495): an error dict
# with `busy`, returned normally rather than raised. Reads and writes travel
# the same endpoint, so a read gets turned away like this too.
BUSY = {"error": "LabCore is busy, try again later", "busy": True,
        "retry_after": 4}
BLIP = {"error": "HTTPSConnectionPool(host='labvision'): Read timed out"}

# A refusal carrying NO "error" key. Synthetic — this is a shape chosen for the
# suite because it exercises the `ok`/`queued` half of the rule, not a shape
# LabCore has ever been recorded sending. See labcore_result.
NO_ERROR_KEY = {"ok": False}


def _config() -> AppConfig:
    """A lab with something to lose."""
    return AppConfig(
        version=5, poll_minutes=5, map_locked=False, sample_id_column="Lab ID",
        samples=[SampleSpec(name="Diesel - AO25", sample_id_val="STD-1",
                            tests=[SampleTestSpec(name="Flash",
                                                  value_col="Flash Point",
                                                  expected=65.0, std_dev=2.0,
                                                  units="C")])],
        boxes=[BoxConfig(uid="m1", title="Multitek NS", csv_path="")],
        users=[UserSpec(username="kaden", password="x")])


class ReadsRefused:
    """Writes work; the config reads are turned away.

    Deliberately the shape of a full write queue rather than a dead LabCore:
    the queue refuses reads by ANSWERING, and that answer is what the old
    hand-rolled check read as "the lab is empty".
    """

    def __init__(self, real, answer=None, tables=("lem_meta", "lem_boxes",
                                                  "lem_samples", "lem_users",
                                                  "lem_checklists")):
        self.real = real
        self.answer = dict(answer or BUSY)
        self.tables = tables

    def sql(self, sql, args=None, **kw):
        return self.real.sql(sql, args, **kw)

    def read_sql(self, sql, args=None, **kw):
        if any(t in sql for t in self.tables):
            return dict(self.answer)
        return self.real.read_sql(sql, args, **kw)

    def is_running(self):
        return True

    def write(self, operation, params, **kw):
        return self.real.write(operation, params, **kw)

    def get_test_names(self):
        return self.real.get_test_names()

    def get_samples(self, **kw):
        return self.real.get_samples(**kw)


def _stored(gw, table, field):
    res = gw.read_sql("SELECT data FROM {0}".format(table))
    return sorted(json.loads(r["data"])[field] for r in res.get("rows") or [])


class TestAConfigReadThatFailedIsNotAnEmptyConfig:
    def test_a_refused_read_raises(self):
        gw = FakeLabCoreGateway()
        DbConfigStore(gw).save(_config())
        with pytest.raises(LabCoreError):
            DbConfigStore(ReadsRefused(gw)).load()

    def test_a_timed_out_read_raises(self):
        gw = FakeLabCoreGateway()
        DbConfigStore(gw).save(_config())
        with pytest.raises(LabCoreError):
            DbConfigStore(ReadsRefused(gw, BLIP)).load()

    def test_a_refusal_carrying_no_error_key_raises_too(self):
        """The half of the rule an `if res.get("error")` check misses."""
        gw = FakeLabCoreGateway()
        DbConfigStore(gw).save(_config())
        with pytest.raises(LabCoreError):
            DbConfigStore(ReadsRefused(gw, NO_ERROR_KEY)).load()

    def test_a_failed_LIST_read_raises_even_when_the_settings_blob_arrived(
            self):
        """`_read_rows` is the one that feeds the prune.

        Settings live in `lem_meta` and the lists in four other tables, so a
        partial failure produces the most convincing wrong answer of all: real
        poll interval, real theme, and no equipment.
        """
        gw = FakeLabCoreGateway()
        DbConfigStore(gw).save(_config())
        with pytest.raises(LabCoreError):
            DbConfigStore(ReadsRefused(gw, tables=("lem_samples",))).load()

    def test_a_lab_that_has_never_saved_anything_still_loads_the_default(self):
        """The one error a read may swallow, unchanged.

        A table nobody has created holds nothing, and LEM has to come up on a
        LabCore where it has never run.
        """
        cfg = DbConfigStore(FakeLabCoreGateway()).load()
        assert cfg.boxes == [] and cfg.samples == []

    def test_a_real_empty_config_is_still_empty(self):
        """The fix must not make "empty" impossible to express."""
        gw = FakeLabCoreGateway()
        store = DbConfigStore(gw)
        store.save(_config())
        empty = _config()
        empty.samples, empty.boxes, empty.users = [], [], []
        assert store.save(empty)[0]
        assert store.load().samples == []

    def test_a_healthy_load_still_round_trips(self):
        gw = FakeLabCoreGateway()
        store = DbConfigStore(gw)
        store.save(_config())
        assert [s.name for s in store.load().samples] == ["Diesel - AO25"]


class TestNoSaveIsEverBuiltOutOfAReadThatFailed:
    """The blocker, end to end.

    This is the sequence the fix has to make impossible: refuse the read, let
    the app attempt the save it builds from it, and check the lab still has its
    configuration.
    """

    class StubAuth:
        def login(self, username, password):
            return ("kaden", "tok", "")

        def logout(self, token):
            pass

    def _client(self, gateway):
        from web_app import create_app
        app = create_app(gateway, authenticator=self.StubAuth(), secret="s")
        app.config["TESTING"] = True
        c = app.test_client()
        c.post("/api/login", json={"username": "kaden", "password": "p"})
        return c

    def test_adding_a_box_during_a_blip_does_not_shred_the_config(self):
        """`/api/boxes` is load → append → save. With the read degrading to an
        empty config, the prune deletes the whole QC library to match it."""
        gw = FakeLabCoreGateway()
        DbConfigStore(gw).save(_config())
        assert _stored(gw, "lem_samples", "name") == ["Diesel - AO25"]

        client = self._client(ReadsRefused(gw))
        res = client.post("/api/boxes", json={"title": "New Machine"})

        assert _stored(gw, "lem_samples", "name") == ["Diesel - AO25"], (
            "a refused READ deleted the lab's QC standards")
        assert _stored(gw, "lem_users", "username") == ["kaden"]
        assert _stored(gw, "lem_boxes", "uid") == ["m1"]
        assert res.status_code in (502, 503), (
            "and it must say so rather than report the box as added")

    def test_the_config_endpoint_says_it_could_not_ask(self):
        gw = FakeLabCoreGateway()
        DbConfigStore(gw).save(_config())
        res = self._client(ReadsRefused(gw)).get("/api/config")
        assert res.status_code in (502, 503)
        assert res.get_json().get("error")

    def test_the_legacy_snapshot_does_not_draw_an_empty_lab(self):
        """`/api/status` renders the V4 dashboard's boxes. Zero boxes and zero
        QC is a floor that says every instrument was retired."""
        gw = FakeLabCoreGateway()
        DbConfigStore(gw).save(_config())
        res = self._client(ReadsRefused(gw)).get("/api/status")
        assert res.status_code in (502, 503)
        assert "boxes" not in (res.get_json() or {})

    def test_a_healthy_add_box_still_works(self):
        """The guard on the guard: this must not become "never save"."""
        gw = FakeLabCoreGateway()
        DbConfigStore(gw).save(_config())
        client = self._client(gw)
        assert client.post("/api/boxes", json={"title": "New Machine"}).status_code == 200
        assert _stored(gw, "lem_samples", "name") == ["Diesel - AO25"]
        assert len(_stored(gw, "lem_boxes", "uid")) == 2
