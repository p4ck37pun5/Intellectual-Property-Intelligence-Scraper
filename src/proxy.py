"""
Proxy Rotation Manager
Supports three proxy modes (in priority order):
  1. Apify Proxy (auto-detected from APIFY_PROXY_PASSWORD env var)
  2. Custom proxy list (passed via PROXY_URLS env var, comma-separated)
  3. Direct connection (no proxy)

Usage:
    proxy_mgr = ProxyManager()
    async with proxy_mgr.client(jurisdiction="US") as client:
        resp = await client.get(url)
"""

import os
import logging
import random
import asyncio
from itertools import cycle
from typing import AsyncIterator
from contextlib import asynccontextmanager

import httpx

log = logging.getLogger(__name__)

# Apify Proxy country groups per jurisdiction
JURISDICTION_COUNTRY_MAP = {
    "US": "US",
    "EP": "DE",   # Germany as EU node for EPO
    "WO": "CH",   # Switzerland for WIPO Geneva
}

MAX_PROXY_RETRIES = 3


class ProxyManager:
    """Manages proxy selection and rotation across jurisdictions."""

    def __init__(self) -> None:
        self._apify_password = os.getenv("APIFY_PROXY_PASSWORD", "")
        self._custom_proxies = self._load_custom_proxies()
        self._mode = self._detect_mode()
        self._custom_cycle = cycle(self._custom_proxies) if self._custom_proxies else None

        log.info(f"ProxyManager initialized: mode={self._mode}, "
                 f"custom_proxies={len(self._custom_proxies)}")

    def _detect_mode(self) -> str:
        if self._apify_password:
            return "apify"
        if self._custom_proxies:
            return "custom"
        return "direct"

    def _load_custom_proxies(self) -> list[str]:
        raw = os.getenv("PROXY_URLS", "")
        if not raw:
            return []
        proxies = [p.strip() for p in raw.split(",") if p.strip()]
        log.info(f"Loaded {len(proxies)} custom proxies")
        return proxies

    def get_proxy_url(self, jurisdiction: str = "") -> str | None:
        """Return proxy URL string for httpx, or None for direct."""
        if self._mode == "apify":
            country = JURISDICTION_COUNTRY_MAP.get(jurisdiction, "")
            session_id = f"ip_scraper_{jurisdiction}_{random.randint(1000, 9999)}"
            base = "http://groups-RESIDENTIAL"
            if country:
                base = f"http://groups-RESIDENTIAL,country-{country}"
            return (
                f"{base},session-{session_id}:"
                f"{self._apify_password}@proxy.apify.com:8000"
            )

        if self._mode == "custom" and self._custom_cycle:
            return next(self._custom_cycle)

        return None  # direct

    @asynccontextmanager
    async def client(
        self,
        jurisdiction: str = "",
        timeout: float = 30.0,
        headers: dict | None = None,
    ) -> AsyncIterator[httpx.AsyncClient]:
        """
        Async context manager that yields an httpx.AsyncClient configured
        with the appropriate proxy for the given jurisdiction.
        """
        proxy_url = self.get_proxy_url(jurisdiction)
        proxies = {"http://": proxy_url, "https://": proxy_url} if proxy_url else None

        default_headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }
        if headers:
            default_headers.update(headers)

        client_kwargs: dict = {
            "timeout": timeout,
            "follow_redirects": True,
            "headers": default_headers,
        }
        if proxies:
            client_kwargs["proxies"] = proxies

        if proxy_url:
            log.debug(f"Using proxy for {jurisdiction}: {proxy_url[:40]}…")
        else:
            log.debug(f"Direct connection for {jurisdiction}")

        async with httpx.AsyncClient(**client_kwargs) as client:
            yield client

    async def fetch_with_rotation(
        self,
        url: str,
        method: str = "GET",
        jurisdiction: str = "",
        **kwargs,
    ) -> httpx.Response | None:
        """
        Fetch a URL, rotating proxy on failure.
        Returns Response or None after MAX_PROXY_RETRIES failures.
        """
        for attempt in range(1, MAX_PROXY_RETRIES + 1):
            async with self.client(jurisdiction=jurisdiction) as client:
                try:
                    resp = await client.request(method, url, **kwargs)
                    resp.raise_for_status()
                    return resp
                except httpx.HTTPStatusError as e:
                    code = e.response.status_code
                    log.warning(f"HTTP {code} on attempt {attempt}/{MAX_PROXY_RETRIES} [{url[:60]}]")
                    if code in (403, 429, 503):
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise
                except (httpx.ConnectError, httpx.TimeoutException) as e:
                    log.warning(f"Connection error attempt {attempt}/{MAX_PROXY_RETRIES}: {e}")
                    await asyncio.sleep(2 ** attempt)

        log.error(f"All {MAX_PROXY_RETRIES} proxy attempts failed for {url[:60]}")
        return None


# Singleton — import and use directly
proxy_manager = ProxyManager()