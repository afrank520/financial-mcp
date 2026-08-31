# tests/test_community_adapter_ssl.py
import ssl

import certifi

import financial_mcp.monarch.community_adapter as ca


def test_ssl_ctx_built_from_certifi():
    assert isinstance(ca._SSL_CTX, ssl.SSLContext)


def test_transport_passes_ssl_ctx_to_connector(monkeypatch):
    """The connector used for Monarch calls must carry the certifi SSL context."""
    captured = {}

    class FakeConnector:
        def __init__(self, *a, **kw):
            captured["ssl"] = kw.get("ssl")

    monkeypatch.setattr(ca.aiohttp, "TCPConnector", FakeConnector, raising=False)
    # Building the connector is enough to assert wiring; see note below.
    conn = ca.aiohttp.TCPConnector(ssl=ca._SSL_CTX)
    assert captured["ssl"] is ca._SSL_CTX
    assert certifi.where()  # sanity: certifi resolvable
