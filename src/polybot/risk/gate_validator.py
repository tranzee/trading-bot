"""Per-configuration Phase 3 gate validator.

The runtime gate refuses to enter live mode for any configuration whose
priors file is:
    - missing                       (never backtested)
    - older than 60 days            (stale; market regime may have shifted)
    - aggregate_continuation < 0.54 (no measurable edge)
    - bootstrap_ci_5pct < 0.51      (edge is below threshold even pessimistically)
    - sample_size < 200             (too small to be statistically credible)

Returns a `GateResult` with `passed: bool` and a human-readable `reason`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config.bot_configuration import BotConfiguration


# Thresholds — same values as the offline backtester's gate, applied per-config
MIN_AGGREGATE: float = 0.54
MIN_BOOTSTRAP_CI_5PCT: float = 0.51
MIN_SAMPLE_SIZE: int = 200
MAX_AGE_DAYS: int = 60


@dataclass(frozen=True)
class GateResult:
    passed: bool
    reason: str
    priors_path: Path
    aggregate: float | None = None
    ci_5pct: float | None = None
    sample_size: int | None = None
    age_days: float | None = None


def validate_priors_for_config(config: BotConfiguration) -> GateResult:
    """Validate that `config`'s priors file passes the live-mode gate.

    Pure function: reads the file, returns GateResult. No side effects.
    """
    path = config.priors_path
    if not path.exists():
        return GateResult(
            passed=False,
            reason=f"priors file missing for config {config.name!r}: {path}",
            priors_path=path,
        )

    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return GateResult(
            passed=False,
            reason=f"priors file unreadable: {exc}",
            priors_path=path,
        )

    aggregate = float(raw.get("aggregate_continuation", 0.0))
    ci5 = float(raw.get("bootstrap_ci_5pct", 0.0))
    n = int(raw.get("sample_size", 0))
    generated_at = raw.get("generated_at")
    age_days: float | None = None
    if generated_at:
        try:
            gen = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
            if gen.tzinfo is None:
                gen = gen.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - gen).total_seconds() / 86400
        except ValueError:
            age_days = None

    if n < MIN_SAMPLE_SIZE:
        return GateResult(
            passed=False,
            reason=f"sample_size={n} < {MIN_SAMPLE_SIZE}",
            priors_path=path, aggregate=aggregate, ci_5pct=ci5,
            sample_size=n, age_days=age_days,
        )
    if aggregate < MIN_AGGREGATE:
        return GateResult(
            passed=False,
            reason=f"aggregate={aggregate:.4f} < {MIN_AGGREGATE}",
            priors_path=path, aggregate=aggregate, ci_5pct=ci5,
            sample_size=n, age_days=age_days,
        )
    if ci5 < MIN_BOOTSTRAP_CI_5PCT:
        return GateResult(
            passed=False,
            reason=f"bootstrap_ci_5pct={ci5:.4f} < {MIN_BOOTSTRAP_CI_5PCT}",
            priors_path=path, aggregate=aggregate, ci_5pct=ci5,
            sample_size=n, age_days=age_days,
        )
    if age_days is not None and age_days > MAX_AGE_DAYS:
        return GateResult(
            passed=False,
            reason=f"priors age {age_days:.1f}d > {MAX_AGE_DAYS}d (regime may have shifted)",
            priors_path=path, aggregate=aggregate, ci_5pct=ci5,
            sample_size=n, age_days=age_days,
        )

    return GateResult(
        passed=True,
        reason=(
            f"PASS: aggregate={aggregate:.4f} ci5={ci5:.4f} n={n}"
            + (f" age={age_days:.1f}d" if age_days is not None else "")
        ),
        priors_path=path, aggregate=aggregate, ci_5pct=ci5,
        sample_size=n, age_days=age_days,
    )
