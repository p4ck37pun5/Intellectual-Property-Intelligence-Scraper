"""
Request Cache
Lightweight file-based cache for HTTP responses (development only).
Disabled automatically in Apify production environment.

Usage:
    cache = RequestCache()
    data = cache.get(key)
    if data is None:
        data = await fetch(...)
        cache.set(key, data)
"""

import os
import json
import hashlib
import logging
import time
from pathlib import Path

log = logging.getLogger(__name__)

CACHE_DIR = Path("/tmp/ip_scraper_cache")
CACHE_TTL_SECONDS = 3600 * 6


class RequestCache:
    """
    Simple disk-based JSON cache.
    Automatically disabled when running inside Apify (APIFY_IS_AT_HOME=1).
    """

    def __init__(self, ttl: int = CACHE_TTL_SECONDS) -> None:
        self._enabled = os.getenv("APIFY_IS_AT_HOME", "0") != "1"
        self._ttl = ttl

        if self._enabled:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            log.info(f"RequestCache enabled (TTL={ttl}s, dir={CACHE_DIR})")
        else:
            log.info("RequestCache disabled (running in Apify production)")

    def _key_path(self, key: str) -> Path:
        h = hashlib.sha256(key.encode()).hexdigest()[:16]
        return CACHE_DIR / f"{h}.json"

    def get(self, key: str) -> dict | list | None:
        if not self._enabled:
            return None

        path = self._key_path(key)
        if not path.exists():
            return None

        try:
            envelope = json.loads(path.read_text())
            if time.time() - envelope["ts"] > self._ttl:
                path.unlink(missing_ok=True)
                return None
            log.debug(f"Cache HIT: {key[:50]}")
            return envelope["data"]
        except Exception:
            return None

    def set(self, key: str, data: dict | list) -> None:
        if not self._enabled:
            return

        path = self._key_path(key)
        try:
            path.write_text(json.dumps({"ts": time.time(), "data": data}))
            log.debug(f"Cache SET: {key[:50]}")
        except Exception as e:
            log.warning(f"Cache write failed: {e}")

    def clear(self) -> int:
        """Delete all cache files. Returns count of deleted files."""
        count = 0
        for f in CACHE_DIR.glob("*.json"):
            f.unlink(missing_ok=True)
            count += 1
        log.info(f"Cache cleared: {count} files deleted")
        return count

    def stats(self) -> dict:
        files = list(CACHE_DIR.glob("*.json"))
        total_bytes = sum(f.stat().st_size for f in files)
        return {
            "enabled": self._enabled,
            "entries": len(files),
            "size_kb": round(total_bytes / 1024, 1),
            "ttl_seconds": self._ttl,
        }


# Singleton
request_cache = RequestCache()