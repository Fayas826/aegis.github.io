"""
AEGIS Desktop Execution Lock
============================
Phase 4.3: Authoritative mutex for physical desktop interactions.
Serializes operations that share the physical Windows desktop, keyboard,
mouse, and foreground window state. Prevents concurrent agents from
competing for focus, typing over each other, or stealing foreground focus.
"""

import time
import asyncio
import threading
from typing import Optional
from dataclasses import dataclass


@dataclass
class LockHolder:
    owner: str
    acquired_at: float
    timeout: float
    task_id: Optional[str] = None


class DesktopExecutionLock:
    """Authoritative singleton mutex for physical desktop interactions."""
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(DesktopExecutionLock, cls).__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self):
        self._async_lock = asyncio.Lock()
        self._thread_lock = threading.RLock()
        self._current_holder: Optional[LockHolder] = None

    @property
    def current_owner(self) -> Optional[str]:
        with self._thread_lock:
            if self._current_holder:
                # Check for expiry
                if time.time() - self._current_holder.acquired_at > self._current_holder.timeout:
                    print(f"[DESKTOP_LOCK] Expired lock held by '{self._current_holder.owner}' (timeout {self._current_holder.timeout}s). Releasing.")
                    self._current_holder = None
                else:
                    return self._current_holder.owner
            return None

    @property
    def owner(self) -> Optional[str]:
        return self.current_owner

    def is_locked(self) -> bool:
        return self.current_owner is not None

    async def acquire_async(self, owner: str, timeout: float = 30.0, task_id: Optional[str] = None) -> bool:
        """Acquires the desktop execution lock asynchronously with bounded timeout."""
        start = time.time()
        while time.time() - start < timeout:
            with self._thread_lock:
                cur = self.current_owner
                if cur is None or cur == owner:
                    self._current_holder = LockHolder(
                        owner=owner,
                        acquired_at=time.time(),
                        timeout=timeout,
                        task_id=task_id,
                    )
                    return True
            await asyncio.sleep(0.05)
        return False

    def acquire_sync(self, owner: str, timeout: float = 30.0, task_id: Optional[str] = None) -> bool:
        """Acquires the desktop execution lock synchronously with bounded timeout."""
        start = time.time()
        while time.time() - start < timeout:
            with self._thread_lock:
                cur = self.current_owner
                if cur is None or cur == owner:
                    self._current_holder = LockHolder(
                        owner=owner,
                        acquired_at=time.time(),
                        timeout=timeout,
                        task_id=task_id,
                    )
                    return True
            time.sleep(0.05)
        return False

    def release(self, owner: Optional[str] = None, force: bool = False) -> bool:
        """Releases the lock if held by owner or if force is True."""
        with self._thread_lock:
            if force or owner is None or (self._current_holder and self._current_holder.owner == owner):
                self._current_holder = None
                return True
            return False

    def force_release(self):
        """Emergency lock release for recovery or cancellation."""
        with self._thread_lock:
            self._current_holder = None


desktop_execution_lock = DesktopExecutionLock()


class AsyncDesktopLockContext:
    def __init__(self, owner: str, timeout: float = 30.0, task_id: Optional[str] = None):
        self.owner = owner
        self.timeout = timeout
        self.task_id = task_id
        self.acquired = False

    async def __aenter__(self):
        self.acquired = await desktop_execution_lock.acquire_async(
            owner=self.owner, timeout=self.timeout, task_id=self.task_id
        )
        if not self.acquired:
            raise TimeoutError(f"Could not acquire DesktopExecutionLock for '{self.owner}' within {self.timeout}s.")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.acquired:
            desktop_execution_lock.release(self.owner)


class SyncDesktopLockContext:
    def __init__(self, owner: str, timeout: float = 30.0, task_id: Optional[str] = None):
        self.owner = owner
        self.timeout = timeout
        self.task_id = task_id
        self.acquired = False

    def __enter__(self):
        self.acquired = desktop_execution_lock.acquire_sync(
            owner=self.owner, timeout=self.timeout, task_id=self.task_id
        )
        if not self.acquired:
            raise TimeoutError(f"Could not acquire DesktopExecutionLock for '{self.owner}' within {self.timeout}s.")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.acquired:
            desktop_execution_lock.release(self.owner)
