import textwrap

from financial_mcp.config import Config, load_config


def test_defaults_when_no_file():
    cfg = load_config(None)
    assert isinstance(cfg, Config)
    assert cfg.write_mode == "safe"
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
