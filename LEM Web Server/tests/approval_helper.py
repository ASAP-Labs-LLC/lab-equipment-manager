"""Hide rows in a test store the only way the store allows (D7).

Since round 4 an approval reaches the store through `record_approval` alone
(a plain INSERT is refused), it carries the HMAC of the approval flow, and it
NAMES every row it covers; a hide is refused for any row its approval does
not name. Tests that need a hidden row for some other purpose (an aged-out
reading, a retired history) go through here, so they exercise the same road
a real hide takes.
"""

from __future__ import annotations

import collections
from typing import Iterable, List, Optional


def signed_approval(store, uid: str, rule: str, members: Iterable[int],
                    by: str = "ryan", at: str = "2026-10-01T09:00:00",
                    decision: str = "approved",
                    run_id: str = "upto=1;sha=test") -> int:
    ids = [int(i) for i in members] if decision == "approved" else []
    row = {"machine_uid": uid, "rule": rule, "run_id": run_id,
           "candidates": max(1, len(ids)), "approved_by": by,
           "approved_at": at, "decision": decision}
    return store.record_approval(signature=store.sign_approval(row),
                                 members=ids, **row)


def hide(store, where: str, args: Optional[list] = None,
         label: str = "replay_duplicate") -> List[int]:
    """Hide every effective row matching `where`, one approval per bench."""
    rows = store.read_sql(
        "SELECT id, machine_uid FROM lem_machine_log_effective WHERE "
        + where, args or [])
    assert "error" not in rows, rows
    per = collections.defaultdict(list)
    for r in rows["rows"]:
        per[r["machine_uid"]].append(r["id"])
    for uid, ids in per.items():
        aid = signed_approval(store, uid, label, ids)
        for rid in ids:
            res = store.sql(
                "INSERT INTO log_annotation (log_id, label, by, at, "
                "approval_id) VALUES (?, ?, 'test', 't', ?)",
                [rid, label, aid])
            assert "error" not in res, res
    return sorted(i for ids in per.values() for i in ids)
