from financial_mcp.config import Config
from financial_mcp.utils.logging import configure_logging, mask, mask_mapping


def test_mask_keeps_last_four():
    assert mask("1234567890") == "******7890"


def test_mask_short_value():
    assert mask("abc") == "****"


def test_mask_mapping_is_case_insensitive_and_nondestructive():
    src = {"Token": "supersecretvalue", "amount": 42}
    out = mask_mapping(src, {"token"})
    assert out["Token"] == "************alue"
    assert out["amount"] == 42
    assert src["Token"] == "supersecretvalue"  # original untouched


def test_configure_logging_returns_named_logger():
    logger = configure_logging(Config(log_level="DEBUG", log_file=None))
    assert logger.name == "financial_mcp"
    assert logger.level == 10  # DEBUG
