"""How often the pages ask, and why they are allowed to ask that often.

The endpoints the pages poll are served from memory in under 2ms at ZERO
LabCore operations — that is what the 2026-08-03 performance work bought. The
timers were never lowered afterwards, so a status change sat up to 30s in the
browser on top of the queue and the snapshot: the single largest fixed chunk
of the lag, and the cheapest to remove.

This guards against it drifting back. If someone needs to raise these, the
question to answer first is what changed about the zero-op property.

The old floor page had its own two timers; piece 14 deleted it. Every
page now, the walls included, hears about a change through ONE poller,
static/js/live.js, on `/api/ui/live` (0 LabCore ops: tests/test_ia_guards.py
counts). So the same promise is pinned there: while a page is visible, a
change is at most a few seconds away.
"""
import re
from pathlib import Path

import pytest

MAX_MS = 5000
LIVE = Path(__file__).resolve().parent.parent / "static" / "js" / "live.js"


@pytest.fixture
def live_js():
    return LIVE.read_text(encoding="utf-8")


def interval(src, name):
    match = re.search(rf"const\s+{name}\s*=\s*(\d+)", src)
    assert match, f"{name} is not declared in live.js"
    return int(match.group(1))


class TestThePagesAskOftenEnough:
    def test_the_visible_poll_is_seconds_not_half_a_minute(self, live_js):
        assert interval(live_js, "VISIBLE_MS") <= MAX_MS

    def test_the_constant_is_actually_used(self, live_js):
        """A constant nobody schedules with would pass the check above while
        the pages still polled every 30 seconds."""
        assert re.search(r"visible\s*\?\s*VISIBLE_MS\s*:", live_js)

    def test_thirty_seconds_is_only_the_hidden_tabs_rate(self, live_js):
        """30 s is the rate for a tab nobody is looking at, and only that."""
        assert re.findall(r"\b30000\b", live_js) == ["30000"]
        assert re.search(r"const HIDDEN_MS = 30000;", live_js)
