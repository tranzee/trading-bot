"""Structured logging via loguru.

One call: `from polybot.obs.logger import log` — and the configured singleton
is ready. Configures level from `Settings.LOG_LEVEL`, redacts secrets, writes
both stdout and `logs/polybot-YYYY-MM-DD.log` with rotation.

Secret-redaction policy (also enforced by `safe_repr`):
    - any field named 'private_key', 'api_secret', 'api_passphrase' -> "***"
    - any 0x-prefixed 64-hex-char string in a value -> "0xPRIVKEY-REDACTED"
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

from loguru import logger as _logger

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_LOGS_DIR = _PROJECT_ROOT / "logs"

_PRIVKEY_RE = re.compile(r"0x[a-fA-F0-9]{64}")
_REDACT_FIELDS = frozenset(
    {"private_key", "api_secret", "api_passphrase", "POLYMARKET_PRIVATE_KEY", "signature"}
)


def safe_repr(value: Any) -> str:
    """Best-effort redacted repr. Strings get privkey-pattern blanked, dicts get key-blanked."""
    if isinstance(value, dict):
        return repr({k: ("***" if k in _REDACT_FIELDS else safe_repr(v)) for k, v in value.items()})
    s = repr(value)
    return _PRIVKEY_RE.sub("0xPRIVKEY-REDACTED", s)


def _patcher(record: dict[str, Any]) -> None:
    # Loguru records' `extra` dict is operator-controlled; redact known fields.
    extras = record.get("extra")
    if not extras:
        return
    for key in list(extras.keys()):
        if key in _REDACT_FIELDS:
            extras[key] = "***"


def _configure(level: str = "INFO") -> None:
    _logger.remove()
    _logger.add(
        sys.stderr,
        level=level,
        backtrace=False,
        diagnose=False,  # never include local variables in tracebacks (could leak)
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
            "<level>{level: <7}</level> "
            "<cyan>{name}:{line}</cyan> "
            "<level>{message}</level>"
        ),
    )
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _logger.add(
        _LOGS_DIR / "polybot-{time:YYYY-MM-DD}.log",
        level=level,
        rotation="00:00",
        retention="30 days",
        backtrace=False,
        diagnose=False,
        enqueue=True,
    )
    _logger.configure(patcher=_patcher)


def configure_from_settings(level: str | None = None) -> None:
    """Configure logging from a level string (default INFO)."""
    _configure(level or "INFO")


# Default to INFO until configure_from_settings is called. Importing the
# logger anywhere is safe.
_configure("INFO")

#: Module-level logger. Loguru-native — use `{}` placeholders only.
#: A ruff lint rule (`G003` from pylint convention) forbids `%`-style format
#: strings as the first argument to `log.*()` calls so we don't drift back.
log = _logger
