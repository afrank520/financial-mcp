from __future__ import annotations

import logging
import sys

from financial_mcp.config import Config, ensure_parent


def mask(value: str) -> str:
    s = str(value)
    if len(s) <= 4:
        return "****"
    return "*" * (len(s) - 4) + s[-4:]


def mask_mapping(d: dict, sensitive_keys: set[str]) -> dict:
    lowered = {k.lower() for k in sensitive_keys}
    return {k: (mask(str(v)) if k.lower() in lowered else v) for k, v in d.items()}


def configure_logging(cfg: Config) -> logging.Logger:
    logger = logging.getLogger("financial_mcp")
    logger.handlers.clear()
    logger.setLevel(cfg.log_level.upper())
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(fmt)
    logger.addHandler(stderr_handler)

    if cfg.log_file:
        ensure_parent(cfg.log_file)
        file_handler = logging.FileHandler(cfg.log_file, encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger
