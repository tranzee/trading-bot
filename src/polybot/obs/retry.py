"""Async retry with exponential backoff + circuit-breaker integration.

Used as a decorator on every SDK and RPC call:

    @retry(
        attempts=4,
        base_delay_s=0.2,
        max_delay_s=5.0,
        retry_on=(httpx.HTTPError, asyncio.TimeoutError),
        circuit=poly_circuit,
    )
    async def place_order(...): ...

Behaviour:
    - exponential backoff with jitter: delay_n = min(base * 2**n, cap) * U(0.5, 1.5)
    - structured per-attempt log line at WARNING
    - circuit breaker checked before each attempt; if OPEN, raises CircuitOpen
    - permanent errors (e.g. 4xx) listed in `permanent_on` short-circuit
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any, ParamSpec, TypeVar

from polybot.obs.logger import log, safe_repr

P = ParamSpec("P")
T = TypeVar("T")


class CircuitOpen(RuntimeError):
    """Raised when a call is rejected because its circuit breaker is OPEN."""


@dataclass
class CircuitBreaker:
    """Simple half-open circuit breaker.

    States:
        CLOSED  — calls pass through; failures increment a counter.
        OPEN    — calls rejected immediately for `cooldown_s`.
        HALF_OPEN — one trial call permitted; success closes, failure re-opens.
    """

    name: str
    failure_threshold: int = 5
    cooldown_s: float = 30.0
    failures: int = 0
    opened_at: float = 0.0
    state: str = "CLOSED"

    def before_call(self) -> None:
        if self.state == "OPEN":
            elapsed = time.monotonic() - self.opened_at
            if elapsed >= self.cooldown_s:
                self.state = "HALF_OPEN"
                log.info(f"circuit[{self.name}] HALF_OPEN after {elapsed:.1f}s cooldown")
            else:
                raise CircuitOpen(
                    f"circuit[{self.name}] OPEN; "
                    f"{self.cooldown_s - elapsed:.1f}s remain on cooldown"
                )

    def record_success(self) -> None:
        if self.state in ("HALF_OPEN", "OPEN"):
            log.info(f"circuit[{self.name}] CLOSED after success")
        self.state = "CLOSED"
        self.failures = 0

    def record_failure(self) -> None:
        self.failures += 1
        if self.state == "HALF_OPEN" or self.failures >= self.failure_threshold:
            self.state = "OPEN"
            self.opened_at = time.monotonic()
            log.warning(
                f"circuit[{self.name}] OPEN after {self.failures} failures; "
                f"cooldown {self.cooldown_s}s"
            )


def retry(
    *,
    attempts: int = 4,
    base_delay_s: float = 0.2,
    max_delay_s: float = 5.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    permanent_on: tuple[type[BaseException], ...] = (),
    circuit: CircuitBreaker | None = None,
    label: str | None = None,
) -> Callable[[Callable[P, Awaitable[T]]], Callable[P, Awaitable[T]]]:
    """Decorate an async function with retry + circuit breaker."""

    def decorator(fn: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
        tag = label or fn.__qualname__

        @wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            last_exc: BaseException | None = None
            for attempt in range(1, attempts + 1):
                if circuit is not None:
                    circuit.before_call()
                try:
                    result = await fn(*args, **kwargs)
                    if circuit is not None:
                        circuit.record_success()
                    return result
                except permanent_on as exc:
                    log.warning(f"{tag}: permanent error on attempt {attempt}: {safe_repr(exc)}")
                    raise
                except retry_on as exc:
                    last_exc = exc
                    if circuit is not None:
                        circuit.record_failure()
                    if attempt == attempts:
                        log.error(
                            f"{tag}: gave up after {attempts} attempts; last error: {safe_repr(exc)}"
                        )
                        raise
                    delay = min(base_delay_s * (2 ** (attempt - 1)), max_delay_s)
                    delay *= 0.5 + random.random()  # jitter [0.5x, 1.5x]
                    log.warning(
                        f"{tag}: attempt {attempt}/{attempts} failed "
                        f"({type(exc).__name__}: {exc}); retrying in {delay:.2f}s"
                    )
                    await asyncio.sleep(delay)
            assert last_exc is not None  # pragma: no cover
            raise last_exc

        return wrapper

    return decorator


async def with_timeout(coro: Awaitable[T], timeout_s: float, label: str = "") -> T:
    """Helper: await a coroutine with a timeout, raising asyncio.TimeoutError on expiry."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout_s)
    except asyncio.TimeoutError:
        log.warning(f"timeout[{label}] after {timeout_s:.2f}s")
        raise


def _typecheck() -> None:  # pragma: no cover
    """Static helper to keep mypy --strict happy on Any-bound generics above."""
    _: Any = None
    return _
