"""Drift guard: forbid %-style format args in loguru log calls.

Loguru uses `{}` placeholder substitution. A `log.info("foo: %s", x)` call
silently emits the literal string `foo: %s` and stashes `x` as `extra`,
producing useless log lines. This test scans `src/polybot/` for the pattern
and fails if any drift back in.

The logger module itself is exempt (it defines the logger).
"""

from __future__ import annotations

import re
from pathlib import Path

_BAD_PATTERN = re.compile(
    r"""log\.(?:debug|info|warning|error|critical|exception|trace|success)\(
        [^)]*?                              # message + maybe earlier args
        ['"][^'"]*%[sdrfgex]                # %s / %d / %r / etc. inside a string literal
    """,
    re.VERBOSE | re.DOTALL,
)

# Files that are allowed to mention the pattern (the test itself, the doc string).
_EXEMPT = {
    "tests/unit/test_logger_format_drift.py",
    "src/polybot/obs/logger.py",  # docstrings reference the rule
}


def test_no_percent_style_in_loguru_calls() -> None:
    repo = Path(__file__).resolve().parents[2]
    src = repo / "src" / "polybot"
    offenders: list[str] = []
    for path in src.rglob("*.py"):
        rel = path.relative_to(repo).as_posix()
        if rel in _EXEMPT:
            continue
        text = path.read_text(encoding="utf-8")
        for match in _BAD_PATTERN.finditer(text):
            line_num = text[: match.start()].count("\n") + 1
            snippet = match.group(0).replace("\n", " ")
            offenders.append(f"{rel}:{line_num}: {snippet[:80]}")
    assert not offenders, (
        "Loguru uses `{}` placeholders, not %-style. Replace %s/%d/%r with {}:\n"
        + "\n".join(offenders)
    )
