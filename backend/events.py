"""Utilities for structured agent event emission."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def build_event(event: str, data: Dict[str, Any], agent: Optional[str] = None) -> Dict[str, Any]:
    """Create a standardized event payload with UTC timestamp."""
    payload: Dict[str, Any] = {
        "event": event,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if agent:
        payload["agent"] = agent
    return payload


def emit_event(state: Dict[str, Any], event: str, data: Dict[str, Any], agent: Optional[str] = None) -> None:
    """
    Emit an event into state history and optionally to a live callback.

    The callback is expected to be a sync callable that accepts one dict payload.
    """
    payload = build_event(event=event, data=data, agent=agent)
    state.setdefault("agent_events", []).append(payload)

    callback = state.get("event_callback")
    if not callback:
        return

    try:
        callback(payload)
    except Exception:  # pragma: no cover - best-effort real-time delivery
        logger.exception("Failed to emit real-time event: %s", payload.get("event"))
