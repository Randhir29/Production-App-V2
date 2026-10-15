"""Small in-process TTL cache for dashboard-polled reads (agent.md §7.9).

* Off by default (``RESPONSE_CACHE_TTL_S=0``): ``get_or_compute`` then just calls through.
* Single-flight: concurrent misses for the same key share one computation.
* Failures are never cached; waiters see the same exception.
* Hits return a deep copy so callers cannot corrupt the cached value.
* Must not be used for reports (agent.md §7.9).
"""

from __future__ import annotations

import asyncio
import copy
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable
from typing import Any


class TTLCache:
    def __init__(
        self,
        ttl_s: float,
        max_entries: int = 1024,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_s
        self._max = max_entries
        self._clock = clock
        self._values: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._inflight: dict[Hashable, asyncio.Future[Any]] = {}

    @property
    def enabled(self) -> bool:
        return self._ttl > 0

    def __len__(self) -> int:
        return len(self._values)

    async def get_or_compute[T](self, key: Hashable, compute: Callable[[], Awaitable[T]]) -> T:
        """Return the cached value for ``key`` or compute, store and return it."""
        if not self.enabled:
            return await compute()

        now = self._clock()
        hit = self._values.get(key)
        if hit is not None:
            expires_at, value = hit
            if expires_at > now:
                self._values.move_to_end(key)
                return copy.deepcopy(value)  # type: ignore[no-any-return]
            del self._values[key]

        pending = self._inflight.get(key)
        if pending is not None:
            return copy.deepcopy(await asyncio.shield(pending))  # type: ignore[no-any-return]

        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            value = await compute()
        except BaseException as exc:
            future.set_exception(exc)
            future.exception()  # mark retrieved when nobody is waiting
            raise
        else:
            future.set_result(value)
            self._store(key, value)
            return value
        finally:
            self._inflight.pop(key, None)

    def _store(self, key: Hashable, value: Any) -> None:
        self._values[key] = (self._clock() + self._ttl, copy.deepcopy(value))
        self._values.move_to_end(key)
        while len(self._values) > self._max:
            self._values.popitem(last=False)

    def clear(self) -> None:
        self._values.clear()
