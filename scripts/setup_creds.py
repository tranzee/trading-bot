"""One-time CLOB API credential derivation.

Reads POLYMARKET_PRIVATE_KEY from .env, calls the SDK's
`create_or_derive_api_key()`, and prints the result. The operator can save
the values into a separate credentials store if desired; the bot itself
re-derives on every startup, so persisting them is optional.

Usage:
    python scripts/setup_creds.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Make `polybot` and `config` importable when the script is run directly.
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from config.settings import load_settings  # noqa: E402
from polybot.obs.logger import configure_from_settings, log  # noqa: E402
from polybot.poly.client import PolyClient  # noqa: E402


async def main() -> int:
    settings = load_settings()
    configure_from_settings(settings.LOG_LEVEL.value)
    poly = PolyClient(settings)
    await poly.setup_creds()
    assert poly._creds is not None  # post-setup_creds invariant
    creds = poly._creds  # noqa: SLF001 — operator-only script
    print("=" * 72)
    print(" CLOB V2 API credentials derived OK")
    print("=" * 72)
    print(f"  api_key        : {creds.api_key}")
    print(f"  api_secret     : {creds.api_secret[:6]}...{creds.api_secret[-4:]}  (full secret printed below)")
    print(f"  api_passphrase : {creds.api_passphrase}")
    print()
    print("Full secret (handle carefully; do not commit):")
    print(f"  {creds.api_secret}")
    print()
    print("These can be persisted in a separate secrets store. The bot itself")
    print("re-derives on every startup, so storing them is OPTIONAL.")
    log.info("setup_creds: success (api_key=%s)", creds.api_key[:8] + "...")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
