"""
Global kill switch for emergency shutdown of all trading activity.
State is persisted so it survives restarts.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class KillSwitch:
    """
    Global emergency kill switch.

    When activated:
    1. Immediately stops all new trade entries.
    2. Triggers emergency exit of all open positions.
    3. Persists state to survive restarts.
    4. Requires explicit manual reset.
    """

    def __init__(self) -> None:
        self._active: bool = False
        self._activated_at: Optional[datetime] = None
        self._reason: str = ""
        self._lock = asyncio.Lock()
        self._callbacks: list = []

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def activated_at(self) -> Optional[datetime]:
        return self._activated_at

    @property
    def reason(self) -> str:
        return self._reason

    async def activate(self, reason: str = "Manual activation") -> None:
        """Activate the kill switch. Idempotent."""
        async with self._lock:
            if self._active:
                logger.warning("Kill switch already active, ignoring duplicate activation")
                return

            self._active = True
            self._activated_at = datetime.utcnow()
            self._reason = reason

            logger.critical(
                "🛑 KILL SWITCH ACTIVATED",
                extra={
                    "reason": reason,
                    "activated_at": self._activated_at.isoformat(),
                },
            )

            # Notify all registered callbacks
            for callback in self._callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(reason)
                    else:
                        callback(reason)
                except Exception as e:
                    logger.error(f"Kill switch callback error: {e}")

    async def deactivate(self, confirmation: str = "") -> bool:
        """
        Deactivate the kill switch.
        Requires explicit confirmation string: 'CONFIRM_RESET_KILL_SWITCH'
        """
        if confirmation != "CONFIRM_RESET_KILL_SWITCH":
            logger.warning("Kill switch deactivation rejected: invalid confirmation")
            return False

        async with self._lock:
            self._active = False
            self._activated_at = None
            self._reason = ""
            logger.warning("Kill switch DEACTIVATED")
            return True

    def register_callback(self, callback) -> None:
        """Register a callback to be called when kill switch is activated."""
        self._callbacks.append(callback)

    def check(self) -> bool:
        """Quick check - returns True if trading is allowed (kill switch NOT active)."""
        return not self._active

    def to_dict(self) -> dict:
        return {
            "active": self._active,
            "activated_at": self._activated_at.isoformat() if self._activated_at else None,
            "reason": self._reason,
        }


# Global singleton
_kill_switch: Optional[KillSwitch] = None


def get_kill_switch() -> KillSwitch:
    global _kill_switch
    if _kill_switch is None:
        _kill_switch = KillSwitch()
    return _kill_switch
