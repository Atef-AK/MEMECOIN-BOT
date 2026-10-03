"""
Async event bus for decoupled communication between bot components.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)

EventHandler = Callable[..., Coroutine[Any, Any, None]]


class EventBus:
    """Simple async publish-subscribe event bus."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._lock = asyncio.Lock()

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        """Subscribe a handler to an event type."""
        self._handlers[event_type].append(handler)
        logger.debug(f"Subscribed {handler.__name__} to {event_type}")

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        """Remove a handler from an event type."""
        if handler in self._handlers[event_type]:
            self._handlers[event_type].remove(handler)

    async def publish(self, event_type: str, **data: Any) -> None:
        """Publish an event to all subscribed handlers."""
        handlers = self._handlers.get(event_type, [])
        if not handlers:
            return

        for handler in handlers:
            try:
                await handler(**data)
            except Exception as e:
                logger.error(
                    f"Event handler error: {handler.__name__} for {event_type}: {e}",
                    exc_info=True,
                )

    async def publish_concurrent(self, event_type: str, **data: Any) -> None:
        """Publish an event, running all handlers concurrently."""
        handlers = self._handlers.get(event_type, [])
        if not handlers:
            return

        tasks = []
        for handler in handlers:
            tasks.append(asyncio.create_task(self._safe_call(handler, event_type, data)))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _safe_call(
        self, handler: EventHandler, event_type: str, data: dict
    ) -> None:
        try:
            await handler(**data)
        except Exception as e:
            logger.error(
                f"Event handler error: {handler.__name__} for {event_type}: {e}",
                exc_info=True,
            )


# Event type constants
class Events:
    TOKEN_DISCOVERED = "token.discovered"
    TOKEN_ANALYZED = "token.analyzed"
    TOKEN_REJECTED = "token.rejected"
    TOKEN_CANDIDATE = "token.candidate"
    TOKEN_SCORED = "token.scored"

    TRADE_ENTERED = "trade.entered"
    TRADE_EXITED = "trade.exited"
    TRADE_FAILED = "trade.failed"
    TRADE_TARGET_HIT = "trade.target_hit"

    EMERGENCY_EXIT = "emergency.exit"
    EMERGENCY_TRIGGER = "emergency.trigger"

    KILL_SWITCH_ACTIVATED = "kill_switch.activated"
    KILL_SWITCH_DEACTIVATED = "kill_switch.deactivated"

    RISK_LIMIT_HIT = "risk.limit_hit"

    PRICE_UPDATE = "price.update"
    LIQUIDITY_CHANGE = "liquidity.change"
    DEV_SELL = "dev.sell"

    SYSTEM_ERROR = "system.error"
    SYSTEM_HEARTBEAT = "system.heartbeat"


# Global singleton
_event_bus = EventBus()


def get_event_bus() -> EventBus:
    return _event_bus
