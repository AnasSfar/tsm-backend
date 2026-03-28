from __future__ import annotations

from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import DEFAULT_TIMEOUT, HEADERS


class AppleMusicSession(Session):
    default_timeout: int = DEFAULT_TIMEOUT

    def request(self, *args, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", self.default_timeout)
        return super().request(*args, **kwargs)



def build_session() -> AppleMusicSession:
    retry = Retry(
        total=3,
        read=3,
        connect=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    session = AppleMusicSession()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session
