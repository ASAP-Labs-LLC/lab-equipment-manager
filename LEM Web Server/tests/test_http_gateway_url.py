"""The production gateway must use the same connection point as the other apps.

Across the LabLink suite the connection point is a full base URL resolved from
LABCORE_URL, defaulting to https://labvision.asaplabs.net (LabCore's HTTP queue
reverse-proxied behind that hostname over HTTPS). Not localhost:8080.
"""

from labcore_gateway import HttpLabCoreGateway, DEFAULT_LABCORE_URL


def test_default_labcore_url_is_labvision():
    assert DEFAULT_LABCORE_URL == "https://labvision.asaplabs.net"


def test_gateway_defaults_to_labvision():
    gw = HttpLabCoreGateway()
    assert gw.base_url == "https://labvision.asaplabs.net"


def test_gateway_respects_labcore_url_env(monkeypatch):
    monkeypatch.setenv("LABCORE_URL", "https://labcore.example.net/")
    gw = HttpLabCoreGateway()
    # trailing slash trimmed
    assert gw.base_url == "https://labcore.example.net"


def test_gateway_respects_explicit_base_url():
    gw = HttpLabCoreGateway(base_url="http://192.168.1.5:8089")
    assert gw.base_url == "http://192.168.1.5:8089"


def test_underlying_client_targets_that_url():
    """The vendored LabCoreClient the gateway wraps must hit the resolved URL."""
    gw = HttpLabCoreGateway(base_url="https://labvision.asaplabs.net")
    assert gw._client.base_url == "https://labvision.asaplabs.net"
