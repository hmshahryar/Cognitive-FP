"""Chooses which configured API key to use. Holds positions and timers, and never logs a key."""

import time
from collections.abc import Callable
from functools import lru_cache


class KeyPool:
    """Stays on a working key, and skips keys that are cooling down."""

    def __init__(self, count: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._count = count
        self._clock = clock
        self._current = 0
        self._ready_at = [0.0] * count

    def order(self) -> list[int]:
        """Keys to try now. If all are cooling down, try the one that recovers first."""
        now = self._clock()
        rotated = [(self._current + step) % self._count for step in range(self._count)]
        ready = [index for index in rotated if self._ready_at[index] <= now]
        return ready or [min(rotated, key=lambda index: self._ready_at[index])]

    def cool_down(self, index: int, seconds: float) -> None:
        self._ready_at[index] = max(self._ready_at[index], self._clock() + seconds)

    def succeeded(self, index: int) -> None:
        self._ready_at[index] = 0.0
        self._current = index


@lru_cache(maxsize=4)
def shared_key_pool(keys: tuple[str, ...]) -> KeyPool:
    """One pool per key set, so every agent in the process knows which keys are used up."""
    return KeyPool(len(keys))
