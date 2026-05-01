"""Runtime settings driven by environment variables.

This is the single typed entrypoint for everything in `.env`. All secrets
flow through here — no other module reads `os.environ` directly.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunMode(str, Enum):
    """Top-level run mode. Live requires explicit operator action."""

    PAPER = "paper"
    LIVE = "live"
    BACKTEST = "backtest"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """All process-wide settings loaded from `.env`."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # === Polymarket V2 ===
    POLYMARKET_PRIVATE_KEY: SecretStr = Field(
        description="Wallet private key. NEVER log; treat as the keys to the kingdom.",
    )
    POLYMARKET_FUNDER_ADDRESS: str = Field(
        description="Funder/proxy address that holds pUSD.",
        pattern=r"^0x[a-fA-F0-9]{40}$",
    )
    POLYMARKET_BUILDER_CODE: str = Field(
        default="",
        description="Optional V2 builder code; attached to every order via builder field.",
    )
    POLYMARKET_HOST: str = Field(
        default="https://clob-v2.polymarket.com",
        description=(
            "V2 host. Pre-April-28-2026 ~11:00 UTC: clob-v2.polymarket.com (testnet). "
            "On/after cutover: clob.polymarket.com (production). "
            "Same SDK, same code path — only this URL changes at cutover."
        ),
    )

    # === Network ===
    POLYGON_RPC_URL: str = Field(default="https://polygon-rpc.com")
    POLYGON_RPC_URL_FALLBACK: str = Field(default="")

    # === Modes ===
    RUN_MODE: RunMode = Field(default=RunMode.PAPER)
    LOG_LEVEL: LogLevel = Field(default=LogLevel.INFO)

    # === Risk caps (USD) ===
    WALLET_BALANCE: Annotated[Decimal, Field(ge=0)] = Decimal("100.0")
    MAX_SESSION_LOSS: Annotated[Decimal, Field(ge=0)] = Decimal("10.0")
    MAX_DAILY_LOSS: Annotated[Decimal, Field(ge=0)] = Decimal("20.0")
    MAX_PER_TRADE_USD: Annotated[Decimal, Field(ge=0)] = Decimal("5.0")
    MAX_PER_TRADE_FRACTION: Annotated[Decimal, Field(gt=0, le=1)] = Decimal("0.05")
    CONSECUTIVE_LOSS_LIMIT: Annotated[int, Field(ge=1)] = 5

    # === Truth layer ===
    BTC_SOURCES: str = Field(default="binance,coinbase,chainlink")
    DIVERGENCE_THRESHOLD_BPS: Annotated[int, Field(ge=0)] = 50
    SOURCE_STALE_TIMEOUT_S: Annotated[int, Field(ge=1)] = 5

    # === Strategy ===
    STRATEGY: str = Field(default="price_action_maker")
    MIN_SIGNAL_CONFIDENCE: Annotated[Decimal, Field(ge=0, le=1)] = Decimal("0.55")
    MAKER_DISCOUNT_TICKS: Annotated[int, Field(ge=0)] = 1
    CANCEL_REPLACE_MS: Annotated[int, Field(ge=50)] = 200
    EXIT_BEFORE_CLOSE_S: Annotated[int, Field(ge=0)] = 30

    # === Force flags ===
    FORCE_LIVE: bool = Field(default=False)
    SKIP_VERSION_CHECK: bool = Field(default=False)

    # === Paths (derived; not env-controlled) ===
    project_root: Path = PROJECT_ROOT
    state_dir: Path = PROJECT_ROOT / "state"
    logs_dir: Path = PROJECT_ROOT / "logs"
    strategy_params_path: Path = PROJECT_ROOT / "config" / "strategy_params.yaml"

    @field_validator("BTC_SOURCES")
    @classmethod
    def _validate_sources(cls, v: str) -> str:
        allowed = {"binance", "coinbase", "chainlink"}
        items = {s.strip().lower() for s in v.split(",") if s.strip()}
        if not items:
            raise ValueError("BTC_SOURCES must list at least one source")
        unknown = items - allowed
        if unknown:
            raise ValueError(f"Unknown BTC sources: {unknown}; allowed = {allowed}")
        return ",".join(sorted(items))

    @property
    def btc_sources(self) -> tuple[str, ...]:
        return tuple(s.strip().lower() for s in self.BTC_SOURCES.split(",") if s.strip())

    @property
    def is_live(self) -> bool:
        return self.RUN_MODE is RunMode.LIVE

    @property
    def is_paper(self) -> bool:
        return self.RUN_MODE is RunMode.PAPER

    @property
    def is_backtest(self) -> bool:
        return self.RUN_MODE is RunMode.BACKTEST


def load_settings() -> Settings:
    """Load settings, ensuring runtime directories exist."""
    settings = Settings()  # type: ignore[call-arg]
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    return settings
