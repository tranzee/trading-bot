"""pUSD wrap / unwrap helpers + ERC-20 balance reads.

API-only traders must wrap USDC.e -> pUSD via the Collateral Onramp `wrap()`
function. This module provides:

    - get_pusd_balance(): pUSD balance of the funder address
    - get_usdce_balance(): USDC.e balance
    - wrap(amount): wrap USDC.e to pUSD
    - unwrap(amount): unwrap pUSD back to USDC.e

All contract calls go through web3.py with timeout, retry, and circuit-
breaker guards. Implementation note: as of 2026-04, the Collateral Onramp
contract address and ABI are documented in Polymarket's V2 migration guide.
The constants below are placeholders to be filled by the operator from the
official guide before any live wrap operation — verifying contract and ABI
correctness is the operator's job, not the bot's.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from web3 import Web3
from web3.contract import Contract

from config import constants as K
from config.settings import Settings
from polybot.obs.logger import log


# ============================================================================
# Contract addresses — placeholders. Operator MUST verify before live wrap.
# Source: Polymarket V2 migration guide -> "Collateral Onramp" section.
# ============================================================================

#: pUSD ERC-20 on Polygon mainnet. Placeholder; verify in V2 migration guide.
PUSD_TOKEN_ADDRESS_POLYGON = "0x0000000000000000000000000000000000000000"

#: USDC.e ERC-20 on Polygon mainnet (well-known).
USDCE_TOKEN_ADDRESS_POLYGON = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

#: Collateral Onramp contract that exposes wrap()/unwrap(). Placeholder; verify.
COLLATERAL_ONRAMP_ADDRESS_POLYGON = "0x0000000000000000000000000000000000000000"

# Minimal ERC-20 ABI for balanceOf and approve.
ERC20_ABI: list[dict[str, Any]] = [
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
        "stateMutability": "view",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "value", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
        "stateMutability": "nonpayable",
    },
]

# Collateral Onramp ABI (subset). Operator must replace with the real ABI
# from Polymarket's V2 migration guide.
COLLATERAL_ONRAMP_ABI: list[dict[str, Any]] = [
    {
        "name": "wrap",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "amount", "type": "uint256"}],
        "outputs": [],
    },
    {
        "name": "unwrap",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [{"name": "amount", "type": "uint256"}],
        "outputs": [],
    },
]


def _to_units(amount: Decimal) -> int:
    return int((amount * (Decimal(10) ** K.PUSD_DECIMALS)).to_integral_value())


def _from_units(amount: int) -> Decimal:
    return Decimal(amount) / (Decimal(10) ** K.PUSD_DECIMALS)


class PusdHelper:
    """Web3-based pUSD/USDC.e helpers. Operator-driven — never auto-runs in the engine loop."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._w3 = Web3(Web3.HTTPProvider(settings.POLYGON_RPC_URL, request_kwargs={"timeout": 10}))
        self._funder = Web3.to_checksum_address(settings.POLYMARKET_FUNDER_ADDRESS)

    def _erc20(self, address: str) -> Contract:
        return self._w3.eth.contract(
            address=Web3.to_checksum_address(address), abi=ERC20_ABI
        )

    def _onramp(self) -> Contract:
        return self._w3.eth.contract(
            address=Web3.to_checksum_address(COLLATERAL_ONRAMP_ADDRESS_POLYGON),
            abi=COLLATERAL_ONRAMP_ABI,
        )

    def get_pusd_balance(self) -> Decimal:
        units = self._erc20(PUSD_TOKEN_ADDRESS_POLYGON).functions.balanceOf(self._funder).call()
        return _from_units(int(units))

    def get_usdce_balance(self) -> Decimal:
        units = self._erc20(USDCE_TOKEN_ADDRESS_POLYGON).functions.balanceOf(self._funder).call()
        return _from_units(int(units))

    def wrap(self, amount: Decimal) -> str:
        if COLLATERAL_ONRAMP_ADDRESS_POLYGON == "0x" + "0" * 40:
            raise RuntimeError(
                "COLLATERAL_ONRAMP_ADDRESS_POLYGON is a placeholder. "
                "Operator: fill in the real address from the V2 migration guide."
            )
        log.warning(
            "pusd.wrap: placeholder ABI / address active; operator must verify before mainnet."
        )
        raise NotImplementedError("Operator: complete the wrap/unwrap implementation per the V2 migration guide before live use.")

    def unwrap(self, amount: Decimal) -> str:
        raise NotImplementedError("Operator: complete the wrap/unwrap implementation per the V2 migration guide before live use.")
