"""Retry decorator + circuit breaker."""

from __future__ import annotations

import asyncio
import time

import pytest

from polybot.obs.retry import CircuitBreaker, CircuitOpen, retry


@pytest.mark.asyncio
async def test_retry_success_first_attempt() -> None:
    counter = {"calls": 0}

    @retry(attempts=3, base_delay_s=0.01, max_delay_s=0.02)
    async def fn() -> int:
        counter["calls"] += 1
        return 42

    assert await fn() == 42
    assert counter["calls"] == 1


@pytest.mark.asyncio
async def test_retry_eventually_succeeds() -> None:
    counter = {"calls": 0}

    @retry(attempts=3, base_delay_s=0.01, max_delay_s=0.02, retry_on=(ValueError,))
    async def fn() -> int:
        counter["calls"] += 1
        if counter["calls"] < 3:
            raise ValueError("transient")
        return 7

    assert await fn() == 7
    assert counter["calls"] == 3


@pytest.mark.asyncio
async def test_retry_gives_up() -> None:
    counter = {"calls": 0}

    @retry(attempts=2, base_delay_s=0.01, max_delay_s=0.02, retry_on=(RuntimeError,))
    async def fn() -> None:
        counter["calls"] += 1
        raise RuntimeError("broken")

    with pytest.raises(RuntimeError):
        await fn()
    assert counter["calls"] == 2


@pytest.mark.asyncio
async def test_permanent_errors_short_circuit() -> None:
    counter = {"calls": 0}

    @retry(
        attempts=3,
        base_delay_s=0.01,
        max_delay_s=0.02,
        retry_on=(Exception,),
        permanent_on=(ValueError,),
    )
    async def fn() -> None:
        counter["calls"] += 1
        raise ValueError("permanent")

    with pytest.raises(ValueError):
        await fn()
    assert counter["calls"] == 1  # no retries on permanent


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_threshold() -> None:
    cb = CircuitBreaker(name="test", failure_threshold=2, cooldown_s=0.1)

    @retry(attempts=1, retry_on=(RuntimeError,), circuit=cb, label="fail")
    async def fn() -> None:
        raise RuntimeError("nope")

    # Two failures -> circuit opens
    with pytest.raises(RuntimeError):
        await fn()
    with pytest.raises(RuntimeError):
        await fn()
    assert cb.state == "OPEN"

    # Third call: rejected immediately by circuit
    with pytest.raises(CircuitOpen):
        await fn()


@pytest.mark.asyncio
async def test_circuit_breaker_recovers_after_cooldown() -> None:
    cb = CircuitBreaker(name="test", failure_threshold=1, cooldown_s=0.05)

    state = {"fail": True}

    @retry(attempts=1, retry_on=(RuntimeError,), circuit=cb, label="recover")
    async def fn() -> int:
        if state["fail"]:
            raise RuntimeError("nope")
        return 1

    with pytest.raises(RuntimeError):
        await fn()
    assert cb.state == "OPEN"

    await asyncio.sleep(0.06)
    state["fail"] = False
    assert await fn() == 1
    assert cb.state == "CLOSED"
