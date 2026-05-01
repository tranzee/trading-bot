"""One-time helper: wrap USDC.e -> pUSD via Polymarket's Collateral Onramp.

Usage:
    python scripts/wrap_pusd.py --amount 100

Operator must verify the Collateral Onramp address and ABI in
`src/polybot/poly/pusd.py` before running this against mainnet. Will refuse
to run while the address is a placeholder.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from config.settings import load_settings  # noqa: E402
from polybot.obs.logger import configure_from_settings  # noqa: E402
from polybot.poly.pusd import PusdHelper  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Wrap USDC.e -> pUSD")
    parser.add_argument("--amount", type=Decimal, required=True, help="Amount of USDC.e to wrap.")
    parser.add_argument(
        "--unwrap",
        action="store_true",
        help="Unwrap pUSD -> USDC.e instead of wrapping.",
    )
    args = parser.parse_args()

    settings = load_settings()
    configure_from_settings(settings.LOG_LEVEL.value)
    helper = PusdHelper(settings)

    if args.unwrap:
        tx = helper.unwrap(args.amount)
        action = "unwrap"
    else:
        tx = helper.wrap(args.amount)
        action = "wrap"
    print(f"{action}({args.amount}) tx: {tx}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
