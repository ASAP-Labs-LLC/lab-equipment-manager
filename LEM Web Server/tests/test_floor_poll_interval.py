"""How often the floor asks, and why it is allowed to ask that often.

Both endpoints the floor polls are served from the in-memory snapshot in under
2ms at ZERO LabCore operations — that is what the 2026-08-03 performance work
bought. The timers were never lowered afterwards, so a status change sat up to
30s in the browser on top of the queue and the snapshot: the single largest
fixed chunk of the lag, and the cheapest to remove.

This guards against it drifting back. If someone needs to raise these, the
question to answer first is what changed about the zero-op property.
"""
import re

import pytest

from labcore_gateway import FakeLabCoreGateway

MAX_MS = 5000


class StubAuth:
    def login(self, u, p):
        return ("kaden", "tok", "") if p == "good" else (None, "", "bad")

    def logout(self, t):
        pass


@pytest.fixture
def floor_html():
    from web_app import create_app
    app = create_app(FakeLabCoreGateway(), authenticator=StubAuth(), secret="s")
    app.config["TESTING"] = True
    return app.test_client().get("/floor").get_data(as_text=True)


def interval(html, name):
    match = re.search(rf"const\s+{name}\s*=\s*(\d+)", html)
    assert match, f"{name} is not declared in the floor template"
    return int(match.group(1))


class TestTheFloorAsksOftenEnough:
    def test_the_status_refresh_is_seconds_not_half_a_minute(self, floor_html):
        assert interval(floor_html, "FLOOR_REFRESH_MS") <= MAX_MS

    def test_the_run_blips_keep_up_with_it(self, floor_html):
        assert interval(floor_html, "BLIP_POLL_MS") <= MAX_MS

    def test_the_timers_are_actually_used(self, floor_html):
        """A constant nobody passes to setInterval would pass the check above
        while the floor still polled every 30 seconds."""
        assert re.search(r"setInterval\(.*?,\s*FLOOR_REFRESH_MS\s*\)",
                         floor_html, re.S)
        assert re.search(r"setInterval\(.*?,\s*BLIP_POLL_MS\s*\)",
                         floor_html, re.S)

    def test_no_raw_thirty_second_poll_survives(self, floor_html):
        assert "30000" not in floor_html, (
            "a 30-second timer is back on the floor")
