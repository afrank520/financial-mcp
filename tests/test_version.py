from financial_mcp.version import __version__


def test_version_is_semver_string():
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
