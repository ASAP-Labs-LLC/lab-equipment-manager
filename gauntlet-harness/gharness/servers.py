"""Build the Flask app HServer forwards to, for whichever server the target has.

v4 shape (spec §5.3): `create_app(LocalStoreGateway(LEM_STORE_PATH), labcore=...)`
— looked up by name; the piece that adds it (P6) makes this branch live.
v3.9 shape: `create_app(gateway)` with LabCore as the only store.

Either way the server's LabCore is the SAME fake the bench writes to (it is one
LabCore), seen through `ServerLabCore`, which counts the server's own ops so
they never mix into the bench's counts — and counts how many of them were
`lem_*` statements (`lem_sql_to_labcore`, spec §15.6; §5.3's S4 says it is 0
with the bridge off).
"""
import importlib
import inspect
import os
from collections import Counter

STORE_GATEWAY_MODULES = ("local_store", "lem_store", "store_gateway", "labcore_gateway")


class ServerLabCore:
    def __init__(self, fake):
        self.fake = fake
        self.counts = Counter()
        self.lem_sql = 0

    def _count(self, kind, sql=""):
        self.counts[kind] += 1
        if "LEM_" in str(sql).upper():
            self.lem_sql += 1

    def is_running(self):
        return True

    def sql(self, sql, args=None, **kw):
        self._count("sql", sql)
        return self.fake.sql(sql, args)

    def read_sql(self, sql, args=None, **kw):
        self._count("read", sql)
        return self.fake.read_sql(sql, args)

    def write(self, operation, params=None, **kw):
        self._count("write", (params or {}).get("sql", ""))
        return self.fake.write(operation, params or {})

    def get_test_names(self, **kw):
        self._count("read")
        return self.fake.get_test_names()

    def get_samples(self, **kw):
        self._count("read")
        return self.fake.get_samples()


class _Auth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


def find_store_gateway():
    for name in STORE_GATEWAY_MODULES:
        try:
            m = importlib.import_module(name)
        except ImportError:
            continue
        cls = getattr(m, "LocalStoreGateway", None)
        if cls is not None:
            return cls
    return None


#: The shared live token, as the server publishes it to lem_meta at boot (a
#: v4 bench proves a known uid's FIRST enrolment with it, transfer §6.4).
SHARED_TOKEN = "gate-shared-live-token"


def bench_uids(world):
    return [getattr(world, "uid", None) or "b1"]


def seed_known_bench(store, uid, title=None):
    """The bench as an imported LEM store knows it (§10.1): its machine
    configuration and its status row. Without them a v4 bench's enrolment is
    "LEM does not know this uid; a person must approve it" — correct, and not
    what any scenario but T4 is about."""
    from snapshot_service import SCHEMA_DDL
    for stmt in SCHEMA_DDL:
        if stmt.startswith("CREATE TABLE IF NOT EXISTS lem_machine_status ") \
                or stmt.startswith("CREATE TABLE IF NOT EXISTS lem_machine_config "):
            res = store.sql(stmt)
            if res.get("error"):
                raise RuntimeError("store DDL: " + res["error"])
    for sql in ("INSERT OR IGNORE INTO lem_machine_config (machine_uid, title, "
                "config, updated_at) VALUES (?, ?, '{}', '2026-10-01T08:00:00')",
                "INSERT OR IGNORE INTO lem_machine_status (machine_uid, title, "
                "status, reason, updated_at) VALUES (?, ?, 'UNKNOWN', '', "
                "'2026-10-01T08:00:00')"):
        res = store.sql(sql, [uid, title or ("Bench " + uid)])
        if res.get("error"):
            raise RuntimeError("store seed: " + res["error"])


def server_factory(world):
    import web_app
    lab = ServerLabCore(world.gw.fake)
    world.server_labcore = lab
    params = inspect.signature(web_app.create_app).parameters
    Store = find_store_gateway()
    if Store is not None and "labcore" in params:
        path = os.environ["LEM_STORE_PATH"]
        store = Store(path)
        kw = {"live_token": SHARED_TOKEN} if "live_token" in params else {}
        app = web_app.create_app(store, labcore=lab, authenticator=_Auth(),
                                 secret="s", **kw)
        app.config["LEM_STORE"] = path
        for uid in bench_uids(world):
            seed_known_bench(store, uid)
        snaps = app.config.get("SNAPSHOTS")
        if snaps is not None:
            # The server's 12 s snapshot, built once here (its thread does
            # not run under a test client); a scenario that changes the
            # configuration refreshes it again (`World.lem_refresh`).
            snaps.refresh()
    else:
        app = web_app.create_app(lab, authenticator=_Auth(), secret="s")
    app.config["TESTING"] = True
    return app
