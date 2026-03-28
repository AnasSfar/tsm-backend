from __future__ import annotations

from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import (
    DEFAULT_TIMEOUT,
    HEADERS,
    RETRY_BACKOFF,
    RETRY_STATUS_FORCELIST,
    RETRY_TOTAL,
)


class AppleMusicSession(Session):
    default_timeout: int = DEFAULT_TIMEOUT

    def request(self, *args, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", self.default_timeout)
        return super().request(*args, **kwargs)


def build_session(
    *,
    retry_total: int = RETRY_TOTAL,
    retry_backoff: float = RETRY_BACKOFF,
    timeout: int = DEFAULT_TIMEOUT,
) -> AppleMusicSession:
    retry = Retry(
        total=retry_total,
        read=retry_total,
        connect=retry_total,
        backoff_factor=retry_backoff,
        status_forcelist=RETRY_STATUS_FORCELIST,
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    session = AppleMusicSession()
    session.default_timeout = timeout
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session
