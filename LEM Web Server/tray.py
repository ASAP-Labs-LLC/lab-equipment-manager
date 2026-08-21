#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tray.py — run the LEM server from the system tray, the way the old LEM did.

V4's `web_server.pyw` put a pystray icon in the tray with Open in Browser /
Restart / Quit. This keeps that shape and adds the two things it lacked:

  * **a console you can hide** — the window is useful when something is wrong
    and clutter the rest of the time (Windows only; nothing else can hide its
    own terminal, so the menu item disables itself rather than lying).
  * **an automatic restart when the code changes** — editing a template or an
    endpoint shouldn't need a manual bounce. Flask's own reloader can't be used
    here: it re-execs the process, which would take the tray icon with it.

Restarting means re-launching the script and exiting, which is also the only
reliable way to pick up changed Python. Everything here that can be tested
without a display is a plain function; `run_tray` is the only part that needs
pystray.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import webbrowser
from typing import Dict, List, Optional, Sequence

IS_WINDOWS = os.name == "nt"

# Only what a person edits. Watching `.venv` would mean thousands of files and a
# restart on every pip install; `data` holds caches the server writes itself,
# which would restart it in a loop.
WATCH_SUFFIXES = (".py", ".pyw", ".html", ".css", ".js")
SKIP_DIRS = {".venv", "venv", "__pycache__", ".pytest_cache", ".git", "tests",
             "data", "node_modules", ".mypy_cache"}

POLL_SECONDS = 1.0
# Editors save in bursts (write, rename, chmod). Wait for quiet before bouncing,
# or one save restarts the server three times.
DEBOUNCE_SECONDS = 0.8


# ── what to watch ──────────────────────────────────────────────────────────
def iter_watched_files(root) -> List[str]:
    """Every file worth restarting for, under `root`."""
    found: List[str] = []
    try:
        for dirpath, dirnames, filenames in os.walk(str(root)):
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                if name.endswith(WATCH_SUFFIXES):
                    found.append(os.path.join(dirpath, name))
    except OSError:
        return []
    return sorted(found)


def snapshot(root) -> Dict[str, float]:
    """path → mtime, for everything watched."""
    out: Dict[str, float] = {}
    for path in iter_watched_files(root):
        try:
            out[path] = os.path.getmtime(path)
        except OSError:
            continue
    return out


def changed_files(before: Dict[str, float],
                  after: Dict[str, float]) -> List[str]:
    """What moved between two snapshots — edited, added or deleted."""
    changed = [p for p, m in after.items() if before.get(p) != m]
    changed += [p for p in before if p not in after]
    return sorted(set(changed))


# ── the console window ─────────────────────────────────────────────────────
def can_toggle_console() -> bool:
    """Only Windows can hide the console it was launched in."""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        return bool(ctypes.windll.kernel32.GetConsoleWindow())
    except Exception:
        return False


def set_console_visible(visible: bool) -> bool:
    """Show or hide the console. Returns whether it actually happened."""
    if not can_toggle_console():
        return False
    try:
        import ctypes
        handle = ctypes.windll.kernel32.GetConsoleWindow()
        if not handle:
            return False
        SW_HIDE, SW_SHOW = 0, 5
        ctypes.windll.user32.ShowWindow(handle, SW_SHOW if visible else SW_HIDE)
        return True
    except Exception:
        return False


# ── restarting ─────────────────────────────────────────────────────────────
def restart_command(argv: Optional[Sequence[str]] = None) -> List[str]:
    """The command that re-launches this server exactly as it was started."""
    argv = list(sys.argv if argv is None else argv)
    return [sys.executable] + argv


def spawn_kwargs() -> dict:
    """Detach the replacement, so it outlives us and Ctrl+C in the old console
    doesn't kill it."""
    if IS_WINDOWS and hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


RESTART_ENV = "LEM_RESTARTING"
PORT_WAIT_SECONDS = 25.0


def port_is_free(host: str, port: int) -> bool:
    """Can something bind this port right now?

    Ask by *binding*, because that is literally the question. Probing with a
    connect answers a different one and gets it wrong in both directions:

      * a port held by a server that isn't accepting — mid-shutdown, or with a
        full listen backlog — refuses or times out and reads as free, which is
        how the bind race this exists to prevent comes back;
      * on Windows, `connect_ex` on a socket with a timeout returns
        WSAEWOULDBLOCK immediately instead of waiting for the refusal, so an
        idle port reads as occupied and the server refuses to start on a port
        with nothing whatsoever on it.

    Binding the way the real server is about to bind means this answer *is* the
    answer the real bind will give.
    """
    import errno
    import socket

    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        if not IS_WINDOWS:
            # Werkzeug sets this on POSIX, so a port left in TIME_WAIT by the
            # old server is bindable and has to read as free. Windows is
            # deliberately left alone: there SO_REUSEADDR lets you bind a port
            # out from under a live listener, which would make every check
            # answer "free" and defeat the whole handover.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host or "0.0.0.0", int(port)))
        except OSError as exc:
            # EACCES is how Windows reports a port reserved out from under us
            # (an excluded port range, a Hyper-V reservation). Nothing shows in
            # netstat, but we still can't have it, so it counts as occupied.
            if exc.errno in (errno.EADDRINUSE, errno.EACCES):
                return False
            # Anything else — a host that isn't one of ours, a bad address — is
            # not a collision, and waiting for it to clear would only stall and
            # then bury the real error. Let the actual bind raise it.
            return True
    return True


def wait_for_port_free(host: str, port: int,
                       timeout: float = PORT_WAIT_SECONDS) -> bool:
    """Block until the port frees up, or give up after `timeout`.

    A restart spawns the replacement and only then exits, so for a moment BOTH
    processes want the port. Without this wait the child dies instantly with
    "Address already in use" and the parent exits a moment later — which looks
    exactly like the restart having closed the program.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_is_free(host, port):
            return True
        time.sleep(0.25)
    return port_is_free(host, port)


def who_holds_port(port: int) -> str:
    """Best-effort description of whatever is sitting on the port.

    "Still in use" is only useful if it says *by what* — otherwise the operator
    has nothing to act on.
    """
    try:
        if IS_WINDOWS:
            out = subprocess.run(["netstat", "-ano"], capture_output=True,
                                 text=True, timeout=8).stdout
            for line in out.splitlines():
                if f":{port}" in line and "LISTEN" in line.upper():
                    pid = line.split()[-1]
                    name = ""
                    try:
                        tl = subprocess.run(
                            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                            capture_output=True, text=True, timeout=8).stdout
                        name = tl.split()[0] if tl.split() else ""
                    except Exception:
                        pass
                    return f"PID {pid}{f' ({name})' if name else ''}"
            return ""
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                             capture_output=True, text=True, timeout=8).stdout
        rows = [r.split() for r in out.splitlines()[1:] if r.split()]
        if rows:
            return f"PID {rows[0][1]} ({rows[0][0]})"
    except Exception:
        pass
    return ""


def how_to_free_port(port: int) -> str:
    """The exact command to clear it, for whichever platform this is."""
    if IS_WINDOWS:
        return (f'netstat -ano | findstr :{port}    then    '
                f'taskkill /PID <pid> /F')
    return f"lsof -ti:{port} | xargs kill"


def data_dir(root=None) -> str:
    """Where this server writes things it wants to keep.

    ``LEM_DATA_DIR`` when set, otherwise the code directory — so an existing
    shared-drive install behaves exactly as it always has.

    Under the deployment layout the code directory is a junction onto an
    immutable release that a deploy swaps wholesale, so anything written there
    is lost on the next deploy. LEM has very little at stake here, because its
    configuration lives in LabCore's ``lem_*`` tables and ``data/`` is
    regenerable cache — but ``restart.log`` is not regenerable, and losing it
    on a deploy means losing the record of the deploy that went wrong.
    """
    configured = os.environ.get("LEM_DATA_DIR", "").strip()
    if configured:
        return configured
    return str(root) if root else os.path.dirname(os.path.abspath(
        sys.argv[0] or __file__))


def log_path(root=None) -> str:
    """Where restart diagnostics go.

    A `.pyw` launched by pythonw.exe has no console at all, so `print` goes
    nowhere and a failed restart looks like the program simply vanishing. This
    file is the only way to find out why.
    """
    return os.path.join(data_dir(root), "restart.log")


def note(message: str, root=None) -> None:
    """Say something both to the console (if there is one) and to the log."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    try:
        print(line, flush=True)
    except Exception:
        pass                            # no console: the file is the record
    try:
        target = log_path(root)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def relaunch(argv: Optional[Sequence[str]] = None, settle: float = 1.5,
             root=None) -> bool:
    """Start a replacement, confirm it is alive, then leave.

    Returns False WITHOUT exiting if the replacement could not be started or
    died immediately — the caller must keep the current server running in that
    case. Getting this backwards is how "restart" turned into "quit": the icon
    was stopped first, so when the spawn failed the main loop simply ended and
    the whole program closed with nothing to replace it.
    """
    argv = list(sys.argv if argv is None else argv)
    # An absolute script path with an explicit cwd: a relative argv[0] breaks
    # the moment anything has changed directory.
    if argv and not os.path.isabs(argv[0]):
        candidate = os.path.abspath(argv[0])
        if os.path.exists(candidate):
            argv[0] = candidate
    cwd = os.path.dirname(argv[0]) if argv and os.path.isabs(argv[0]) else None

    env = dict(os.environ)
    env[RESTART_ENV] = "1"          # tells the child to wait for the port
    cmd = [sys.executable] + argv
    try:
        child = subprocess.Popen(cmd, env=env, cwd=cwd, **spawn_kwargs())
    except Exception as exc:
        note(f"restart FAILED to spawn: {exc!r}  cmd={cmd}", root)
        return False

    time.sleep(settle)
    code = child.poll()
    if code is not None:
        note(f"restart FAILED: replacement exited immediately (rc={code}). "
             f"cmd={cmd}", root)
        return False
    note(f"restart: replacement pid={child.pid} is up — handing over", root)
    return True


def relaunch_and_exit(argv: Optional[Sequence[str]] = None, root=None) -> bool:
    """Hand over to a replacement and exit. Returns False if it couldn't, in
    which case this process is still running and must stay that way.

    os._exit, not sys.exit: the Flask server and the LabCore client both hold
    threads that would otherwise argue about shutting down.
    """
    if not relaunch(argv, root=root):
        return False
    os._exit(0)


# ── the menu ───────────────────────────────────────────────────────────────
def build_menu_spec(port: int, console_visible: bool = True,
                    can_toggle: Optional[bool] = None,
                    can_toggle_console: Optional[bool] = None,
                    watching: bool = True) -> List[dict]:
    """The tray menu as data, so its shape can be checked without a display."""
    toggle_ok = can_toggle_console
    if toggle_ok is None:
        toggle_ok = can_toggle if can_toggle is not None else globals()[
            "can_toggle_console"]()
    return [
        {"label": f"LEM server — port {port}", "enabled": False,
         "action": None},
        # Separators carry the same keys as everything else, so anything walking
        # the menu can do so without special-casing them.
        {"separator": True, "label": "", "enabled": False, "action": None},
        {"label": "Open in browser", "enabled": True, "default": True,
         "action": "open"},
        {"label": ("Hide console" if console_visible else "Show console"),
         "enabled": bool(toggle_ok), "action": "console"},
        {"label": ("Auto-restart on code change: on" if watching
                   else "Auto-restart on code change: off"),
         "enabled": True, "action": "watch"},
        # Separators carry the same keys as everything else, so anything walking
        # the menu can do so without special-casing them.
        {"separator": True, "label": "", "enabled": False, "action": None},
        {"label": "Restart server", "enabled": True, "action": "restart"},
        {"label": "Exit", "enabled": True, "action": "exit"},
    ]


# ── the icon ───────────────────────────────────────────────────────────────
def make_icon_image(size: int = 64):
    """A tray icon in LEM's own colours. Returns None if Pillow is missing —
    the server must still boot."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return None
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = max(2, size // 16)
    draw.ellipse([pad, pad, size - pad, size - pad],
                 fill="#21c071", outline="#0e1116",
                 width=max(2, size // 20))
    # An "L" struck through the middle, legible at 16px where text is not.
    x0, x1 = size * 0.36, size * 0.64
    y0, y1 = size * 0.28, size * 0.72
    w = max(2, size // 12)
    draw.line([x0, y0, x0, y1], fill="#0e1116", width=w)
    draw.line([x0, y1, x1, y1], fill="#0e1116", width=w)
    return img


def tray_available() -> bool:
    """Is a tray actually possible here?"""
    try:
        import pystray                              # noqa: F401
        from PIL import Image                       # noqa: F401
    except Exception:
        return False
    return True


# ── the watcher ────────────────────────────────────────────────────────────
class CodeWatcher:
    """Restart the server when a watched file changes.

    Polls mtimes rather than using a filesystem-notification library: this runs
    off a network share where those are unreliable, and one second of latency on
    a dev restart costs nothing.
    """

    def __init__(self, root, on_change, poll: float = POLL_SECONDS,
                 debounce: float = DEBOUNCE_SECONDS) -> None:
        self.root = str(root)
        self.on_change = on_change
        self.poll = poll
        self.debounce = debounce
        self.enabled = True
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="lem-code-watcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        state = snapshot(self.root)
        while not self._stop.is_set():
            time.sleep(self.poll)
            if not self.enabled:
                state = snapshot(self.root)        # don't bank up changes
                continue
            current = snapshot(self.root)
            changes = changed_files(state, current)
            if not changes:
                continue
            # Let the editor finish writing before bouncing.
            time.sleep(self.debounce)
            current = snapshot(self.root)
            changes = changed_files(state, current)
            state = current
            if changes:
                try:
                    self.on_change(changes)
                except Exception:
                    pass                            # never take the server down


def run_tray(host: str, port: int, serve, root, watch: bool = True) -> None:
    """Serve in a background thread and hold the tray icon on the main one.

    pystray must own the main thread on macOS, so the Flask server is the thing
    that gets moved off it.
    """
    import pystray

    url = f"http://{'localhost' if host in ('0.0.0.0', '') else host}:{port}"
    threading.Thread(target=serve, daemon=True, name="lem-flask").start()

    state = {"console": True, "watching": bool(watch), "restarting": False}
    icon: dict = {}

    def rebuild():
        if icon.get("i") is not None:
            icon["i"].menu = pystray.Menu(*to_pystray(spec()))
            icon["i"].update_menu()

    def spec():
        return build_menu_spec(port, console_visible=state["console"],
                              watching=state["watching"])

    def do(action):
        if action == "open":
            webbrowser.open(url)
        elif action == "console":
            if set_console_visible(not state["console"]):
                state["console"] = not state["console"]
                rebuild()
        elif action == "watch":
            state["watching"] = not state["watching"]
            watcher.enabled = state["watching"]
            print(f"Auto-restart on code change: "
                  f"{'on' if state['watching'] else 'off'}")
            rebuild()
        elif action == "restart":
            if state["restarting"]:
                return
            state["restarting"] = True
            note("restart requested from the tray", root)
            if not relaunch(root=root):
                # No replacement: stay up rather than leaving nothing running.
                note("staying up — see restart.log", root)
                state["restarting"] = False
                return
            if icon.get("i") is not None:
                icon["i"].stop()
            os._exit(0)
        elif action == "exit":
            if icon.get("i") is not None:
                icon["i"].stop()
            os._exit(0)

    def to_pystray(items):
        out = []
        for item in items:
            if item.get("separator"):
                out.append(pystray.Menu.SEPARATOR)
                continue
            action = item.get("action")
            out.append(pystray.MenuItem(
                item["label"],
                (lambda a: (lambda _i=None, _it=None: do(a)))(action)
                if action else None,
                enabled=item.get("enabled", True),
                default=bool(item.get("default"))))
        return out

    def on_code_change(changes):
        # Saving three files in a second must hand over ONCE. Several
        # replacements racing for the port means all but one dies with
        # "address already in use".
        if state["restarting"]:
            return
        state["restarting"] = True
        names = ", ".join(os.path.basename(c) for c in changes[:3])
        extra = f" (+{len(changes) - 3} more)" if len(changes) > 3 else ""
        note(f"code changed: {names}{extra} — restarting", root)
        if not relaunch(root=root):
            note("code change did NOT restart — staying on the old code", root)
            state["restarting"] = False
            return
        if icon.get("i") is not None:
            icon["i"].stop()
        os._exit(0)

    watcher = CodeWatcher(root, on_code_change)
    watcher.enabled = state["watching"]
    watcher.start()

    icon["i"] = pystray.Icon("LEM server", make_icon_image(64), "LEM server",
                             pystray.Menu(*to_pystray(spec())))
    print(f"LEM server in the system tray — {url}")
    print("Right-click the tray icon for options.")
    icon["i"].run()
