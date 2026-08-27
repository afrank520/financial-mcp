import textwrap

from financial_mcp.config import Config, load_config


def test_defaults_when_no_file():
    cfg = load_config(None)
    assert isinstance(cfg, Config)
    assert cfg.write_mode == "off"  # read-only unless the user opts in
    assert cfg.batch_cap == 200
    assert cfg.cache_ttls.transactions == 12 * 3600
    assert cfg.cache_ttls.categories == 7 * 24 * 3600


def test_yaml_overrides_defaults(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent("""
            write_mode: "off"
            batch_cap: 50
            cache_ttls:
              transactions: 3600
            """))
    cfg = load_config(str(p))
    assert cfg.write_mode == "off"
    assert cfg.batch_cap == 50
    assert cfg.cache_ttls.transactions == 3600
    # untouched TTL keeps its default
    assert cfg.cache_ttls.balances == 12 * 3600


def test_invalid_write_mode_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("write_mode: wideopen\n")
    try:
        load_config(str(p))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_source_defaults_to_csv():
    cfg = load_config(None)
    assert cfg.source == "csv"
    assert cfg.keyring_service_name == "financial_mcp_monarch"


def test_source_live_and_service_name_override(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("source: live\nkeyring_service_name: my_service\n")
    cfg = load_config(str(p))
    assert cfg.source == "live"
    assert cfg.keyring_service_name == "my_service"


def test_invalid_source_rejected(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("source: cloud\n")
    try:
        load_config(str(p))
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_credential_store_defaults_to_file():
    from financial_mcp.config import Config

    cfg = Config()
    assert cfg.credential_store == "file"
    assert cfg.credential_file == "~/.financial_mcp/session.json"
