"""HTTP helpers shared by every ingester.

Per-source HTTP client with retry, exponential backoff + jitter, sliding-window
rate limiting, and a configurable User-Agent. Test-friendly via
:class:`httpx.BaseTransport` injection plus pluggable ``sleep`` / ``clock``
callables — none of the timing logic touches a real wall clock when
overridden.

Each ingester instantiates its own client with a source-specific
:class:`RateLimit` (e.g. SEC's 10 req/sec) and :class:`RetryPolicy`. The
client exposes the single building block every collector needs:

    with HttpClient("defillama", rate_limit=RateLimit.per_second(5)) as http:
        protocols = http.get_json("https://api.llama.fi/protocols")
"""

from __future__ import annotations

import math
import random
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from numbers import Real
from typing import Any

import httpx

from genkei import __version__

#: Status codes the default retry policy considers transient. Override on a
#: per-client basis via :class:`RetryPolicy.retry_on_status` if a source has
#: idiosyncratic semantics (e.g. some APIs return 200 with an error body and
#: should never be retried).
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _is_positive_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and value > 0


@dataclass(frozen=True)
class RateLimit:
    """At most ``requests`` calls per ``window_seconds``.

    Implemented as a sliding window — the client drops timestamps older than
    ``window_seconds`` before each request and sleeps until the oldest
    in-window timestamp would expire if we'd exceed the cap.
    """

    requests: int
    window_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.requests, int) or isinstance(self.requests, bool):
            raise ValueError("RateLimit.requests must be an integer >= 1.")
        if self.requests < 1:
            raise ValueError("RateLimit.requests must be an integer >= 1.")
        if not _is_positive_number(self.window_seconds):
            raise ValueError("RateLimit.window_seconds must be a positive number.")

    @classmethod
    def per_second(cls, n: int) -> RateLimit:
        return cls(requests=n, window_seconds=1.0)

    @classmethod
    def per_minute(cls, n: int) -> RateLimit:
        return cls(requests=n, window_seconds=60.0)


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry with exponential backoff + jitter.

    Defaults are conservative — 4 attempts, 1s → 30s backoff, 50% jitter.
    Network-layer exceptions (timeouts, connection errors) and any status code
    in ``retry_on_status`` trigger a retry; on 429 responses the
    ``Retry-After`` header (if present) overrides the computed backoff.
    """

    max_attempts: int = 4
    initial_backoff: float = 1.0
    max_backoff: float = 30.0
    backoff_multiplier: float = 2.0
    jitter: float = 0.5
    retry_on_status: frozenset[int] = field(default_factory=lambda: RETRYABLE_STATUS_CODES)

    def __post_init__(self) -> None:
        if not isinstance(self.max_attempts, int) or isinstance(self.max_attempts, bool):
            raise ValueError("RetryPolicy.max_attempts must be an integer >= 1.")
        if self.max_attempts < 1:
            raise ValueError("RetryPolicy.max_attempts must be an integer >= 1.")
        if not _is_positive_number(self.initial_backoff):
            raise ValueError("RetryPolicy.initial_backoff must be a positive number.")
        if not _is_positive_number(self.max_backoff):
            raise ValueError("RetryPolicy.max_backoff must be a positive number.")
        if self.initial_backoff > self.max_backoff:
            raise ValueError("RetryPolicy.initial_backoff must be <= RetryPolicy.max_backoff.")
        if not _is_positive_number(self.backoff_multiplier):
            raise ValueError("RetryPolicy.backoff_multiplier must be a positive number.")
        if not isinstance(self.jitter, Real) or isinstance(self.jitter, bool):
            raise ValueError("RetryPolicy.jitter must be a number in [0, 1].")
        if self.jitter < 0 or self.jitter > 1:
            raise ValueError("RetryPolicy.jitter must be a number in [0, 1].")
        if not isinstance(self.retry_on_status, (set, frozenset)):
            raise ValueError("RetryPolicy.retry_on_status must be a set or frozenset of ints.")
        if not all(
            isinstance(code, int) and not isinstance(code, bool) for code in self.retry_on_status
        ):
            raise ValueError("RetryPolicy.retry_on_status must be a set or frozenset of ints.")


class _SlidingWindowLimiter:
    """Sleeps before ``acquire`` returns if a fresh request would exceed
    the rate. Timestamps live in a deque keyed off the supplied clock."""

    def __init__(
        self,
        limit: RateLimit,
        *,
        sleep: Callable[[float], None],
        clock: Callable[[], float],
    ) -> None:
        self._limit = limit
        self._sleep = sleep
        self._clock = clock
        self._timestamps: deque[float] = deque()

    def _evict(self, now: float) -> None:
        cutoff = now - self._limit.window_seconds
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    def acquire(self) -> None:
        now = self._clock()
        self._evict(now)
        if len(self._timestamps) >= self._limit.requests:
            oldest = self._timestamps[0]
            wait = oldest + self._limit.window_seconds - now
            if wait > 0:
                self._sleep(wait)
                now = self._clock()
                self._evict(now)
        self._timestamps.append(now)


class HttpClient:
    """Per-source HTTP client with retry, backoff, rate limiting, UA.

    Owns an :class:`httpx.Client` under the hood. Call :meth:`close` (or use
    as a context manager) to release the underlying connection pool.
    """

    def __init__(
        self,
        source_name: str,
        *,
        user_agent: str | None = None,
        rate_limit: RateLimit | None = None,
        retry: RetryPolicy | None = None,
        timeout: float = 30.0,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.source_name = source_name
        self.user_agent = user_agent or f"genkei/{__version__} (+{source_name})"
        self.retry = retry or RetryPolicy()
        self._sleep = sleep or time.sleep
        self._clock = clock or time.monotonic
        self._limiter = (
            _SlidingWindowLimiter(rate_limit, sleep=self._sleep, clock=self._clock)
            if rate_limit is not None
            else None
        )
        self._client = httpx.Client(
            base_url=base_url or "",
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": self.user_agent},
        )

    def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Issue a request with retry + backoff + rate limiting applied.

        Returns the final :class:`httpx.Response` whether or not it's a 2xx.
        Network-layer exceptions are re-raised after the retry budget is
        exhausted; retryable status codes return the last response so the
        caller can inspect it.
        """
        if method.upper() not in IDEMPOTENT_METHODS:
            if self._limiter is not None:
                self._limiter.acquire()
            return self._client.request(method, url, **kwargs)

        attempt = 0
        while True:
            attempt += 1
            if self._limiter is not None:
                self._limiter.acquire()
            try:
                response = self._client.request(method, url, **kwargs)
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt >= self.retry.max_attempts:
                    raise
                self._sleep(self._compute_backoff(attempt))
                continue

            if response.status_code in self.retry.retry_on_status:
                if attempt >= self.retry.max_attempts:
                    return response
                wait = self._wait_after_retryable_status(response, attempt)
                self._sleep(wait)
                continue

            return response

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def get_json(self, url: str, **kwargs: Any) -> Any:
        """GET a URL and return the parsed JSON body, raising on non-2xx."""
        response = self.get(url, **kwargs)
        response.raise_for_status()
        return response.json()

    def _wait_after_retryable_status(self, response: httpx.Response, attempt: int) -> float:
        """Honor a numeric ``Retry-After`` header when present; otherwise
        fall back to the policy's exponential backoff."""
        retry_after = response.headers.get("retry-after")
        if retry_after is not None:
            try:
                wait = float(retry_after)
                if math.isfinite(wait) and wait > 0:
                    return wait
            except ValueError:
                pass
        return self._compute_backoff(attempt)

    def _compute_backoff(self, attempt: int) -> float:
        backoff = min(
            self.retry.initial_backoff * (self.retry.backoff_multiplier ** (attempt - 1)),
            self.retry.max_backoff,
        )
        if self.retry.jitter > 0:
            return backoff + random.uniform(0, self.retry.jitter * backoff)
        return backoff

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()
