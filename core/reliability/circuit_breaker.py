"""
A.E.G.I.S. Enterprise Circuit Breaker & Fault Isolation
========================================================
Implements the industry-standard Circuit Breaker pattern:
CLOSED -> OPEN -> HALF_OPEN

Prevents cascading outages by isolating failing shell scripts,
unresponsive hardware sensors, or hung network endpoints.

A breaker rejects future work; it cannot interrupt an already hung native
function. Configure transport deadlines (or process isolation) separately.
"""

import asyncio
from contextvars import ContextVar
import inspect
import time
import logging
import threading
from typing import Callable, Any, Optional, Dict, Awaitable, Tuple

logger = logging.getLogger("AEGIS.CircuitBreaker")


class CircuitState:
    CLOSED = "CLOSED"        # Normal execution
    OPEN = "OPEN"            # Blocked, auto-fails or executes fallback
    HALF_OPEN = "HALF_OPEN"  # Single trial execution allowed


class CircuitBreakerOpenException(Exception):
    """Raised when an operation is attempted while circuit is OPEN."""
    pass


class CircuitBreaker:
    """Thread-safe consecutive-failure breaker with one recovery probe.

    Only bookkeeping runs under the lock, never operations or notifications.
    ``clock`` must be a quick monotonic clock; its values are not Unix timestamps.
    ``on_trip`` is optional and explicitly injected: it should enqueue a bounded,
    nonblocking notification. No brain, healer or model is imported/started here.

    Manual callers must pair each successful ``can_execute()`` admission with
    ``record_success()`` or ``record_failure()`` in the same thread/async task.
    ``can_execute()`` reserves the HALF_OPEN probe; it is not a status-only query.
    Prefer ``call``/``call_async`` for automatic completion and cancellation cleanup.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 15.0,
        fallback_fn: Optional[Callable[..., Any]] = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        on_trip: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.fallback_fn = fallback_fn
        self._clock = clock
        self._on_trip = on_trip
        self._lock = threading.Lock()
        self._generation = 0
        self._probe = None
        self._manual_attempt = ContextVar("circuit_breaker_attempt", default=None)

        self.state: str = CircuitState.CLOSED
        self.failure_count: int = 0
        self.last_failure_time: float = 0.0
        self.total_trips: int = 0

    def call(self, target_fn: Callable[..., Any], *args, **kwargs) -> Any:
        """Run a synchronous operation; raised Exceptions count as failures.

        A normal return (including None) is success. Async operations must use
        ``call_async``; merely creating a coroutine cannot establish recovery.
        """
        attempt = self._admit()
        if attempt is None:
            return self._sync_result(self._fallback(*args, **kwargs))
        try:
            result = target_fn(*args, **kwargs)
        except Exception:
            self._complete(attempt, failed=True)
            if self.fallback_fn is not None:
                return self._sync_result(self.fallback_fn(*args, **kwargs))
            raise
        except BaseException:
            self._complete(attempt, cancelled=True)
            raise
        if inspect.isawaitable(result):
            self._complete(attempt, cancelled=True)
            return self._sync_result(result)  # raises without recording success
        self._complete(attempt)
        return result

    async def call_async(self, target_fn: Callable[..., Awaitable[Any]], *args, **kwargs) -> Any:
        """Await an operation, counting errors only after it actually completes.

        Cancellation propagates without a failure, fallback, or success reset.
        A cancelled recovery probe releases its reservation for another trial.
        """
        attempt = self._admit()
        if attempt is None:
            return await self._async_result(self._fallback(*args, **kwargs))
        try:
            result = await target_fn(*args, **kwargs)
        except asyncio.CancelledError:
            self._complete(attempt, cancelled=True)
            raise
        except Exception:
            self._complete(attempt, failed=True)
            if self.fallback_fn is not None:
                return await self._async_result(self.fallback_fn(*args, **kwargs))
            raise
        except BaseException:
            self._complete(attempt, cancelled=True)
            raise
        self._complete(attempt)
        return result

    def _fallback(self, *args, **kwargs):
        if self.fallback_fn is not None:
            return self.fallback_fn(*args, **kwargs)
        raise CircuitBreakerOpenException(
            f"Circuit '{self.name}' is OPEN or its recovery probe is in progress."
        )

    @staticmethod
    def _sync_result(result):
        if inspect.isawaitable(result):
            if inspect.iscoroutine(result):
                result.close()  # prevent an un-awaited coroutine/resource warning
            raise TypeError("Use call_async() for an awaitable operation or fallback")
        return result

    @staticmethod
    async def _async_result(result):
        return await result if inspect.isawaitable(result) else result

    def _admit(self) -> Optional[Tuple[int, Any]]:
        with self._lock:
            if self.state == CircuitState.OPEN:
                if self._clock() - self.last_failure_time < self.recovery_timeout:
                    return None
                self.state = CircuitState.HALF_OPEN
            if self.state == CircuitState.HALF_OPEN:
                if self._probe is not None:
                    return None
                self._probe = object()
                return self._generation, self._probe
            return self._generation, None

    def _complete(self, attempt, *, failed=False, cancelled=False):
        notification = None
        failure_count = None
        with self._lock:
            generation, probe = attempt
            # An older in-flight operation must not close/retrip a newer circuit.
            if generation != self._generation:
                return
            if self.state == CircuitState.HALF_OPEN:
                if probe is None or probe is not self._probe:
                    return
                self._probe = None
            elif self.state != CircuitState.CLOSED:
                return
            if cancelled:
                return
            if not failed:
                self.failure_count = 0
                if self.state == CircuitState.HALF_OPEN:
                    self.state = CircuitState.CLOSED
                    self._generation += 1
                return
            self.failure_count += 1
            failure_count = self.failure_count
            self.last_failure_time = self._clock()
            if self.state == CircuitState.HALF_OPEN or self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                self.total_trips += 1
                self._generation += 1
                notification = self._status_locked()

        # Neither exception messages nor error payloads are formatted or logged.
        logger.warning("[CircuitBreaker:%s] Failure #%d", self.name, failure_count)
        if notification is not None and self._on_trip is not None:
            try:
                self._on_trip(notification)
            except Exception:
                logger.warning("[CircuitBreaker:%s] Trip notification failed", self.name)

    @staticmethod
    def _owner():
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        return threading.current_thread(), task

    def can_execute(self) -> bool:
        """Admit manual work, reserving the sole HALF_OPEN probe if available."""
        attempt = self._admit()
        if attempt is None:
            return False
        self._manual_attempt.set((self._owner(), attempt))
        return True

    def _record_manual(self, *, failed=False):
        stored = self._manual_attempt.get()
        if stored is not None and stored[0] == self._owner():
            self._manual_attempt.set(None)
            attempt = stored[1]
        else:
            # Retain support for direct record_* use in CLOSED state. Without
            # an admission, callers cannot complete somebody else's probe.
            with self._lock:
                if self.state != CircuitState.CLOSED:
                    return
                attempt = self._generation, None
        self._complete(attempt, failed=failed)

    def record_success(self):
        """Complete manual work successfully and reset consecutive failures."""
        self._record_manual()

    def record_failure(self, error: Any = ""):
        """Complete manual work as failed; error payloads are never logged."""
        self._record_manual(failed=True)

    def _handle_failure(self, exception: Exception):
        self.record_failure(exception)

    def reset(self):
        """Manually forces the breaker back into normal CLOSED state."""
        with self._lock:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self._probe = None
            self._generation += 1
        self._manual_attempt.set(None)

    def get_status(self) -> Dict[str, Any]:
        """Return a snapshot; last_state_change is a monotonic clock value."""
        with self._lock:
            return self._status_locked()

    def _status_locked(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "failure_count": self.failure_count,
            "threshold": self.failure_threshold,
            "total_trips": self.total_trips,
            "last_state_change": self.last_failure_time
        }


# Enterprise Global Breakers
admin_shell_breaker = CircuitBreaker("admin_shell", failure_threshold=3, recovery_timeout=15.0)
hardware_sensor_breaker = CircuitBreaker("hardware_sensors", failure_threshold=3, recovery_timeout=10.0)
network_audit_breaker = CircuitBreaker("network_audit", failure_threshold=3, recovery_timeout=12.0)

CircuitBreakerState = CircuitState


class CircuitBreakerManager:
    """Enterprise registry for dynamically managing circuit breakers per squad and subsystem."""

    def __init__(self):
        self._lock = threading.Lock()
        self._breakers: Dict[str, CircuitBreaker] = {
            "admin_shell": admin_shell_breaker,
            "hardware_sensors": hardware_sensor_breaker,
            "network_audit": network_audit_breaker,
        }

    def get_breaker(self, name: str, failure_threshold: int = 3, recovery_timeout: float = 15.0) -> CircuitBreaker:
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(
                    name=name,
                    failure_threshold=failure_threshold,
                    recovery_timeout=recovery_timeout
                )
            return self._breakers[name]

    def list_breakers(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            breakers = list(self._breakers.items())
        return {name: b.get_status() for name, b in breakers}


circuit_breaker_manager = CircuitBreakerManager()
