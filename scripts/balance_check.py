"""Print pUSD and USDC.e balances for the configured funder address.

Usage:
    python scripts/balance_check.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from config.settings import load_settings  # noqa: E402
from polybot.obs.logger import configure_from_settings  # noqa: E402
from polybot.poly.client import PolyClient  # noqa: E402
from polybot.poly.pusd import (  # noqa: E402
    COLLATERAL_ONRAMP_ADDRESS_POLYGON,
    PUSD_TOKEN_ADDRESS_POLYGON,
    PusdHelper,
)


async def main() -> int:
    settings = load_settings()
    configure_from_settings(settings.LOG_LEVEL.value)

    poly = PolyClient(settings)
    await poly.setup_creds()
    balances = await poly.get_balances()

    print("=" * 72)
    print(" Polymarket V2 balances")
    print("=" * 72)
    print(f"  funder           : {settings.POLYMARKET_FUNDER_ADDRESS}")
    print(f"  host             : {settings.POLYMARKET_HOST}")
    print(f"  pUSD (collateral): {balances.pusd}")

    # Best-effort on-chain pUSD/USDC.e snapshot (skipped if placeholder addrs).
    if PUSD_TOKEN_ADDRESS_POLYGON != "0x" + "0" * 40:
        helper = PusdHelper(settings)
        try:
            print(f"  pUSD (on-chain)  : {helper.get_pusd_balance()}")
            print(f"  USDC.e (on-chain): {helper.get_usdce_balance()}")
        except Exception as exc:  # noqa: BLE001
            print(f"  on-chain query   : skipped ({exc})")
    else:
        print("  on-chain query   : skipped (PUSD_TOKEN_ADDRESS placeholder)")
    if COLLATERAL_ONRAMP_ADDRESS_POLYGON == "0x" + "0" * 40:
        print()
        print("Note: Collateral Onramp address is a placeholder. Operator must")
        print("update poly/pusd.py with the real address from Polymarket's V2")
        print("migration guide before any wrap/unwrap operations.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
