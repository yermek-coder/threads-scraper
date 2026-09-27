"""Small helpers for consistent machine-readable application logs."""

import json
import logging


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    *,
    exc_info: bool = False,
    **fields: object,
) -> None:
    """Write one compact JSON payload after a stable event name."""
    payload = json.dumps(
        fields,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    logger.log(level, "%s %s", event, payload, exc_info=exc_info)
