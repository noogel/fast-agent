"""只处理可识别的临时故障；不把协议错误或业务拒绝当作重试理由。"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import random
import httpx2
from openai import APIConnectionError, APIStatusError

MAX_DELAY = 30.0


def retryable(exc):
    if isinstance(exc, APIStatusError):
        return exc.status_code in {429, 500, 502, 503, 504}
    return isinstance(exc, (APIConnectionError, httpx2.TimeoutException,
                            httpx2.NetworkError, httpx2.RemoteProtocolError))


def retry_delay(exc, attempt):
    """Retry-After 优先；服务要求等待过长时停止，不提前重试。"""
    response = getattr(exc, 'response', None)
    value = response.headers.get('retry-after') if response is not None else None
    if value is not None:
        try:
            seconds = float(value)
        except ValueError:
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                seconds = max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                seconds = None
        if seconds is not None and math.isfinite(seconds) and seconds >= 0:
            return seconds if seconds <= MAX_DELAY else None
    return min(2 ** (attempt - 1) * random.uniform(.8, 1.2), MAX_DELAY)
