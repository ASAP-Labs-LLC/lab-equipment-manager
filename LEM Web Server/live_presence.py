"""The live road: what each bench says about itself, held in memory only.

Two roads carry different facts. LabCore keeps the **record** — results, QC
verdicts, history, specs, corrections. This keeps **liveness**: I am running, my
status is now X, I just parsed L-1234. Only the module can know those, and today
the floor merely infers them from the age of a `lem_machine_heartbeat` row the
module writes every five minutes through the same queue as everything else.

Nothing here is persisted and nothing is ever written back to LabCore — the
module already wrote the record. An entry ages out on its own, and the floor
falls back to the record when it does, so a server restart or a bench losing its
path to us degrades to exactly today's behaviour rather than a blank floor.

See docs/superpowers/specs/2026-08-05-live-push-channel-design.md.
"""
import logging
import secrets
import threading
import time
from datetime import datetime

from labcore_result import refusal_of

logger = logging.getLogger(__name__)

# The TTL is per machine, not fixed. The module offers poll intervals of 15s,
# 30s, 60s and 5 min; a fixed 90s window would make a 5-minute bench read live
# for 90s and from-record for the remaining 3½ — every cycle, visibly flapping.
DEFAULT_TTL = 90.0
TTL_MULTIPLIER = 2.5
MAX_TTL = 1200.0        # a bogus interval must not pin a dead bench as live
MAX_MACHINES = 256

LIVE_URL_KEY = "live_url"
LIVE_TOKEN_KEY = "live_token"


META_DDL = "CREATE TABLE IF NOT EXISTS lem_meta (key TEXT PRIMARY KEY, value TEXT)"
META_UPSERT = ("INSERT INTO lem_meta (key, value) VALUES (?, ?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value")


def resolve_token(configured=None) -> str:
    """The token the push endpoint checks. Nobody types one.

    An explicit value (LEM_LIVE_TOKEN) is used as given; otherwise one is
    generated. What it is worth: it lives in `lem_meta`, so anything that can
    read LabCore can read it. It stops a stray host on the lab LAN — or a test
    script with a typo'd machine_uid — from repainting the floor. It is not a
    defence against someone who already has LabCore access, and it does not need
    to be: nothing pushed is authoritative and all of it expires on its own.
    """
    token = str(configured or "").strip()
    return token or secrets.token_urlsafe(32)


def _publish_one(gateway, sql, args=None) -> str:
    """Issue one publish write. "" if it landed, else why it did not.

    Never raises — see `publish_live_config`. Both ways a write can fail come
    back the same way, because "the address was not published" is one fact
    whether the socket died or the queue said no.
    """
    try:
        res = gateway.sql(sql, args or [])
    except Exception as exc:                        # transport, not logic
        return "{0}: {1}".format(type(exc).__name__, exc)
    return refusal_of(res) or ""


def publish_live_config(gateway, url: str, token: str) -> bool:
    """Put the server's address and token where every module already looks.

    In `lem_meta`, so a bench that moves to another PC picks them up with no
    local setup — the reason this is not a module setting. Called at BOOT, never
    from create_app: a factory with side effects gives every test a LabCore
    write, the same trap the snapshot poller taught.

    NEVER RAISES — AND, SINCE 2026-08-25, NEVER SILENT. The old body was three
    unread `gateway.sql()` calls inside a bare `except Exception: return`, and
    the docstring defended the non-raising. That defence is still right: a
    queue that is full at boot must not take the floor with it. It was never a
    defence for not LOOKING.

    What the silence cost: every bench reads its push address out of `lem_meta`
    (`build_live_config_query`). A refused publish leaves that stale or absent,
    so the live push goes nowhere — and the live road is best-effort by design,
    so nothing anywhere complains. The floor's dots and blips fall back to a 12s
    snapshot and a five-minute heartbeat, and the first symptom is somebody
    saying the floor feels slow.

    Returns True when all three writes were acknowledged. Every one is
    attempted and judged rather than stopping at the first: the DDL succeeds
    whenever the table already exists, which is every boot but the first, so
    reporting only its verdict would report nothing.
    """
    if not str(url or "").strip():
        return False
    trouble = [t for t in (
        _publish_one(gateway, META_DDL),
        _publish_one(gateway, META_UPSERT, [LIVE_URL_KEY, str(url).strip()]),
        _publish_one(gateway, META_UPSERT,
                     [LIVE_TOKEN_KEY, str(token or "")]),
    ) if t]
    if trouble:
        logger.warning(
            "the live push address was NOT published to lem_meta (%s). Benches "
            "will keep using whatever they last cached, or skip the push "
            "entirely; the floor falls back to the snapshot and the heartbeat.",
            "; ".join(trouble))
        return False
    return True


def _primary_ip() -> str:
    """The address this host is reachable on from the lab, without asking DNS.

    Connecting a UDP socket sends no packets; it only makes the OS choose the
    interface it would route through, which is the one the benches are on.
    """
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        sock.close()


def live_url(host, port, env_url=None, resolver=None) -> str:
    """The address to publish for benches to POST to.

    A wildcard bind must never be published as-is: a module cannot POST to
    0.0.0.0, and handing every bench an address that can never work would fail
    silently — the push is best-effort by design, so nothing would complain.
    """
    explicit = str(env_url or "").strip()
    if explicit:
        return explicit.rstrip("/")
    name = str(host or "").strip()
    if name in ("", "0.0.0.0", "::", "*"):
        name = (resolver or _primary_ip)()
    return f"http://{name}:{int(port)}"


def start_live_channel(app, gateway, host, port) -> str:
    """Boot step: tell LabCore where the floor listens and with what token.

    Called from web_server.pyw, never from create_app — the factory stays free
    of side effects.
    """
    import os
    url = live_url(host, port, os.environ.get("LEM_LIVE_URL"))
    publish_live_config(gateway, url, app.config.get("LIVE_TOKEN", ""))
    return url


def ttl_for(interval_seconds) -> float:
    """How long a push from a bench on this interval stays live.

    2.5× its own poll interval, floored at 90s so a fast bench does not flicker
    on one missed push, capped so a nonsense interval cannot make a dead bench
    look alive for hours.
    """
    try:
        interval = float(interval_seconds or 0.0)
    except (TypeError, ValueError):
        interval = 0.0
    return min(MAX_TTL, max(DEFAULT_TTL, interval * TTL_MULTIPLIER))


def _moment(value):
    """A pushed `at` as something comparable, or None if it is not a timestamp."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def merge_machines(machines, live, status_colors: dict) -> list:
    """The failover rule, applied to the `/api/machines` payload.

    **Live entry if one is fresh; otherwise the record, flagged `live: false`.**
    One source supplies a value at any moment — a failover, never a merge of two
    sources into one field, because a precedence rule spanning two writers is
    what rots into "the floor shows a status LabCore never held".

    The record supplies everything the push does not carry (title, position, QC
    specs, maintenance). What the push carries, it carries completely: status,
    its colour, the reason, and the timestamp the bench stamped on it — a status
    overlaid without its colour would leave a green machine drawn red.
    """
    merged = []
    for machine in machines or []:
        machine = dict(machine)
        entry = live.get(machine.get("machine_uid")) if live else None
        if not entry or not entry.get("status"):
            machine["live"] = False
            merged.append(machine)
            continue
        status = entry["status"]
        machine["status"] = status
        machine["status_color"] = status_colors.get(
            status, status_colors.get("UNKNOWN", ""))
        machine["reason"] = entry.get("reason", "")
        if entry.get("at"):
            machine["updated_at"] = entry["at"]
            machine["last_poll"] = entry["at"]
        if entry.get("last_parse_at"):
            machine["last_activity"] = entry["last_parse_at"]
        # A module talking to us IS the liveness signal. Today's "running" is
        # inferred from the age of a heartbeat written every five minutes
        # through the queue; this is the module itself, seconds ago.
        machine["state"] = "running"
        machine["live"] = True
        merged.append(machine)
    return merged


def live_events(live) -> list:
    """Parse blips straight off the live road, in `/api/events` shape.

    Keyed the way the floor already dedupes (`machine_uid|ts|lab_id`), so the
    same run arriving later through `lem_machine_log` does not blip twice.
    """
    events = []
    for uid, entry in (live.all() if live else {}).items():
        if not entry.get("last_parse_at"):
            continue
        events.append({"machine_uid": uid, "ts": entry["last_parse_at"],
                       "kind": "run", "lab_id": entry.get("lab_id", ""),
                       "test_name": "", "value": "", "detail": ""})
    events.sort(key=lambda e: e["ts"], reverse=True)
    return events


def merge_events(live_rows: list, recorded: list, limit: int) -> list:
    """Live blips ahead of the recorded ones, newest first, no run twice.

    The floor dedupes on `machine_uid|ts|lab_id`; this dedupes on the same key
    so a run that arrives here first and through LabCore later is one blip.
    """
    seen, out = set(), []
    for event in list(live_rows or []) + list(recorded or []):
        key = (event.get("machine_uid"), event.get("ts"), event.get("lab_id"))
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    out.sort(key=lambda e: e.get("ts") or "", reverse=True)
    return out[:limit]


class LivePresence:
    """machine_uid → what it last told us, until that ages out.

    Thread-safe: the Flask dev server is threaded, the same reason
    FakeLabCoreGateway carries a lock.
    """

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict = {}

    def record(self, machine_uid: str, payload: dict) -> bool:
        """Store one push. False if it was refused (no uid, or out of order)."""
        uid = str(machine_uid or "").strip()
        if not uid:
            return False
        payload = payload or {}
        entry = {
            "status": str(payload.get("status") or ""),
            "reason": str(payload.get("reason") or ""),
            "at": str(payload.get("at") or ""),
            "last_parse_at": str(payload.get("last_parse_at") or ""),
            "lab_id": str(payload.get("lab_id") or ""),
            "seen": self._clock(),
            "ttl": ttl_for(payload.get("interval_seconds")),
        }
        with self._lock:
            previous = self._entries.get(uid)
            if previous is not None:
                was, now = _moment(previous.get("at")), _moment(entry["at"])
                if was is not None and now is not None and now < was:
                    # A POST delayed in flight must not undo a newer state.
                    return False
            # Re-inserted so the cap evicts in arrival order.
            self._entries.pop(uid, None)
            self._entries[uid] = entry
            while len(self._entries) > MAX_MACHINES:
                self._entries.pop(next(iter(self._entries)))
        return True

    def get(self, machine_uid: str):
        """What this bench last said, or None once it has aged out."""
        with self._lock:
            entry = self._entries.get(str(machine_uid or "").strip())
            if entry is None:
                return None
            if self._clock() - entry["seen"] > entry["ttl"]:
                return None
            return dict(entry)

    def all(self) -> dict:
        """Every machine still within its TTL."""
        with self._lock:
            now = self._clock()
            return {uid: dict(entry)
                    for uid, entry in self._entries.items()
                    if now - entry["seen"] <= entry["ttl"]}
