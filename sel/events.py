"""In-process publish/subscribe for pushing changes to open browser tabs.

The review app used to poll; now each tab holds one Server-Sent Events stream
(``GET /api/events``) and the server pushes call status changes, simulator
room updates and WebRTC signalling through it. Browsers allow only six
HTTP/1.1 connections per origin across all tabs, so a tab multiplexes every
topic it needs over that single stream instead of opening one per topic.

A subscriber that falls behind (its queue fills up) is dropped. Its stream
ends, the browser's EventSource reconnects, and the first event on the new
stream is a full snapshot, so nothing is silently missed.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any

QUEUE_SIZE = 512


@dataclass(frozen=True)
class Event:
    topic: str
    type: str
    data: Any
    to: str | None = None  # deliver only to the subscriber with this client id
    origin: str | None = None  # client id (tab) whose request caused the event


class Subscription:
    def __init__(self, bus: EventBus, topics: set[str], client: str | None):
        self.bus = bus
        self.topics = set(topics)
        self.client = client
        self.queue: queue.Queue[Event | None] = queue.Queue(QUEUE_SIZE)

    def get(self, timeout: float) -> Event | None:
        """The next event, ``None`` when the stream should end; raises ``queue.Empty`` on timeout."""
        return self.queue.get(timeout=timeout)

    def close(self) -> None:
        self.bus.unsubscribe(self)


class EventBus:
    def __init__(self) -> None:
        self._subs: set[Subscription] = set()
        self._lock = threading.Lock()

    def subscribe(self, topics: set[str], client: str | None = None) -> Subscription:
        sub = Subscription(self, topics, client)
        with self._lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            self._subs.discard(sub)

    def subscribers(self, topic: str) -> int:
        with self._lock:
            return sum(1 for s in self._subs if topic in s.topics)

    def publish(self, topic: str, type: str, data: Any, to: str | None = None, origin: str | None = None) -> None:
        event = Event(topic, type, data, to, origin)
        with self._lock:
            targets = [s for s in self._subs if topic in s.topics and (to is None or s.client == to)]
        for sub in targets:
            try:
                sub.queue.put_nowait(event)
            except queue.Full:
                # Too slow to keep up: end its stream; the reconnect starts from a snapshot.
                self.unsubscribe(sub)
                _end(sub)

    def close(self) -> None:
        """End every open stream (server shutdown, tests)."""
        with self._lock:
            subs, self._subs = self._subs, set()
        for sub in subs:
            _end(sub)


def _end(sub: Subscription) -> None:
    try:
        sub.queue.put_nowait(None)
    except queue.Full:
        try:
            sub.queue.get_nowait()
            sub.queue.put_nowait(None)
        except (queue.Empty, queue.Full):
            pass
