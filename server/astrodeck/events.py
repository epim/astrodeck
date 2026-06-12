"""Async event bus.

Everything that happens in AstroDeck (device status, preview frames, sequence
progress, guide pulses, log lines) flows through one bus and fans out to all
connected WebSocket clients and any internal subscribers.
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Event:
    type: str
    data: dict[str, Any]
    ts: float = field(default_factory=time.time)

    def to_json(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data, "ts": self.ts}


class EventBus:
    def __init__(self, history: int = 200):
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._history: deque[Event] = deque(maxlen=history)

    def subscribe(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=500)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(q)

    def publish(self, type: str, **data: Any) -> None:
        ev = Event(type=type, data=data)
        if type == "log":
            self._history.append(ev)
        for q in list(self._subscribers):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                # Slow consumer: drop oldest to keep the stream live.
                try:
                    q.get_nowait()
                    q.put_nowait(ev)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def log(self, level: str, message: str, source: str = "hub") -> None:
        self.publish("log", level=level, message=message, source=source)

    @property
    def log_history(self) -> list[dict[str, Any]]:
        return [e.to_json() for e in self._history]


bus = EventBus()
