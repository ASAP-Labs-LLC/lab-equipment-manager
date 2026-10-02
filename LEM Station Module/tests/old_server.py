"""LEM as TODAY's server (v3.9) — one that has already answered 404.

Since T-P8 round 2 a bench whose v2 state is merely UNKNOWN does not take the
legacy road: only a 404 means "old server" (transfer spec §6.1, §12.2), so a
bench that has not heard one journals and holds. Most of this suite pins the
legacy road itself (heartbeat, DDL, log rows, the QC library read from
LabCore) on a fresh module whose uploader cannot reach anything
(`_no_real_network`) — which is "unknown", not "old server". Those tests
model M3, a v4 bench on a v3.9 server, so they start where such a bench is
after its first 404: in legacy mode.

A test that installs `fake_lem_v2.FakeLem` gets the real starting state, and
with it the real unknown → v2 / unknown → legacy behaviour, which
`test_bench_uploader.py` pins.
"""
import urllib.request


def _fake_lem_installed() -> bool:
    from fake_lem_v2 import FakeLem
    return isinstance(getattr(urllib.request.urlopen, "__self__", None),
                      FakeLem)


def transfer_state_class(real):
    """`real` (`_TransferState`), starting in legacy mode unless a FakeLem
    is answering for LEM."""
    class _AfterA404(real):
        def __init__(self):
            super().__init__()
            if not _fake_lem_installed():
                self.mode = "legacy"
    _AfterA404.__name__ = real.__name__
    return _AfterA404
