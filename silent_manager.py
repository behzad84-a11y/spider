"""
Silent Manager — Quiet Hours Controller
=========================================
Controls message suppression during off-hours.
Default silent window: 23:00 — 07:00 local time.

Usage:
    sm = SilentManager()
    if sm.is_silent():
        # skip non-critical messages
    else:
        # send normally
"""

import logging
from datetime import datetime, time as dtime
from typing import Optional

logger = logging.getLogger(__name__)


class SilentManager:
    """
    Manages quiet hours for the trading bot.
    During silent hours, only critical alerts are sent.
    """

    # Message priority levels
    CRITICAL = 0     # Always sent (crashes, emergency exits)
    HIGH = 1         # Sent unless deep silence (trade entries/exits)
    NORMAL = 2       # Suppressed during silent hours
    LOW = 3          # Always suppressed during silent hours (scans, digests)

    def __init__(
        self,
        silent_start_hour: int = 23,
        silent_start_minute: int = 0,
        silent_end_hour: int = 7,
        silent_end_minute: int = 0,
        enabled: bool = True,
    ):
        self._start = dtime(silent_start_hour, silent_start_minute)
        self._end = dtime(silent_end_hour, silent_end_minute)
        self.enabled = enabled
        self._override_until: Optional[datetime] = None
        logger.info(
            f"SILENT MANAGER: {self._start.strftime('%H:%M')} - "
            f"{self._end.strftime('%H:%M')} | Enabled={enabled}"
        )

    def is_silent(self) -> bool:
        """Check if we are currently in silent hours."""
        if not self.enabled:
            return False

        # Check override (e.g., user forced active via command)
        if self._override_until and datetime.now() < self._override_until:
            return False

        now = datetime.now().time()

        # Handle overnight range (23:00 — 07:00)
        if self._start > self._end:
            return now >= self._start or now < self._end
        else:
            return self._start <= now < self._end

    def should_send(self, priority: int = NORMAL) -> bool:
        """
        Check if a message with given priority should be sent.
        
        Args:
            priority: CRITICAL(0), HIGH(1), NORMAL(2), LOW(3)
        
        Returns:
            True if message should be sent.
        """
        if priority <= self.CRITICAL:
            return True  # Critical always sent
        if not self.is_silent():
            return True  # Outside silent hours, everything goes
        if priority <= self.HIGH:
            return True  # High priority during silent still goes
        return False  # NORMAL and LOW suppressed

    def override_active(self, minutes: int = 60):
        """Temporarily disable silent mode for N minutes."""
        self._override_until = datetime.now()
        from datetime import timedelta
        self._override_until += timedelta(minutes=minutes)
        logger.info(f"SILENT MANAGER: Override active for {minutes} minutes")

    def clear_override(self):
        """Clear any temporary override."""
        self._override_until = None

    def set_window(self, start_hour: int, start_min: int, end_hour: int, end_min: int):
        """Update the silent window."""
        self._start = dtime(start_hour, start_min)
        self._end = dtime(end_hour, end_min)
        logger.info(
            f"SILENT MANAGER: Window updated to "
            f"{self._start.strftime('%H:%M')} - {self._end.strftime('%H:%M')}"
        )

    def get_status(self) -> dict:
        """Return current status for dashboard."""
        return {
            'enabled': self.enabled,
            'silent_now': self.is_silent(),
            'window': f"{self._start.strftime('%H:%M')} - {self._end.strftime('%H:%M')}",
            'override_active': (
                self._override_until is not None 
                and datetime.now() < self._override_until
            ),
        }

    def __repr__(self):
        state = "SILENT" if self.is_silent() else "ACTIVE"
        return (
            f"SilentManager({state}, "
            f"{self._start.strftime('%H:%M')}-{self._end.strftime('%H:%M')})"
        )
