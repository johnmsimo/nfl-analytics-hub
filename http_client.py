"""Shared outbound HTTP client with bounded retries and consistent defaults."""
from __future__ import annotations

import os
from functools import lru_cache

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_DEFAULT_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT_SEC", "25"))
_RETRY_TOTAL = int(os.environ.get("HTTP_RETRY_TOTAL", "3"))
_RETRY_BACKOFF = float(os.environ.get("HTTP_RETRY_BACKOFF_SEC", "0.5"))
_RETRY_STATUSES = (429, 500, 502, 503, 504)


@lru_cache(maxsize=1)
def session() -> requests.Session:
    """Return a process-local pooled session with safe idempotent retries."""
    retry = Retry(
        total=_RETRY_TOTAL,
        connect=_RETRY_TOTAL,
        read=_RETRY_TOTAL,
        status=_RETRY_TOTAL,
        backoff_factor=_RETRY_BACKOFF,
        status_forcelist=_RETRY_STATUSES,
        allowed_methods=frozenset({"GET", "HEAD", "OPTIONS"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    client = requests.Session()
    client.mount("https://", adapter)
    client.mount("http://", adapter)
    client.headers.update({"User-Agent": os.environ.get("HTTP_USER_AGENT", "nfl-analytics-hub/3.0")})
    return client


def request(method: str, url: str, *, timeout: float | None = None, **kwargs) -> requests.Response:
    """Issue an outbound request with the shared pool and default timeout."""
    return session().request(method, url, timeout=_DEFAULT_TIMEOUT if timeout is None else timeout, **kwargs)


def get(url: str, *, timeout: float | None = None, **kwargs) -> requests.Response:
    return request("GET", url, timeout=timeout, **kwargs)
