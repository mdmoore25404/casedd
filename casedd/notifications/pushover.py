"""Pushover webhook notification sender.

Sends an HTTP POST to a Pushover webhook URL when a trigger rule fires.
Pushover's webhook receiver (https://pushover.net/dashboard) accepts
arbitrary JSON and converts it to mobile/desktop notifications using
selectors configured in the dashboard.

Configure your Pushover webhook with these field selectors:
    - Title selector: ``{{title}}``
    - Body selector:  ``{{message}}``

The payload also includes ``source`` and ``value`` fields for advanced
selector configurations.

Public API:
    - :func:`send_pushover_webhook` — fire-and-forget async POST
"""

from __future__ import annotations

import logging

import httpx

from casedd.config import TemplateTriggerRule
from casedd.data_store import StoreValue

_log = logging.getLogger(__name__)


async def send_pushover_webhook(
    webhook_url: str,
    rule: TemplateTriggerRule,
    value: StoreValue | None,
) -> None:
    """POST a JSON alert payload to a Pushover webhook URL.

    Uses the trigger rule's ``notify_title`` / ``notify_message`` fields when
    set, otherwise falls back to sensible defaults derived from the rule.

    Args:
        webhook_url: Pushover webhook URL created in the Pushover dashboard.
        rule: The trigger rule that just activated.
        value: Current value of the monitored data-store key at activation.
    """
    title = rule.notify_title or f"CASEDD Alert: {rule.source}"
    message = rule.notify_message or f"{rule.source} triggered (value: {value})"
    payload: dict[str, str] = {
        "title": title,
        "message": message,
        "source": rule.source,
        "value": str(value),
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
        _log.info("Pushover webhook sent: %s", title)
    except Exception:
        _log.warning("Failed to send Pushover webhook notification", exc_info=True)
