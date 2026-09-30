"""Transport retry classification and server-directed delay, without SDK retries."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import random
import math


def transient_error(exc):
    return (isinstance(exc, (TimeoutError, ConnectionError))
            or getattr(exc, "status_code", None) in {408, 429, 500, 502, 503, 504}
            or type(exc).__name__ in {"APITimeoutError", "APIConnectionError"})


def retry_delay(exc, attempt):
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {})
    value = headers.get("retry-after")
    if value is not None:
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            try:
                seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                seconds = float("nan")
        if math.isfinite(seconds):
            return max(0.0, seconds)
    return random.uniform(0.5, 1.0) * min(60.0, 2.0 ** attempt)
