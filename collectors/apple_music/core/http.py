from __future__ import annotations

import time
from collections.abc import Callable, Hashable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from requests import RequestException, Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import (
    DEFAULT_TIMEOUT,
    FAILURE_RETRY_ROUNDS,
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


def retry_failed(
    failed: list[Hashable],
    fetch: Callable[[Any], Any],
    *,
    label: str,
    rounds: int = FAILURE_RETRY_ROUNDS,
    workers: int = 8,
) -> tuple[dict[Any, Any], list[tuple[Any, str]]]:
    """Re-fetch items that failed in the main pool (few, low concurrency, with
    a pause between rounds) instead of skipping them on the first error.
    Returns (recovered results by item, still-failed (item, error))."""
    recovered: dict[Any, Any] = {}
    pending = list(failed)
    errors: dict[Any, str] = {}
    for round_no in range(1, rounds + 1):
        if not pending:
            break
        time.sleep(2.0 * round_no)
        print(f"{label} Retrying {len(pending)} failed item(s), round {round_no}/{rounds}")

        def _one(item):
            try:
                return item, fetch(item), None
            except (RequestException, RuntimeError) as exc:
                return item, None, str(exc)

        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(pending)))) as executor:
            outcomes = list(executor.map(_one, pending))
        pending = []
        for item, result, error in outcomes:
            if error is None:
                recovered[item] = result
                errors.pop(item, None)
            else:
                errors[item] = error
                pending.append(item)
    return recovered, [(item, errors.get(item, "failed")) for item in pending]
