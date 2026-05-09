"""Unit tests for genkei.common.http.

Network is fully mocked via httpx.MockTransport. Time is controlled with a
fake clock + recorded-sleep callable so retry/backoff/rate-limit assertions
are deterministic.
"""

from __future__ import annotations

import unittest
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx

from genkei.common import http
from genkei.common.http import (
    HttpClient,
    RateLimit,
    RetryPolicy,
    _SlidingWindowLimiter,
)


class _FakeClock:
    """Monotonic clock the test can advance manually.

    Sleep calls advance the clock by the requested duration, mirroring the
    behavior of a real clock under time.sleep without the wall-clock cost.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def tick(self, seconds: float) -> None:
        self.now += seconds


def _handler_returning(*responses: httpx.Response) -> Callable[[httpx.Request], httpx.Response]:
    """Build a MockTransport handler that yields the given responses in order
    then keeps returning the last one."""
    seq: Iterator[httpx.Response] = iter(responses)
    last: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            response = next(seq)
        except StopIteration:
            return last["last"]
        last["last"] = response
        return response

    return handler


def _handler_raising(*errors: Exception) -> Callable[[httpx.Request], httpx.Response]:
    """Build a handler that raises the given exceptions in order then keeps
    raising the last one."""
    seq: Iterator[Exception] = iter(errors)
    last: dict[str, Exception] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            err = next(seq)
        except StopIteration:
            err = last["last"]
        last["last"] = err
        raise err

    return handler


class RateLimitFactoryTests(unittest.TestCase):
    def test_per_second(self) -> None:
        rl = RateLimit.per_second(10)
        self.assertEqual(rl.requests, 10)
        self.assertEqual(rl.window_seconds, 1.0)

    def test_per_minute(self) -> None:
        rl = RateLimit.per_minute(60)
        self.assertEqual(rl.requests, 60)
        self.assertEqual(rl.window_seconds, 60.0)

    def test_rejects_non_positive_capacity(self) -> None:
        with self.assertRaisesRegex(ValueError, "RateLimit.requests"):
            RateLimit(requests=0, window_seconds=1.0)

    def test_rejects_non_positive_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "RateLimit.window_seconds"):
            RateLimit(requests=1, window_seconds=0.0)


class SlidingWindowLimiterTests(unittest.TestCase):
    def test_allows_capacity_without_sleeping(self) -> None:
        clock = _FakeClock()
        limiter = _SlidingWindowLimiter(
            RateLimit(requests=3, window_seconds=1.0),
            sleep=clock.sleep,
            clock=clock,
        )
        for _ in range(3):
            limiter.acquire()
        self.assertEqual(clock.sleeps, [])

    def test_sleeps_until_oldest_expires_when_full(self) -> None:
        clock = _FakeClock()
        limiter = _SlidingWindowLimiter(
            RateLimit(requests=2, window_seconds=1.0),
            sleep=clock.sleep,
            clock=clock,
        )
        limiter.acquire()  # t=1000
        clock.tick(0.4)
        limiter.acquire()  # t=1000.4
        # Window is full (2 in last 1.0s). Oldest is at 1000, expires at 1001.
        # We're at 1000.4, so we should sleep ~0.6s.
        limiter.acquire()
        self.assertEqual(len(clock.sleeps), 1)
        self.assertAlmostEqual(clock.sleeps[0], 0.6, places=6)

    def test_evicts_expired_timestamps(self) -> None:
        clock = _FakeClock()
        limiter = _SlidingWindowLimiter(
            RateLimit(requests=2, window_seconds=1.0),
            sleep=clock.sleep,
            clock=clock,
        )
        limiter.acquire()
        limiter.acquire()
        clock.tick(2.0)  # both timestamps expire
        limiter.acquire()
        # No sleep needed because both old timestamps are evicted.
        self.assertEqual(clock.sleeps, [])


class BackoffComputationTests(unittest.TestCase):
    def _client(self, retry: RetryPolicy) -> HttpClient:
        return HttpClient(
            "test",
            retry=retry,
            transport=httpx.MockTransport(_handler_returning()),
        )

    def test_no_jitter_is_deterministic(self) -> None:
        client = self._client(
            RetryPolicy(
                max_attempts=5,
                initial_backoff=1.0,
                max_backoff=30.0,
                backoff_multiplier=2.0,
                jitter=0.0,
            )
        )
        self.assertEqual(client._compute_backoff(1), 1.0)
        self.assertEqual(client._compute_backoff(2), 2.0)
        self.assertEqual(client._compute_backoff(3), 4.0)
        self.assertEqual(client._compute_backoff(4), 8.0)
        client.close()

    def test_caps_at_max_backoff(self) -> None:
        client = self._client(
            RetryPolicy(
                max_attempts=10,
                initial_backoff=1.0,
                max_backoff=5.0,
                backoff_multiplier=2.0,
                jitter=0.0,
            )
        )
        self.assertEqual(client._compute_backoff(9), 5.0)
        client.close()

    def test_jitter_stays_within_band(self) -> None:
        client = self._client(
            RetryPolicy(
                max_attempts=5,
                initial_backoff=2.0,
                max_backoff=30.0,
                backoff_multiplier=2.0,
                jitter=0.5,
            )
        )
        # Attempt 1: backoff = 2.0; jitter band = [0, 1.0]; result in [2.0, 3.0]
        for _ in range(50):
            value = client._compute_backoff(1)
            self.assertGreaterEqual(value, 2.0)
            self.assertLessEqual(value, 3.0)
        client.close()


class RetryPolicyValidationTests(unittest.TestCase):
    def test_rejects_non_positive_attempts(self) -> None:
        with self.assertRaisesRegex(ValueError, "RetryPolicy.max_attempts"):
            RetryPolicy(max_attempts=0)

    def test_rejects_non_positive_backoff_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "RetryPolicy.initial_backoff"):
            RetryPolicy(initial_backoff=0.0)
        with self.assertRaisesRegex(ValueError, "RetryPolicy.max_backoff"):
            RetryPolicy(max_backoff=0.0)

    def test_rejects_initial_backoff_above_max_backoff(self) -> None:
        with self.assertRaisesRegex(ValueError, "RetryPolicy.initial_backoff"):
            RetryPolicy(initial_backoff=5.0, max_backoff=1.0)

    def test_rejects_invalid_multiplier_and_jitter(self) -> None:
        with self.assertRaisesRegex(ValueError, "RetryPolicy.backoff_multiplier"):
            RetryPolicy(backoff_multiplier=0.0)
        with self.assertRaisesRegex(ValueError, "RetryPolicy.jitter"):
            RetryPolicy(jitter=1.5)

    def test_rejects_non_set_retry_statuses(self) -> None:
        with self.assertRaisesRegex(ValueError, "RetryPolicy.retry_on_status"):
            RetryPolicy(retry_on_status=(500, 503))  # type: ignore[arg-type]

    def test_rejects_non_int_retry_statuses(self) -> None:
        with self.assertRaisesRegex(ValueError, "RetryPolicy.retry_on_status"):
            RetryPolicy(retry_on_status=frozenset({500, "503"}))  # type: ignore[arg-type]

    def test_normalizes_retry_statuses_to_frozenset(self) -> None:
        retry_statuses = {500, 503}
        policy = RetryPolicy(retry_on_status=retry_statuses)

        retry_statuses.add(418)

        self.assertEqual(policy.retry_on_status, frozenset({500, 503}))
        self.assertIsInstance(policy.retry_on_status, frozenset)


class UserAgentTests(unittest.TestCase):
    def test_default_user_agent_includes_source(self) -> None:
        recorded: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            recorded["ua"] = request.headers.get("user-agent", "")
            return httpx.Response(200, json={"ok": True})

        with HttpClient("defillama", transport=httpx.MockTransport(handler)) as client:
            client.get("https://example.test/x")

        self.assertIn("genkei/", recorded["ua"])
        self.assertIn("(+defillama)", recorded["ua"])

    def test_custom_user_agent_overrides_default(self) -> None:
        recorded: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            recorded["ua"] = request.headers.get("user-agent", "")
            return httpx.Response(200, json={"ok": True})

        with HttpClient(
            "fred",
            user_agent="custom-agent/1.2.3",
            transport=httpx.MockTransport(handler),
        ) as client:
            client.get("https://example.test/x")

        self.assertEqual(recorded["ua"], "custom-agent/1.2.3")


class RetryBehaviorTests(unittest.TestCase):
    def _client(
        self,
        handler: Callable[[httpx.Request], httpx.Response],
        retry: RetryPolicy | None = None,
        wall_clock: Callable[[], datetime] | None = None,
    ) -> tuple[HttpClient, _FakeClock]:
        clock = _FakeClock()
        client = HttpClient(
            "test",
            retry=retry or RetryPolicy(jitter=0.0),
            transport=httpx.MockTransport(handler),
            sleep=clock.sleep,
            clock=clock,
            wall_clock=wall_clock,
        )
        return client, clock

    def test_success_on_first_try_does_not_sleep(self) -> None:
        client, clock = self._client(_handler_returning(httpx.Response(200, json={"ok": True})))
        with client:
            response = client.get("https://example.test/x")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(clock.sleeps, [])

    def test_retries_on_502_then_succeeds(self) -> None:
        client, clock = self._client(
            _handler_returning(
                httpx.Response(502),
                httpx.Response(200, json={"ok": True}),
            )
        )
        with client:
            response = client.get("https://example.test/x")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(clock.sleeps), 1)
        self.assertEqual(clock.sleeps[0], 1.0)  # initial_backoff

    def test_returns_last_response_after_max_attempts_on_retryable_status(self) -> None:
        client, clock = self._client(
            _handler_returning(httpx.Response(503)),  # always 503
            RetryPolicy(max_attempts=3, jitter=0.0, initial_backoff=1.0, backoff_multiplier=2.0),
        )
        with client:
            response = client.get("https://example.test/x")
        self.assertEqual(response.status_code, 503)
        # 3 attempts → 2 sleeps (after attempts 1 and 2; the third returns)
        self.assertEqual(clock.sleeps, [1.0, 2.0])

    def test_honors_retry_after_header_on_429(self) -> None:
        client, clock = self._client(
            _handler_returning(
                httpx.Response(429, headers={"Retry-After": "7"}),
                httpx.Response(200, json={"ok": True}),
            )
        )
        with client:
            response = client.get("https://example.test/x")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(clock.sleeps, [7.0])

    def test_honors_http_date_retry_after_header_on_429(self) -> None:
        now = datetime(2026, 5, 8, 12, 0, 0, tzinfo=timezone.utc)
        retry_at = now + timedelta(seconds=5)
        client, clock = self._client(
            _handler_returning(
                httpx.Response(
                    429,
                    headers={"Retry-After": format_datetime(retry_at, usegmt=True)},
                ),
                httpx.Response(200, json={"ok": True}),
            ),
            wall_clock=lambda: now,
        )
        with client:
            response = client.get("https://example.test/x")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(clock.sleeps, [5.0])

    def test_falls_back_to_backoff_on_invalid_retry_after(self) -> None:
        client, clock = self._client(
            _handler_returning(
                httpx.Response(429, headers={"Retry-After": "soon-ish"}),
                httpx.Response(200, json={"ok": True}),
            )
        )
        with client:
            response = client.get("https://example.test/x")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(clock.sleeps, [1.0])

    def test_falls_back_to_backoff_on_non_positive_retry_after(self) -> None:
        client, clock = self._client(
            _handler_returning(
                httpx.Response(429, headers={"Retry-After": "-1"}),
                httpx.Response(429, headers={"Retry-After": "0"}),
                httpx.Response(200, json={"ok": True}),
            ),
            RetryPolicy(max_attempts=3, jitter=0.0, initial_backoff=1.0, backoff_multiplier=2.0),
        )
        with client:
            response = client.get("https://example.test/x")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(clock.sleeps, [1.0, 2.0])

    def test_falls_back_to_backoff_on_non_finite_retry_after(self) -> None:
        client, clock = self._client(
            _handler_returning(
                httpx.Response(429, headers={"Retry-After": "inf"}),
                httpx.Response(429, headers={"Retry-After": "1e309"}),
                httpx.Response(200, json={"ok": True}),
            ),
            RetryPolicy(max_attempts=3, jitter=0.0, initial_backoff=1.0, backoff_multiplier=2.0),
        )
        with client:
            response = client.get("https://example.test/x")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(clock.sleeps, [1.0, 2.0])

    def test_retries_network_exceptions_and_succeeds(self) -> None:
        # First call: timeout. Subsequent: 200. MockTransport's handler can
        # raise; we use a small custom handler that mixes raise + return.
        attempts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(1)
            if len(attempts) == 1:
                raise httpx.ConnectTimeout("timeout", request=request)
            return httpx.Response(200, json={"ok": True})

        clock = _FakeClock()
        client = HttpClient(
            "test",
            retry=RetryPolicy(jitter=0.0),
            transport=httpx.MockTransport(handler),
            sleep=clock.sleep,
            clock=clock,
        )
        with client:
            response = client.get("https://example.test/x")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(clock.sleeps, [1.0])

    def test_raises_after_max_network_failures(self) -> None:
        client, clock = self._client(
            _handler_raising(
                httpx.ConnectError("nope"),
                httpx.ConnectError("nope"),
                httpx.ConnectError("nope"),
            ),
            RetryPolicy(max_attempts=3, jitter=0.0, initial_backoff=1.0, backoff_multiplier=2.0),
        )
        with self.assertRaises(httpx.ConnectError), client:
            client.get("https://example.test/x")
        # 3 attempts → 2 sleeps then re-raise
        self.assertEqual(clock.sleeps, [1.0, 2.0])

    def test_non_retryable_status_returns_immediately(self) -> None:
        client, clock = self._client(_handler_returning(httpx.Response(404)))
        with client:
            response = client.get("https://example.test/x")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(clock.sleeps, [])

    def test_retries_put_and_delete_as_idempotent_methods(self) -> None:
        for method in ("PUT", "DELETE"):
            with self.subTest(method=method):
                attempts: list[str] = []

                def handler(
                    request: httpx.Request, attempts: list[str] = attempts
                ) -> httpx.Response:
                    attempts.append(request.method)
                    if len(attempts) == 1:
                        return httpx.Response(503)
                    return httpx.Response(200, json={"ok": True})

                client, clock = self._client(
                    handler,
                    RetryPolicy(
                        max_attempts=3,
                        jitter=0.0,
                        initial_backoff=1.0,
                        backoff_multiplier=2.0,
                    ),
                )
                with client:
                    response = client.request(method, "https://example.test/x")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(attempts, [method, method])
                self.assertEqual(clock.sleeps, [1.0])

    def test_does_not_retry_non_idempotent_retryable_status(self) -> None:
        attempts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request.method)
            return httpx.Response(503)

        client, clock = self._client(
            handler,
            RetryPolicy(max_attempts=3, jitter=0.0, initial_backoff=1.0, backoff_multiplier=2.0),
        )
        with client:
            response = client.request("POST", "https://example.test/x", json={"value": 1})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(attempts, ["POST"])
        self.assertEqual(clock.sleeps, [])

    def test_does_not_retry_non_idempotent_network_error(self) -> None:
        attempts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            attempts.append(request.method)
            raise httpx.ConnectError("nope", request=request)

        client, clock = self._client(
            handler,
            RetryPolicy(max_attempts=3, jitter=0.0, initial_backoff=1.0, backoff_multiplier=2.0),
        )
        with self.assertRaises(httpx.ConnectError), client:
            client.request("POST", "https://example.test/x", json={"value": 1})
        self.assertEqual(attempts, ["POST"])
        self.assertEqual(clock.sleeps, [])


class GetJsonTests(unittest.TestCase):
    def test_returns_parsed_body_on_200(self) -> None:
        clock = _FakeClock()
        client = HttpClient(
            "test",
            transport=httpx.MockTransport(
                _handler_returning(httpx.Response(200, json={"value": 42}))
            ),
            sleep=clock.sleep,
            clock=clock,
        )
        with client:
            self.assertEqual(client.get_json("https://example.test/x"), {"value": 42})

    def test_raises_on_non_2xx(self) -> None:
        clock = _FakeClock()
        client = HttpClient(
            "test",
            retry=RetryPolicy(max_attempts=1, jitter=0.0),
            transport=httpx.MockTransport(_handler_returning(httpx.Response(404))),
            sleep=clock.sleep,
            clock=clock,
        )
        with self.assertRaises(httpx.HTTPStatusError), client:
            client.get_json("https://example.test/x")


class RateLimitIntegrationTests(unittest.TestCase):
    def test_rate_limiter_enforced_between_requests(self) -> None:
        clock = _FakeClock()
        client = HttpClient(
            "sec",
            rate_limit=RateLimit.per_second(2),
            transport=httpx.MockTransport(
                _handler_returning(httpx.Response(200, json={"ok": True}))
            ),
            sleep=clock.sleep,
            clock=clock,
        )

        with client:
            client.get("https://example.test/x")
            client.get("https://example.test/x")
            # Third call must wait — first two consumed the per-second budget.
            client.get("https://example.test/x")

        # Exactly one rate-limit sleep before the third request. No retry
        # sleeps because every response is 200.
        self.assertEqual(len(clock.sleeps), 1)
        self.assertGreater(clock.sleeps[0], 0.0)
        self.assertLessEqual(clock.sleeps[0], 1.0)


class RetryableStatusCodesTests(unittest.TestCase):
    def test_default_set_contents(self) -> None:
        # If this changes, verify every documented retry-on-status table is updated.
        self.assertEqual(
            http.RETRYABLE_STATUS_CODES,
            frozenset({408, 425, 429, 500, 502, 503, 504}),
        )


if __name__ == "__main__":
    unittest.main()
