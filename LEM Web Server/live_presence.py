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

from labcore_result import refusal_of, retry_after

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

# How long a refused publish waits before the poller tries again. The same
# discipline, and the same numbers, as SnapshotService's schema retry: LabCore's
# queue is full for minutes, and a retry with no throttle is writes piling into
# a queue that is refusing BECAUSE it is full.
LIVE_RETRY_MIN = 30.0
LIVE_RETRY_MAX = 300.0

# The two things a bench re-reads from LabCore on a timer, and the reason it
# does: it has no way to be told they changed. Between them those two reads are
# ~64% of everything on a queue that serialises at ~1.5 ops/sec, and the answer
# is almost always "no change". A note on the push response replaces the timer.
#
# These strings are a wire contract with a program that CANNOT import this file
# (LabStation loads the station module as a lone file). A rename here is a
# silent no-op there — the bench would simply stop re-reading a table that had
# in fact changed — so an unrecognised kind is refused at the door rather than
# stored and handed to a bench that has no idea what to do with it.
STALE_CORRECTIONS = "corrections"
STALE_OVERRIDE = "override"
STALE_KINDS = (STALE_CORRECTIONS, STALE_OVERRIDE)


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


def _publish_attempt(gateway, url: str, token: str):
    """One full publish. Returns (ok, why_not, wait_hint).

    Split out of `publish_live_config` so `LiveConfigPublisher` can retry it
    and honour the `retry_after` LabCore sent, without re-deriving either the
    statements or the verdict.
    """
    if not str(url or "").strip():
        return False, "no address to publish", None
    answers = []
    for sql, args in ((META_DDL, None),
                      (META_UPSERT, [LIVE_URL_KEY, str(url).strip()]),
                      (META_UPSERT, [LIVE_TOKEN_KEY, str(token or "")])):
        try:
            res = gateway.sql(sql, args or [])
        except Exception as exc:                    # transport, not logic
            answers.append(("{0}: {1}".format(type(exc).__name__, exc), None))
            continue
        answers.append((refusal_of(res) or "", retry_after(res)))
    trouble = [why for why, _hint in answers if why]
    hints = [hint for _why, hint in answers if hint is not None]
    return (not trouble), "; ".join(trouble), (max(hints) if hints else None)


class LiveConfigPublisher:
    """Keeps trying to tell LabCore where the benches should push.

    THE ONE WRITE ON THIS BRANCH THAT DID NOT HEAL ITSELF (2026-08-25).
    `start_live_channel` published the address once, at boot, and a refusal was
    logged and forgotten — so every bench's fast path stayed dark until someone
    restarted LEM, and nobody would, because nothing looks broken. The floor
    keeps updating off the 12s snapshot; it just feels slow, forever.

    Everything else here is either retried by the thing that issues it or
    repeated by the operator who asked for it. This had neither, and LabCore's
    queue is full for minutes rather than days.

    Run from the snapshot poller (`SnapshotService.on_cycle`) — the thread that
    is already awake and already talking to LabCore. It NEVER raises for the
    same reason the boot publish never did: a raise there would stop the floor
    refreshing, which is a far worse outage than a dark fast path.
    """

    def __init__(self, gateway, url: str, token: str, clock=time.monotonic):
        self.gateway = gateway
        self.url = str(url or "").strip()
        self.token = token
        self._clock = clock
        self.published = False
        self.attempts = 0
        self.last_error = ""
        self._retry_at = 0.0
        self._backoff = 0.0

    def publish_if_due(self) -> bool:
        """Publish if it has not landed and the cooldown has passed.

        Returns whether the address is published. Once it is, this costs
        nothing forever after — no clock, no write — which matters because it
        runs on every poller cycle of every healthy lab.
        """
        if self.published:
            return True
        now = self._clock()
        if self.attempts and now < self._retry_at:
            return False
        self.attempts += 1
        ok, trouble, hint = _publish_attempt(self.gateway, self.url, self.token)
        if ok:
            self.published = True
            self.last_error = ""
            self._backoff = 0.0
            if self.attempts > 1:
                logger.info("the live push address reached lem_meta on attempt "
                            "%s (%s)", self.attempts, self.url)
            return True
        self.last_error = trouble
        self._backoff = min(max(self._backoff * 2.0, LIVE_RETRY_MIN),
                            LIVE_RETRY_MAX)
        # `max`, not `or`: a hint of 4s must not shorten the back-off, and a
        # hint of ten minutes must not be shortened BY it.
        self._retry_at = now + max(self._backoff, hint or 0.0)
        logger.warning(
            "the live push address was NOT published to lem_meta (%s). Benches "
            "will keep using whatever they last cached, or skip the push "
            "entirely; the floor falls back to the snapshot and the heartbeat. "
            "Retrying in %ss.", trouble, int(self._retry_at - now))
        return False


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
    ok, trouble, _hint = _publish_attempt(gateway, url, token)
    if not ok:
        if trouble:
            logger.warning(
                "the live push address was NOT published to lem_meta (%s). "
                "Benches will keep using whatever they last cached, or skip "
                "the push entirely; the floor falls back to the snapshot and "
                "the heartbeat.", trouble)
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
    publisher = LiveConfigPublisher(gateway, url,
                                    app.config.get("LIVE_TOKEN", ""))
    publisher.publish_if_due()
    # Left where the poller can find it. A refused boot publish is not the end
    # of the attempt any more — see LiveConfigPublisher.
    app.config["LIVE_PUBLISHER"] = publisher
    return url


def attach_to_poller(snapshots, publish) -> None:
    """Run `publish` on every snapshot cycle, KEEPING whatever was already there.

    `SnapshotService` has ONE `on_cycle` hook and more than one passenger wants
    it, because it is the thread that is already awake and already talking to
    LabCore — which is the whole reason a retry rides it rather than starting a
    thread of its own. `create_app` puts the correction-audit spool's retry
    there; `web_server.pyw` adds the live-address publisher on top, and only
    when the address was actually published.

    A bare `snapshots.on_cycle = publisher.publish_if_due` in the entry point is
    what this replaces, and the failure it had was invisible in the worst way:
    publishing is ON in production and OFF in dev and in every test, so the
    assignment would have silently stripped the §7.8.2 receipt retry from the
    live server alone, while everything green.

    Lives here rather than in `web_server.pyw` because that file cannot be
    imported off Windows (see tests/test_deployment.py::TestNoPublish), so
    anything written there can only ever be tested by grepping its text. This
    is the part with a rule in it; the entry point keeps the one line that says
    when to apply it.

    Both passengers are best-effort and isolated from each other: a retry that
    raised must not be able to stop the address being republished, which is the
    thing every bench's fast path depends on.
    """
    already = getattr(snapshots, "on_cycle", None)
    if already is None:
        snapshots.on_cycle = publish
        return

    def _cycle():
        try:
            already()
        except Exception:
            # Whatever it was has already reported itself; `SnapshotService.
            # _loop` records anything `publish` raises.
            pass
        publish()

    snapshots.on_cycle = _cycle


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
        # Notes are DELIBERATELY not kept in `_entries`, and this is the whole
        # reason they are a second dict rather than a field on the entry.
        # `_entries` is a cache of liveness and ages out on purpose — that is
        # what makes the floor fall back to the record. A note is a pending
        # instruction, and an instruction that ages out is a change silently
        # dropped: a bench switched off on Friday, its correction factor
        # changed on Saturday, and on Monday it runs samples all week with the
        # old factor while nothing anywhere reports it. Notes therefore have no
        # TTL at all; the only thing that removes one is the bench collecting
        # it, or the cap below.
        #   uid -> {"pending": set(kind), "handed": set(kind)}
        self._stale: dict = {}

    # ── stale notes: "something you cache changed, go and re-read it" ──────

    def mark_stale(self, machine_uid: str, kind: str) -> bool:
        """Leave a note for one bench. False if there was nothing to record.

        Carries the KIND only, never the value. A correction factor travelling
        this road would make the floor a second writer of a fact LabCore owns,
        with no rule saying which wins — the same rot the failover in
        `merge_machines` was shaped to avoid. This says "go and look"; LabCore
        stays the only place that answers "at what".

        A uid nobody has ever heard of is stored regardless. Validating it
        would cost a LabCore lookup, and corrections are settable before an
        instrument has ever parsed anything, so "unknown" and "not yet" are the
        same shape from here. The cap is what makes that safe.
        """
        uid = str(machine_uid or "").strip()
        if not uid or kind not in STALE_KINDS:
            return False
        with self._lock:
            note = self._stale.pop(uid, None) or {"pending": set(),
                                                  "handed": set()}
            note["pending"].add(kind)
            # Re-inserted so the cap evicts in marking order: the oldest
            # uncollected note belongs to whatever is least likely to ever come
            # back for it.
            self._stale[uid] = note
            while len(self._stale) > MAX_MACHINES:
                self._stale.pop(next(iter(self._stale)))
        return True

    def take_stale(self, machine_uid: str) -> list:
        """The notes to hand this bench on the push being served right now.

        **Held for two pushes, not one.** A note found on push N is returned by
        push N and again by push N+1, then retired.

        Clearing on delivery is the obvious design and it is wrong, because the
        delivery is an HTTP response and a response can be lost in flight.
        `post_live` on the bench is best-effort with a 1.5s timeout and
        swallows everything, so a response that dies on the way back is silent
        at both ends — the note is gone, the bench never learns, and the
        15-minute backstop poll is the only thing left to catch it. Holding one
        extra round makes that self-heal on the next push, ~30s later. The
        price when nothing was lost is that the bench re-reads LabCore once for
        a change it already has: at most ONE redundant read per change, against
        removing ~64% of the queue's traffic.

        Marking again while a note is held restarts the window rather than
        riding out the old one — the second change is a change in its own right
        and must reach the bench.
        """
        uid = str(machine_uid or "").strip()
        if not uid:
            return []
        with self._lock:
            note = self._stale.get(uid)
            if note is None:
                return []
            # Everything outstanding goes out: what was just marked, plus what
            # went out on the previous push and is being repeated.
            due = note["pending"] | note["handed"]
            # Whatever was repeated this time has now had both its rounds and
            # is retired; what is going out for the FIRST time gets one more.
            note["handed"] = set(note["pending"])
            note["pending"] = set()
            if not note["handed"]:
                self._stale.pop(uid, None)
            return sorted(due)

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
