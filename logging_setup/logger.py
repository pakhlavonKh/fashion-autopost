"""Logging configuration with secret masking and rotating file support.

Per SDD §3.10 and SRS FR-7.1, FR-7.2 & NFR-6 (Security - no plaintext secrets in logs).
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
from typing import Sequence


class SecretMaskingFilter(logging.Filter):
    """Filter that masks sensitive tokens, API keys, and passwords from log records."""

    def __init__(self, secrets_to_mask: Sequence[str] | None = None) -> None:
        super().__init__()
        self.patterns: list[re.Pattern[str]] = []
        if secrets_to_mask:
            for s in secrets_to_mask:
                s_str = str(s).strip()
                if len(s_str) >= 6 and not s_str.startswith("mock-"):
                    self.patterns.append(re.compile(re.escape(s_str)))

        # Generic token pattern matching bot<token> or Bearer <token>
        self.generic_patterns = [
            re.compile(r"(bot\d+:[\w-]+)", re.IGNORECASE),
            re.compile(r"(Bearer\s+[\w\.-]{10,})", re.IGNORECASE),
            re.compile(r"(api[-_]?key[\"']?\s*[:=]\s*[\"']?)([\w-]{16,})", re.IGNORECASE),
        ]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pat in self.patterns:
            msg = pat.sub("[REDACTED_SECRET]", msg)
        for gen_pat in self.generic_patterns:
            msg = gen_pat.sub(r"\1[REDACTED]", msg)
        record.msg = msg
        record.args = ()
        return True


def setup_logging(
    log_dir: str | Path = "./logs",
    log_level: int = logging.INFO,
    secrets_to_mask: Sequence[str] | None = None,
) -> logging.Logger:
    """Configure rotating file and console logging."""
    path = Path(log_dir)
    path.mkdir(parents=True, exist_ok=True)
    log_file = path / "app.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers to avoid duplicates
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    mask_filter = SecretMaskingFilter(secrets_to_mask)

    # 1. Rotating File Handler (max 10MB, up to 5 rotations)
    file_handler = RotatingFileHandler(
        filename=str(log_file),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(mask_filter)
    root_logger.addHandler(file_handler)

    # 2. Console Stream Handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.addFilter(mask_filter)
    root_logger.addHandler(console_handler)

    return root_logger
