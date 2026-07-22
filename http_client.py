"""Shared outbound HTTP client with bounded retries and provider telemetry."""
from __future__ import annotations

import os
import time
from functools import lru_cache
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import provider_health

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


def _provider_name(url: str) -> str:
    return urlparse(url).hostname or "unknown"


def request(method: str, url: str, *, timeout: float | None = None, **kwargs) -> requests.Response:
    """Issue a pooled request and record host-level success/failure telemetry."""
    provider = _provider_name(url)
    started = time.monotonic()
    try:
        response = session().request(
            method,
            url,
            timeout=_DEFAULT_TIMEOUT if timeout is None else timeout,
            **kwargs,
        )
    except requests.RequestException as exc:
        provider_health.record_failure(provider, exc, (time.monotonic() - started) * 1000)
        raise
    latency_ms = (time.monotonic() - started) * 1000
    if response.status_code >= 500 or response.status_code == 429:
        provider_health.record_failure(provider, f"HTTP {response.status_code}", latency_ms)
    else:
        provider_health.record_success(provider, latency_ms)
    return response


def get(url: str, *, timeout: float | None = None, **kwargs) -> requests.Response:
    return request("GET", url, timeout=timeout, **kwargs)
