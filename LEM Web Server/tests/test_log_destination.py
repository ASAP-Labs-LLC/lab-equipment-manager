#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The warnings this branch added have to land somewhere a person can read.

`fix/confirm-every-write` reports every refused write, unnamed CSV column and
degraded read through `logger.warning`. On ASAPSV1 the server runs as a `.pyw`
under pythonw.exe: no console, and no handler was ever configured — so
`logging`'s last-resort handler wrote to a `sys.stderr` that does not exist.
Every one of those warnings went into a void.

Detecting a refused audit line and then announcing it nowhere is barely better
than not detecting it, so the app now opens a real destination: a rotating file
next to the other operational state.

WHERE, and why it is not the code directory: a deploy swaps the release
directory wholesale (RELEASING.md §1 — `current` is a junction re-pointed at a
new immutable folder) and the release archive deliberately excludes `data/`.
A log written inside the release is lost exactly when it is most wanted, which
is the deploy that went wrong. `tray.data_dir()` is the persistent one —
`C:\\ASAPApps\\lem\\data` on the server, via LEM_DATA_DIR — and `restart.log`
already lives there for the same reason.
"""
import logging
import os

import pytest

from labcore_gateway import FakeLabCoreGateway


@pytest.fixture
def clean_root():
    """Remove our handler between tests — it is process-global by design."""
    import web_app

    def drop():
        for handler in list(logging.getLogger().handlers):
            if getattr(handler, "_lem", False):
                logging.getLogger().removeHandler(handler)
                handler.close()
    drop()
    yield
    drop()


def _configure(tmp_path, monkeypatch):
    import web_app
    monkeypatch.setenv("LEM_DATA_DIR", str(tmp_path))
    return web_app.configure_logging()


class TestThereIsSomewhereForAWarningToGo:
    def test_a_store_warning_reaches_the_file(self, tmp_path, monkeypatch,
                                              clean_root):
        import machine_map                                   # noqa: F401
        path = _configure(tmp_path, monkeypatch)
        logging.getLogger("machine_map").warning("a QC assignment was refused")
        for handler in logging.getLogger().handlers:
            handler.flush()
        assert "a QC assignment was refused" in open(path, encoding="utf-8").read()

    def test_the_level_captures_warnings(self, tmp_path, monkeypatch,
                                         clean_root):
        """The whole reporting mechanism of this branch is `logger.warning`. A
        handler that starts at ERROR would be the same void with a file in it."""
        path = _configure(tmp_path, monkeypatch)
        logging.getLogger("web_app").warning("audit line was not recorded")
        for handler in logging.getLogger().handlers:
            handler.flush()
        assert "audit line was not recorded" in open(path, encoding="utf-8").read()

    def test_it_lands_in_the_data_dir_not_the_release(self, tmp_path,
                                                     monkeypatch, clean_root):
        import tray
        path = _configure(tmp_path, monkeypatch)
        assert os.path.dirname(path) == tray.data_dir()
        assert os.path.dirname(path) == str(tmp_path)
        assert not path.startswith(os.path.dirname(os.path.abspath(
            tray.__file__)) + os.sep)

    def test_it_rotates_rather_than_growing_without_end(self, tmp_path,
                                                        monkeypatch,
                                                        clean_root):
        """A busy LabCore afternoon is a warning per refused write. Unbounded,
        this is the file that fills the server's disk."""
        from logging.handlers import RotatingFileHandler
        _configure(tmp_path, monkeypatch)
        ours = [h for h in logging.getLogger().handlers
                if getattr(h, "_lem", False)]
        assert len(ours) == 1
        assert isinstance(ours[0], RotatingFileHandler)
        assert ours[0].maxBytes > 0 and ours[0].backupCount > 0

    def test_configuring_twice_does_not_double_every_line(self, tmp_path,
                                                          monkeypatch,
                                                          clean_root):
        """`create_app` runs once in production and hundreds of times in this
        suite; a handler per call would write each warning that many times."""
        first = _configure(tmp_path, monkeypatch)
        second = _configure(tmp_path, monkeypatch)
        assert first == second
        assert len([h for h in logging.getLogger().handlers
                    if getattr(h, "_lem", False)]) == 1

    def test_the_request_log_does_not_drown_the_refusals(self, tmp_path,
                                                         monkeypatch,
                                                         clean_root):
        """The floor re-reads its world every 2 seconds from every open
        browser, and every bench POSTs /api/live on each poll. At INFO,
        werkzeug writes a line for each of those — thousands an hour, rotating
        the refusals out of the file within a day. The one thing this log
        exists to keep would be the first thing it loses.
        """
        path = _configure(tmp_path, monkeypatch)
        wz = logging.getLogger("werkzeug")
        wz.setLevel(logging.INFO)
        wz.info('127.0.0.1 - - "GET /api/machines HTTP/1.1" 200 -')
        wz.warning("werkzeug had something real to say")
        logging.getLogger("machine_map").warning("a QC assignment was refused")
        for handler in logging.getLogger().handlers:
            handler.flush()
        written = open(path, encoding="utf-8").read()
        assert "GET /api/machines" not in written
        assert "a QC assignment was refused" in written
        assert "something real to say" in written

    def test_a_directory_it_cannot_write_never_stops_the_app(self, tmp_path,
                                                             monkeypatch,
                                                             clean_root):
        """A server that refuses to start because it could not open its log is
        a worse outage than the one the log was for."""
        import web_app
        blocked = tmp_path / "file-not-a-dir"
        blocked.write_text("in the way")
        monkeypatch.setenv("LEM_DATA_DIR", str(blocked / "nested"))
        assert web_app.configure_logging() == ""
        app = web_app.create_app(FakeLabCoreGateway())
        assert app is not None


class TestTheAppOpensItAndSaysWhereItIs:
    def test_create_app_configures_it(self, tmp_path, monkeypatch, clean_root):
        import web_app
        monkeypatch.setenv("LEM_DATA_DIR", str(tmp_path))
        app = web_app.create_app(FakeLabCoreGateway())
        assert app.config["LOG_PATH"] == str(tmp_path / "lem.log")
        assert [h for h in logging.getLogger().handlers
                if getattr(h, "_lem", False)]

    def test_healthz_says_where_the_log_is(self, tmp_path, monkeypatch,
                                           clean_root):
        """Nobody can read a file they cannot find, and this server has no
        console to print the path to."""
        import web_app
        monkeypatch.setenv("LEM_DATA_DIR", str(tmp_path))
        app = web_app.create_app(FakeLabCoreGateway())
        app.config["TESTING"] = True
        body = app.test_client().get("/healthz").get_json()
        assert body["log"] == str(tmp_path / "lem.log")


class TestTheTriageTableSendsPeopleToIt:
    def test_releasing_md_names_the_log(self):
        """RELEASING.md §7 sent every symptom to `updater.log`, which knows
        about deploys and nothing about a lab whose writes are being refused."""
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        doc = os.path.join(os.path.dirname(here), "RELEASING.md")
        text = open(doc, encoding="utf-8").read()
        triage = text.split("## 7.", 1)[1]
        assert "lem.log" in triage
        assert "data\\lem.log" in triage or "data/lem.log" in triage
