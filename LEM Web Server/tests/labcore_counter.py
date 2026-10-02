"""A LabCore that counts — for the tests that need LabCore and the LEM store
to be two different things.

Most of this suite hands `create_app` ONE gateway, which then serves as both
the store and LabCore (see `create_app`'s docstring). That cannot tell a store
read from a LabCore op, and since transfer §5 those are different costs: a
store read is a local SQLite lookup, a LabCore op is a slot in the queue every
bench in the lab writes through. A test whose claim is "this costs LabCore
nothing" therefore puts THIS in LabCore's place and counts here.

`lem_statements` is S4's measure (transfer §5.3): how many statements naming
a `lem_*` table reached LabCore at all. With the bridge off it must be 0.
"""

import labcore_gateway


def _lab_fake_class():
    # NOT `FakeLabCoreGateway`, which conftest has made the store-backed one.
    return labcore_gateway.InMemoryLabCore


class CountingLabCore:
    """LabCore's three core tables, in memory, every call recorded."""

    def __init__(self, inner=None):
        self.fake = inner if inner is not None else _lab_fake_class()()
        self.calls = []                    # (method, statement or "")

    # ── what it counts ──
    @property
    def ops(self) -> int:
        return len(self.calls)

    @property
    def lem_statements(self) -> list:
        return [sql for _m, sql in self.calls if "LEM_" in sql.upper()]

    def _note(self, method, sql=""):
        self.calls.append((method, str(sql or "")))

    # ── LabCore's surface ──
    def is_running(self):
        return True                        # a status GET, never a queue op

    def sql(self, sql, args=None, **kw):
        self._note("sql", sql)
        return self.fake.sql(sql, args)

    def read_sql(self, sql, args=None, **kw):
        self._note("read_sql", sql)
        return self.fake.read_sql(sql, args)

    def write(self, operation, params=None, **kw):
        self._note("write", (params or {}).get("sql", operation))
        return self.fake.write(operation, params or {})

    def get_test_names(self, **kw):
        self._note("get_test_names")
        return self.fake.get_test_names()

    def get_samples(self, **kw):
        self._note("get_samples")
        return self.fake.get_samples()
