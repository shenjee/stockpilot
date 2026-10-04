"""Server-side event broadcast bus for the T+0 desktop service.

The formal Python service pushes authoritative ``t0_app_v2`` event envelopes
to connected renderers over the ``/events`` WebSocket. ``EventPublisher`` owns
two distinct concerns:

* **Delivery revision** (the envelope ``revision``): a process-wide counter
  for service broadcasts. Revision 0 is the initial watermark. Connecting
  clients receive the current watermark without consuming a revision.
  Subscription, initialization and broadcast enqueueing share one lock, so
  every subscriber sees its baseline before all subsequent broadcasts.
* **Trade revision** (``payload.trade_revision``): owned by
  :class:`~packages.t0assistant.trading.api.TradeCommandApi`, not here. The
  publisher only carries it through unchanged.

The publisher is transport-owned (it feeds the WebSocket) and is the concrete
implementation of the transport-agnostic
:class:`~packages.t0assistant.trading.api.TradeEventPublisher` Protocol.
"""

from __future__ import annotations

import json
from queue import Queue
from threading import Lock
from typing import Any


class EventPublisher:
    """Broadcast ``t0_app_v2`` envelopes to WebSocket subscribers."""

    def __init__(self, *, service_generation: int) -> None:
        if (
            isinstance(service_generation, bool)
            or not isinstance(service_generation, int)
            or service_generation < 1
        ):
            raise ValueError("service_generation must be a positive integer")
        self._service_generation = service_generation
        self._last_revision = 0
        self._subscribers: set[Queue] = set()
        self._lock = Lock()

    @property
    def service_generation(self) -> int:
        return self._service_generation

    def subscribe(self, *, initialize: bool = False) -> Queue:
        """Register a queue, optionally seeding its service watermark atomically.

        The WebSocket registers before acknowledging the HTTP upgrade. Thus a
        command issued on socket open cannot publish before subscription.
        Initialization does not consume a broadcast revision.
        """
        queue: Queue = Queue()
        with self._lock:
            if initialize:
                queue.put({
                    "schema_version": "t0_app_v2",
                    "service_generation": self._service_generation,
                    "session_id": None,
                    "revision": self._last_revision,
                    "event_type": "service_status",
                    "payload": {
                        "state": "connected",
                        "message": "本地服务事件通道已连接",
                    },
                })
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: Queue) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def publish(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        session_id: str | None = None,
        operation_id: str | None = None,
    ) -> int:
        """Claim a revision, build the envelope, and enqueue it to subscribers.

        Returns the claimed envelope ``revision``. Envelopes are constructed
        without ``operation_id`` when it is ``None`` so they satisfy the frozen
        ``event_envelope`` schema (``additionalProperties: false``).
        """
        # Unbounded subscriber queues never wait for a network write. Keep the
        # entire allocation/enqueue operation under the lock: allocating first
        # and enqueueing later allows another publisher to overtake this event.
        with self._lock:
            self._last_revision += 1
            revision = self._last_revision
            envelope: dict[str, Any] = {
                "schema_version": "t0_app_v2",
                "service_generation": self._service_generation,
                "session_id": session_id,
                "revision": revision,
                "event_type": event_type,
                "payload": payload,
            }
            if operation_id is not None:
                envelope["operation_id"] = operation_id
            for queue in self._subscribers:
                queue.put(envelope)
            return revision

    def publish_envelope(self, envelope: dict[str, Any]) -> None:
        """Broadcast an already-revisioned Session envelope.

        Live/Replay revision authorities own their per-Session counters.  This
        transport method deliberately does not claim a service-scoped revision
        or rewrite the envelope.
        """

        if envelope.get("schema_version") not in {"t0_app_v2", "t0_replay_v2"}:
            raise ValueError("event envelope must use a supported schema")
        if envelope.get("service_generation") != self._service_generation:
            raise ValueError("event service_generation must match the publisher")
        session_id = envelope.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("pre-revisioned envelopes require a Session")
        with self._lock:
            subscribers = list(self._subscribers)
        for queue in subscribers:
            queue.put(dict(envelope))

    # -- TradeEventPublisher Protocol -----------------------------------

    def publish_trades_changed(
        self,
        *,
        service_generation: int,
        trade_revision: int,
        trades: list[dict[str, Any]],
        symbol: str,
        trade_date: str,
        operation_id: str | None = None,
    ) -> None:
        """Publish one authoritative scoped real ``trades_changed`` envelope.

        ``session_id`` is ``None`` because real trades are repository-scoped,
        not Session-scoped. ``payload`` carries the explicit ``symbol`` and
        ``trade_date`` scope alongside ``trade_revision`` and the scoped
        ``trades`` snapshot supplied by the command API (Issue #163).
        """
        if service_generation != self._service_generation:
            raise ValueError(
                "trade event service_generation must match the publisher"
            )
        self.publish(
            event_type="trades_changed",
            payload={
                "trade_revision": trade_revision,
                "trades": trades,
                "symbol": symbol,
                "trade_date": trade_date,
            },
            session_id=None,
            operation_id=operation_id,
        )

    @staticmethod
    def encode(envelope: dict[str, Any]) -> str:
        """Serialize an envelope for a WebSocket text frame."""
        return json.dumps(envelope, ensure_ascii=False)


__all__ = ["EventPublisher"]
