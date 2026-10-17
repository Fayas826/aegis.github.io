"""Offline circuit breaker regressions; no models, transports or healing startup."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
import unittest
from unittest import mock

from core.reliability.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerManager,
    CircuitBreakerOpenException,
    CircuitBreakerState,
    CircuitState,
    admin_shell_breaker,
    circuit_breaker_manager,
)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def fail(*args, **kwargs):
    raise RuntimeError("private transport token")


class BreakerFixture:
    def setUp(self):
        # Also makes running these regressions against the old implementation safe.
        patcher = mock.patch.dict("sys.modules", {"core.orchestration.central_brain": None})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.clock = Clock()
        self.breaker = CircuitBreaker("test", 2, 10.0, clock=self.clock)

    def trip(self):
        self.breaker.record_failure("first")
        self.breaker.record_failure("second")
        self.assertEqual(self.breaker.state, CircuitState.OPEN)


class CircuitBreakerTests(BreakerFixture, unittest.TestCase):
    def test_threshold_and_fast_rejection(self):
        for count in (1, 2):
            with self.assertRaises(RuntimeError):
                self.breaker.call(fail)
            self.assertEqual(self.breaker.failure_count, count)
        target = mock.Mock()
        with self.assertRaises(CircuitBreakerOpenException):
            self.breaker.call(target)
        target.assert_not_called()
        self.assertEqual(self.breaker.total_trips, 1)

    def test_success_separates_failures(self):
        with self.assertRaises(RuntimeError):
            self.breaker.call(fail)
        self.assertEqual(self.breaker.call(lambda value: value, 42), 42)
        self.assertEqual(self.breaker.failure_count, 0)
        with self.assertRaises(RuntimeError):
            self.breaker.call(fail)
        self.assertEqual(self.breaker.state, CircuitState.CLOSED)
        self.assertEqual(self.breaker.failure_count, 1)

    def test_cooldown_uses_injected_monotonic_clock_and_exact_boundary(self):
        self.trip()
        with mock.patch("time.time", return_value=10**12):
            self.clock.advance(9.999)
            self.assertFalse(self.breaker.can_execute())
            self.clock.now = 110.0
            self.assertTrue(self.breaker.can_execute())
        self.assertEqual(self.breaker.state, CircuitState.HALF_OPEN)
        self.assertFalse(self.breaker.can_execute())
        self.breaker.record_success()
        self.assertEqual(self.breaker.state, CircuitState.CLOSED)
        self.assertEqual(self.breaker.failure_count, 0)

    def test_manual_failed_trial_reopens_and_restarts_cooldown(self):
        self.trip()
        self.clock.advance(10)
        self.assertTrue(self.breaker.can_execute())
        self.breaker.record_failure("trial failed")
        self.assertEqual(self.breaker.state, CircuitState.OPEN)
        self.assertEqual(self.breaker.total_trips, 2)
        self.assertFalse(self.breaker.can_execute())
        self.clock.advance(10)
        self.assertEqual(self.breaker.call(lambda: "recovered"), "recovered")
        self.assertEqual(self.breaker.state, CircuitState.CLOSED)

    def test_failed_wrapped_trial_reopens(self):
        self.trip()
        self.clock.advance(10)
        with self.assertRaises(RuntimeError):
            self.breaker.call(fail)
        self.assertEqual(self.breaker.state, CircuitState.OPEN)
        self.assertEqual(self.breaker.total_trips, 2)
        self.assertEqual(self.breaker.last_failure_time, self.clock.now)

    def test_one_concurrent_half_open_call_and_status_does_not_wait_for_target(self):
        self.trip()
        self.clock.advance(10)
        entered, release = threading.Event(), threading.Event()

        def trial():
            entered.set()
            if not release.wait(5):
                raise AssertionError("test did not release probe")
            return "ok"

        def contender():
            try:
                return self.breaker.call(lambda: "unexpected admission")
            except CircuitBreakerOpenException:
                return "blocked"

        with ThreadPoolExecutor(max_workers=9) as pool:
            probe = pool.submit(self.breaker.call, trial)
            try:
                self.assertTrue(entered.wait(2))
                futures = [pool.submit(contender) for _ in range(16)]
                self.assertTrue(all(f.result(timeout=2) == "blocked" for f in futures))
                self.assertEqual(pool.submit(self.breaker.get_status).result(timeout=2)["state"],
                                 CircuitState.HALF_OPEN)
            finally:
                release.set()
            self.assertEqual(probe.result(timeout=2), "ok")

    def test_manual_half_open_admission_is_single_across_threads(self):
        self.trip()
        self.clock.advance(10)
        barrier = threading.Barrier(8)

        def admit():
            barrier.wait(timeout=3)
            return self.breaker.can_execute()

        with ThreadPoolExecutor(max_workers=8) as pool:
            admissions = list(pool.map(lambda _: admit(), range(8)))
        self.assertEqual(sum(admissions), 1)

    def test_old_inflight_success_cannot_close_new_open_generation(self):
        entered, release = threading.Event(), threading.Event()

        def old_call():
            entered.set()
            release.wait(5)
            return "old success"

        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(self.breaker.call, old_call)
            try:
                self.assertTrue(entered.wait(2))
                self.trip()
                self.clock.advance(10)
                self.assertTrue(self.breaker.can_execute())
            finally:
                release.set()
            result.result(timeout=2)
        self.assertEqual(self.breaker.state, CircuitState.HALF_OPEN)
        self.assertFalse(self.breaker.can_execute())
        self.breaker.record_success()
        self.assertEqual(self.breaker.state, CircuitState.CLOSED)

    def test_late_manual_completion_cannot_close_probe(self):
        self.assertTrue(self.breaker.can_execute())
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(self.trip).result(timeout=2)
            self.clock.advance(10)
            self.assertTrue(pool.submit(self.breaker.can_execute).result(timeout=2))
        self.breaker.record_success()
        self.assertEqual(self.breaker.state, CircuitState.HALF_OPEN)

    def test_reset_ignores_old_inflight_failure(self):
        def reset_then_fail():
            self.breaker.reset()
            fail()

        with self.assertRaises(RuntimeError):
            self.breaker.call(reset_then_fail)
        self.assertEqual(self.breaker.failure_count, 0)
        self.assertEqual(self.breaker.state, CircuitState.CLOSED)

    def test_parallel_closed_failures_count_atomically_and_trip_once(self):
        breaker = CircuitBreaker("parallel", 8, clock=self.clock)
        barrier = threading.Barrier(8)

        def failing_call():
            barrier.wait(timeout=3)
            fail()

        def run(_):
            with self.assertRaises(RuntimeError):
                breaker.call(failing_call)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(run, range(8)))
        self.assertEqual(breaker.failure_count, 8)
        self.assertEqual(breaker.total_trips, 1)

    def test_fallback_receives_arguments_without_healing_breaker(self):
        fallback = mock.Mock(return_value="fallback")
        breaker = CircuitBreaker("fallback", 1, fallback_fn=fallback, clock=self.clock)
        self.assertEqual(breaker.call(fail, 7, option=True), "fallback")
        target = mock.Mock()
        self.assertEqual(breaker.call(target, 8, option=False), "fallback")
        target.assert_not_called()
        self.assertEqual(fallback.call_args_list,
                         [mock.call(7, option=True), mock.call(8, option=False)])
        self.assertEqual(breaker.state, CircuitState.OPEN)
        self.assertEqual(breaker.total_trips, 1)

    def test_fallback_failure_is_not_counted_as_another_service_failure(self):
        breaker = CircuitBreaker("fallback", 1, fallback_fn=fail, clock=self.clock)
        with self.assertRaises(RuntimeError):
            breaker.call(fail)
        with self.assertRaises(RuntimeError):
            breaker.call(lambda: "blocked")
        self.assertEqual(breaker.failure_count, 1)
        self.assertEqual(breaker.total_trips, 1)

    def test_sync_call_rejects_coroutine_without_counting_success_or_leaking_probe(self):
        async def target():
            return "async result"

        self.trip()
        self.clock.advance(10)
        with self.assertRaisesRegex(TypeError, "call_async"):
            self.breaker.call(target)
        self.assertEqual(self.breaker.state, CircuitState.HALF_OPEN)
        self.assertEqual(self.breaker.failure_count, 2)
        self.assertEqual(self.breaker.call(lambda: "real recovery"), "real recovery")

    def test_trip_notification_is_explicit_isolated_and_outside_lock(self):
        snapshots = []

        def notify(status):
            # A separate thread can read the breaker while notification runs.
            with ThreadPoolExecutor(max_workers=1) as pool:
                snapshots.append(pool.submit(breaker.get_status).result(timeout=2))
            raise RuntimeError("private notification token")

        breaker = CircuitBreaker("notify", 1, clock=self.clock, on_trip=notify)
        with self.assertLogs("AEGIS.CircuitBreaker", level="WARNING") as logs:
            with self.assertRaises(RuntimeError):
                breaker.call(fail)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["state"], CircuitState.OPEN)
        self.assertNotIn("private", " ".join(logs.output))
        self.assertEqual(breaker.state, CircuitState.OPEN)

    def test_default_trip_does_not_import_brain_or_healing(self):
        breaker = CircuitBreaker("no startup", 1)
        with mock.patch("builtins.__import__", side_effect=AssertionError("unexpected import")) as imports:
            with self.assertRaises(RuntimeError):
                breaker.call(fail)
        imports.assert_not_called()
        self.assertEqual(breaker.state, CircuitState.OPEN)

    def test_failure_logs_do_not_format_exception_or_payload(self):
        class SecretError(Exception):
            def __str__(self):
                raise AssertionError("exception must not be formatted")

        with self.assertLogs("AEGIS.CircuitBreaker", level="WARNING") as logs:
            self.breaker.record_failure(SecretError("private token"))
            self.breaker.record_failure("private token")
        self.assertNotIn("private", " ".join(logs.output))

    def test_manager_uniqueness_and_listing_while_registering(self):
        manager = CircuitBreakerManager()
        barrier = threading.Barrier(12)

        def register(index):
            barrier.wait(timeout=3)
            shared = manager.get_breaker("same", 7, 22)
            manager.get_breaker("worker_" + str(index))
            manager.list_breakers()
            return shared

        with ThreadPoolExecutor(max_workers=12) as pool:
            breakers = list(pool.map(register, range(12)))
        self.assertTrue(all(b is breakers[0] for b in breakers))
        self.assertIs(manager.get_breaker("same", 1, 0), breakers[0])
        self.assertEqual(manager.list_breakers()["same"]["threshold"], 7)
        self.assertEqual(len(manager.list_breakers()), 16)
        self.assertIs(circuit_breaker_manager.get_breaker("admin_shell"), admin_shell_breaker)
        self.assertIs(CircuitBreakerState, CircuitState)


class AsyncCircuitBreakerTests(BreakerFixture, unittest.IsolatedAsyncioTestCase):
    async def test_async_failure_is_awaited_and_success_resets_count(self):
        async def bad():
            await asyncio.sleep(0)
            fail()

        async def good(value):
            await asyncio.sleep(0)
            return value

        with self.assertRaises(RuntimeError):
            await self.breaker.call_async(bad)
        self.assertEqual(self.breaker.failure_count, 1)
        self.assertEqual(await self.breaker.call_async(good, 42), 42)
        self.assertEqual(self.breaker.failure_count, 0)
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                await self.breaker.call_async(bad)
        self.assertEqual(self.breaker.state, CircuitState.OPEN)

    async def test_cancelled_half_open_probe_releases_lease_without_failure_or_fallback(self):
        self.trip()
        self.clock.advance(10)
        fallback = mock.Mock()
        self.breaker.fallback_fn = fallback
        entered = asyncio.Event()

        async def hanging():
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(self.breaker.call_async(hanging))
        await asyncio.wait_for(entered.wait(), 2)
        self.assertFalse(self.breaker.can_execute())
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        fallback.assert_not_called()
        self.assertEqual(self.breaker.failure_count, 2)
        self.assertEqual(self.breaker.total_trips, 1)

        async def good():
            return "recovered"

        self.assertEqual(await self.breaker.call_async(good), "recovered")
        self.assertEqual(self.breaker.state, CircuitState.CLOSED)

    async def test_closed_cancellation_preserves_consecutive_failure_count(self):
        self.breaker.record_failure()

        async def cancelled():
            raise asyncio.CancelledError()

        with self.assertRaises(asyncio.CancelledError):
            await self.breaker.call_async(cancelled)
        self.assertEqual(self.breaker.failure_count, 1)
        self.assertEqual(self.breaker.state, CircuitState.CLOSED)

    async def test_async_failed_trial_reopens(self):
        self.trip()
        self.clock.advance(10)

        async def bad():
            fail()

        with self.assertRaises(RuntimeError):
            await self.breaker.call_async(bad)
        self.assertEqual(self.breaker.state, CircuitState.OPEN)
        self.assertEqual(self.breaker.total_trips, 2)

    async def test_async_fallback_is_awaited_on_failure_and_open_rejection(self):
        async def fallback(value):
            await asyncio.sleep(0)
            return value + 1

        async def bad(value):
            fail()

        breaker = CircuitBreaker("async fallback", 1, fallback_fn=fallback, clock=self.clock)
        self.assertEqual(await breaker.call_async(bad, 1), 2)
        self.assertEqual(await breaker.call_async(bad, 2), 3)
        self.assertEqual(breaker.failure_count, 1)

    async def test_async_supports_sync_fallback(self):
        self.trip()
        self.breaker.fallback_fn = lambda: "fallback"
        self.assertEqual(await self.breaker.call_async(mock.AsyncMock()), "fallback")

    async def test_manual_probe_is_not_owned_by_other_async_task(self):
        self.trip()
        self.clock.advance(10)
        self.assertTrue(self.breaker.can_execute())

        async def unrelated():
            self.assertFalse(self.breaker.can_execute())
            self.breaker.record_success()

        await asyncio.create_task(unrelated())
        self.assertEqual(self.breaker.state, CircuitState.HALF_OPEN)
        self.breaker.record_success()
        self.assertEqual(self.breaker.state, CircuitState.CLOSED)


if __name__ == "__main__":
    unittest.main()
