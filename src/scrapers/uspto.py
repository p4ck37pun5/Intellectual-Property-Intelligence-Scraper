"""
USPTO Scraper
Uses the PatentsView public REST API (no auth required).
Docs: https://search.patentsview.org/docs/docs/Search%20API/SearchAPIReference/

Proxy:   auto-rotated via ProxyManager (jurisdiction="US")
Cache:   disk-based, 6h TTL, disabled in Apify production
"""

import json
import logging
import asyncio
import hashlib
import sys
import os

import httpx

# Allow running directly (python3 src/scrapers/uspto.py) for manual testing
_src = os.path.join(os.path.dirname(__file__), "..")
if _src not in sys.path:
    sys.path.insert(0, _src)

from proxy import proxy_manager
from cache import request_cache

log = logging.getLogger(__name__)

PATENTSVIEW_SEARCH = "https://search.patentsview.org/api/v1/patent/"

FIELDS = [
    "patent_id",
    "patent_title",
    "patent_abstract",
    "patent_date",
    "patent_type",
    "application_filing_date",
    "patent_number",
    "assignees.assignee_organization",
    "assignees.assignee_first_name",
    "assignees.assignee_last_name",
    "inventors.inventor_first_name",
    "inventors.inventor_last_name",
    "cpcs.cpc_group",
    "uspc_at_issue.uspc_mainclass_id",
]

MAX_PAGE_SIZE = 100
MAX_RETRIES = 3
RETRY_DELAY = 2.0


async def scrape_uspto(query: str, date_from: str, max_results: int) -> list[dict]:
    """Scrape USPTO via PatentsView API with pagination, proxy rotation, and caching."""
    results: list[dict] = []
    offset = 0
    page_size = min(MAX_PAGE_SIZE, max_results)

    while len(results) < max_results:
        payload = _build_payload(query, date_from, offset, page_size)
        cache_key = f"uspto:{hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()}"

        # Try cache first (no-op in production)
        data = request_cache.get(cache_key)

        if data is None:
            async with proxy_manager.client(jurisdiction="US") as client:
                data = await _fetch_with_retry(client, payload)
            if data:
                request_cache.set(cache_key, data)

        if not data:
            break

        patents = data.get("patents") or []
        if not patents:
            break

        results.extend(patents)
        log.debug(f"USPTO offset={offset}: got {len(patents)}, total={len(results)}")

        total_hits = data.get("total_hits", 0)
        if offset + page_size >= total_hits or len(results) >= max_results:
            break

        offset += page_size
        await asyncio.sleep(0.3)

    for r in results:
        r["_source"] = "USPTO"
        r["_source_api"] = "PatentsView"

    return results[:max_results]


def _build_payload(query: str, date_from: str, offset: int, size: int) -> dict:
    return {
        "q": {
            "_and": [
                {"_text_any": {"patent_title": query, "patent_abstract": query}},
                {"_gte": {"patent_date": date_from}},
            ]
        },
        "f": FIELDS,
        "o": {"offset": offset, "size": size},
        "s": [{"patent_date": "desc"}],
    }


async def _fetch_with_retry(client: httpx.AsyncClient, payload: dict) -> dict | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.post(
                PATENTSVIEW_SEARCH,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            log.warning(f"USPTO HTTP {status} (attempt {attempt}/{MAX_RETRIES})")
            if status == 429:
                wait = RETRY_DELAY * (2 ** attempt)
                log.info(f"USPTO rate-limited — waiting {wait:.1f}s")
                await asyncio.sleep(wait)
            elif status in (500, 502, 503, 504):
                await asyncio.sleep(RETRY_DELAY * attempt)
            elif attempt == MAX_RETRIES:
                log.error(f"USPTO: giving up after {MAX_RETRIES} attempts (HTTP {status})")
                return None
            else:
                await asyncio.sleep(RETRY_DELAY)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            log.warning(f"USPTO connection error (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt == MAX_RETRIES:
                return None
            await asyncio.sleep(RETRY_DELAY * attempt)
        except Exception as e:
            log.warning(f"USPTO unexpected error (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt == MAX_RETRIES:
                return None
            await asyncio.sleep(RETRY_DELAY)

    return None


# ── Manual test ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    async def _test():
        results = await scrape_uspto("blockchain", "2022-01-01", 5)
        print(f"Got {len(results)} results")
        for r in results:
            print(f"  {r.get('patent_id')} | {r.get('patent_title', '')[:60]}")

    asyncio.run(_test())