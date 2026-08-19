"""The server lives in the system tray, like the old LEM.

V4 had a tray icon with Open in Browser / Restart / Quit. This keeps that and
adds the two things it lacked: a console you can hide, and an automatic restart
when the code changes so editing a template doesn't need a manual bounce.

Everything testable is pure: which files are watched, what counts as a change,
the menu's shape, and how a restart is spelled. pystray and a display are not
needed to check any of it — and the module must import on a headless box.
"""
import os
import sys
import time

import pytest

import tray


# ── what gets watched ───────────────────────────────────────────────────────

class TestWatchedFiles:
    def test_it_watches_python(self, tmp_path):
        (tmp_path / "web_app.py").write_text("x = 1")
        assert "web_app.py" in [os.path.basename(p)
                                for p in tray.iter_watched_files(tmp_path)]

    def test_it_watches_templates_and_css(self, tmp_path):
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "floor.html").write_text("<p>")
        (tmp_path / "static").mkdir()
        (tmp_path / "static" / "lem.css").write_text("p{}")
        names = [os.path.basename(p) for p in tray.iter_watched_files(tmp_path)]
        assert "floor.html" in names and "lem.css" in names

    def test_it_ignores_the_virtualenv(self, tmp_path):
        """Watching .venv means thousands of files and a restart on every pip."""
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "something.py").write_text("x = 1")
        assert tray.iter_watched_files(tmp_path) == []

    def test_it_ignores_caches_and_tests(self, tmp_path):
        for d in ("__pycache__", ".pytest_cache", "tests"):
            (tmp_path / d).mkdir()
            (tmp_path / d / "junk.py").write_text("x = 1")
        assert tray.iter_watched_files(tmp_path) == []

    def test_it_ignores_data_and_dotfiles(self, tmp_path):
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "cache.py").write_text("x = 1")
        (tmp_path / ".hidden.py").write_text("x = 1")
        assert tray.iter_watched_files(tmp_path) == []

    def test_an_unreadable_tree_is_no_files_not_a_crash(self):
        assert tray.iter_watched_files("/nonexistent/place") == []


class TestChangeDetection:
    def test_an_unchanged_tree_reports_nothing(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1")
        before = tray.snapshot(tmp_path)
        assert tray.changed_files(before, tray.snapshot(tmp_path)) == []

    def test_an_edited_file_is_reported(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x = 1")
        before = tray.snapshot(tmp_path)
        os.utime(f, (time.time() + 10, time.time() + 10))
        assert tray.changed_files(before, tray.snapshot(tmp_path)) == [str(f)]

    def test_a_new_file_is_reported(self, tmp_path):
        before = tray.snapshot(tmp_path)
        (tmp_path / "new.py").write_text("x = 1")
        assert len(tray.changed_files(before, tray.snapshot(tmp_path))) == 1

    def test_a_deleted_file_is_reported(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x = 1")
        before = tray.snapshot(tmp_path)
        f.unlink()
        assert tray.changed_files(before, tray.snapshot(tmp_path)) == [str(f)]

    def test_several_edits_are_all_reported(self, tmp_path):
        for n in ("a.py", "b.py"):
            (tmp_path / n).write_text("x = 1")
        before = tray.snapshot(tmp_path)
        later = time.time() + 10
        for n in ("a.py", "b.py"):
            os.utime(tmp_path / n, (later, later))
        assert len(tray.changed_files(before, tray.snapshot(tmp_path))) == 2


# ── the menu ────────────────────────────────────────────────────────────────

class TestMenu:
    def spec(self, **kw):
        return tray.build_menu_spec(port=5557, **kw)

    def test_it_offers_everything_asked_for(self):
        labels = " ".join(i["label"].lower() for i in self.spec())
        for want in ("console", "browser", "restart", "exit"):
            assert want in labels, want

    def test_the_port_is_on_show_so_you_know_which_server(self):
        assert "5557" in self.spec()[0]["label"]

    def test_the_header_is_not_clickable(self):
        assert self.spec()[0]["enabled"] is False

    def test_opening_the_browser_is_the_default_action(self):
        """Left-clicking the icon should do the obvious thing."""
        default = [i for i in self.spec() if i.get("default")]
        assert len(default) == 1 and "browser" in default[0]["label"].lower()

    def test_the_console_item_says_which_way_it_will_go(self):
        shown = [i for i in self.spec(console_visible=True)
                 if "console" in i["label"].lower()][0]
        hidden = [i for i in self.spec(console_visible=False)
                  if "console" in i["label"].lower()][0]
        assert shown["label"] != hidden["label"]
        assert "hide" in shown["label"].lower()
        assert "show" in hidden["label"].lower()

    def test_the_console_item_is_disabled_where_it_cannot_work(self):
        """Only Windows can hide its console window."""
        item = [i for i in self.spec(can_toggle_console=False)
                if "console" in i["label"].lower()][0]
        assert item["enabled"] is False

    def test_auto_reload_state_is_visible(self):
        on = " ".join(i["label"] for i in self.spec(watching=True)).lower()
        off = " ".join(i["label"] for i in self.spec(watching=False)).lower()
        assert on != off

    def test_exit_is_last(self):
        assert "exit" in self.spec()[-1]["label"].lower()


# ── restarting ──────────────────────────────────────────────────────────────

class TestRestart:
    def test_it_relaunches_this_same_script_with_its_arguments(self):
        cmd = tray.restart_command(["web_server.pyw", "--port", "5557"])
        assert cmd[0] == sys.executable
        assert cmd[1:] == ["web_server.pyw", "--port", "5557"]

    def test_it_survives_no_arguments(self):
        assert tray.restart_command([]) == [sys.executable]

    def test_the_child_is_detached(self):
        """A restart must outlive the process that asked for it, and Ctrl+C in
        the old console must not kill the new server."""
        kw = tray.spawn_kwargs()
        assert kw.get("start_new_session") or kw.get("creationflags")


# ── the console toggle ──────────────────────────────────────────────────────

class TestConsole:
    def test_asking_is_always_safe(self):
        """Whatever the platform, this must answer rather than raise."""
        assert tray.can_toggle_console() in (True, False)

    def test_toggling_where_unsupported_reports_failure_quietly(self):
        if tray.can_toggle_console():
            pytest.skip("this box can toggle its console")
        assert tray.set_console_visible(False) is False


# ── it has to import with no display and no pystray ─────────────────────────

class TestHeadless:
    def test_the_module_imports_without_a_tray(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pystray", None)
        import importlib
        importlib.reload(tray)
        assert hasattr(tray, "build_menu_spec")

    def test_it_says_whether_a_tray_is_possible(self):
        assert tray.tray_available() in (True, False)

    def test_making_an_icon_never_raises(self):
        """Missing Pillow must degrade, not stop the server booting."""
        assert tray.make_icon_image(16) is not None or True


# ── the entry point ─────────────────────────────────────────────────────────

class TestEntryPoint:
    def source(self):
        import pathlib
        return (pathlib.Path(__file__).resolve().parent.parent
                / "web_server.pyw").read_text()

    def test_the_tray_is_the_default(self):
        src = self.source()
        assert "--no-tray" in src            # opt OUT, not opt in
        assert "tray.run_tray(" in src

    def test_code_watching_can_be_turned_off(self):
        assert "--no-reload" in self.source()

    def test_flasks_own_reloader_is_never_used(self):
        """It re-execs the process, which would take the tray icon with it."""
        assert "use_reloader=False" in self.source()

    def test_it_falls_back_to_the_console_when_no_tray_is_possible(self):
        src = self.source()
        assert "tray.tray_available()" in src
        assert "Running in the console" in src


# ── restarting must not kill the server ─────────────────────────────────────
#
# `relaunch` spawns the replacement and only THEN exits, so for a moment both
# processes want the port. The child was dying instantly on "Address already in
# use" and the parent exited a moment later — which looks exactly like the
# restart having closed the program.

class TestPortHandover:
    def test_a_free_port_reads_as_free(self):
        assert tray.port_is_free("127.0.0.1", 5599) is True

    def test_a_held_port_reads_as_busy(self):
        import socket
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 5599)); s.listen(1)
        try:
            assert tray.port_is_free("127.0.0.1", 5599) is False
        finally:
            s.close()

    def test_waiting_returns_at_once_when_free(self):
        start = time.time()
        assert tray.wait_for_port_free("127.0.0.1", 5599, timeout=5) is True
        assert time.time() - start < 1

    def test_waiting_gives_up_rather_than_hanging(self):
        import socket
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 5599)); s.listen(1)
        try:
            start = time.time()
            assert tray.wait_for_port_free("127.0.0.1", 5599,
                                          timeout=1.0) is False
            assert time.time() - start < 4
        finally:
            s.close()

    def test_it_returns_as_soon_as_the_port_frees(self):
        import socket, threading
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 5599)); s.listen(1)
        threading.Timer(0.6, s.close).start()
        start = time.time()
        assert tray.wait_for_port_free("127.0.0.1", 5599, timeout=10) is True
        assert 0.3 < time.time() - start < 5

    def test_0_0_0_0_is_probed_on_loopback(self):
        """You can't connect to 0.0.0.0; the check has to ask localhost."""
        assert tray.port_is_free("0.0.0.0", 5599) is True

    def test_the_child_is_told_it_is_a_restart(self):
        assert tray.RESTART_ENV

    def test_a_timeout_is_not_read_as_free(self):
        """Something present but not accepting (mid-shutdown, or a full listen
        backlog) must count as occupied. Reading a timeout as "free" is how the
        bind race comes back."""
        import socket
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 5599))
        s.listen(1)
        held = []
        try:
            # Fill the backlog so further connects hang rather than refuse.
            for _ in range(6):
                c = socket.socket()
                c.settimeout(0.3)
                try:
                    c.connect(("127.0.0.1", 5599))
                    held.append(c)
                except OSError:
                    c.close()
            assert tray.port_is_free("127.0.0.1", 5599) is False
        finally:
            for c in held:
                c.close()
            s.close()


# ── a restart must never leave nothing running ──────────────────────────────
#
# The first cut stopped the tray icon and THEN spawned. When the spawn failed,
# `relaunch` returned without exiting — but the icon was already stopped, so
# `icon.run()` returned, `main()` returned, and the whole program closed with no
# replacement. Editing a file "immediately killed it".

class TestRestartNeverLosesTheServer:
    def test_a_failed_spawn_reports_false_and_does_not_exit(self, tmp_path,
                                                            monkeypatch):
        def boom(*a, **k):
            raise OSError("no such executable")
        monkeypatch.setattr(tray.subprocess, "Popen", boom)
        # If this exits the process, the test run itself dies — so reaching the
        # assert at all is part of what's being checked.
        assert tray.relaunch(["x.py"], settle=0, root=tmp_path) is False

    def test_a_replacement_that_dies_at_once_reports_false(self, tmp_path):
        script = tmp_path / "dies.py"
        script.write_text("import sys; sys.exit(3)")
        assert tray.relaunch([str(script)], settle=1.0,
                             root=tmp_path) is False

    def test_a_replacement_that_stays_up_reports_true(self, tmp_path):
        script = tmp_path / "lives.py"
        script.write_text("import time; time.sleep(20)")
        try:
            assert tray.relaunch([str(script)], settle=1.0,
                                 root=tmp_path) is True
        finally:
            import subprocess
            subprocess.run(["pkill", "-f", "lives.py"], capture_output=True)

    def test_the_failure_is_written_down(self, tmp_path, monkeypatch):
        """Under pythonw there is no console, so the log is the only record."""
        monkeypatch.setattr(tray.subprocess, "Popen",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
        tray.relaunch(["x.py"], settle=0, root=tmp_path)
        log = tmp_path / "restart.log"
        assert log.exists() and "FAILED" in log.read_text()

    def test_the_log_is_not_a_watched_file(self, tmp_path):
        """Writing the log must not itself trigger another restart."""
        (tmp_path / "restart.log").write_text("x")
        assert tray.iter_watched_files(tmp_path) == []

    def test_a_relative_script_path_is_made_absolute(self, tmp_path, monkeypatch):
        """A relative argv[0] breaks as soon as anything changes directory."""
        seen = {}

        class FakeProc:
            pid = 1234

            def poll(self):
                return None

        def spy(cmd, **kw):
            seen["cmd"] = cmd
            seen["cwd"] = kw.get("cwd")
            return FakeProc()

        monkeypatch.setattr(tray.subprocess, "Popen", spy)
        script = tmp_path / "web_server.pyw"
        script.write_text("")
        monkeypatch.chdir(tmp_path)
        assert tray.relaunch(["web_server.pyw"], settle=0, root=tmp_path) is True
        assert os.path.isabs(seen["cmd"][1])
        assert seen["cwd"] == str(tmp_path)

    def test_the_icon_is_only_stopped_after_a_confirmed_handover(self):
        """Source-level guard: in both restart paths the spawn must come first."""
        src = open(tray.__file__, encoding="utf-8").read()
        for marker in ("restart requested from the tray", "code changed:"):
            block = src[src.index(marker):][:520]
            spawn = block.index("relaunch(")
            stop = block.index('icon["i"].stop()')
            assert spawn < stop, f"icon stopped before spawning ({marker})"

    def test_both_paths_bail_out_rather_than_dying(self):
        src = open(tray.__file__, encoding="utf-8").read()
        for marker in ("restart requested from the tray", "code changed:"):
            block = src[src.index(marker):][:520]
            assert "if not relaunch(" in block, marker
            assert "return" in block, marker
