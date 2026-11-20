"""In-process publish/subscribe bus that feeds the ``/api/events`` Server-Sent Events stream."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional, Set


class EventBus:
    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: str, data: Dict[str, Any]) -> None:
        """Publish from any thread; delivery always happens on the event loop."""
        message = {"event": event, "data": data, "ts": time.time()}
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if self._loop is not None and running is not self._loop:
            if not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._dispatch, message)
            return
        self._dispatch(message)

    def _dispatch(self, message: Dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # A stalled client must never block ingestion; it simply misses intermediate updates.
                pass

    @asynccontextmanager
    async def subscribe(self, maxsize: int = 512) -> AsyncIterator[asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)
