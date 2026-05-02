"""
USPTO Scraper
Primary:  PatentsView PatentSearch API (requires free API key)
          https://search.patentsview.org — 45 req/min
          Get a key: https://patentsview.org/apis/keyrequest

Fallback: Google Patents public search (no key, HTML scrape)

Set PATENTSVIEW_API_KEY in your .env file.
Without a key the scraper falls back to Google Patents automatically.

Proxy:   jurisdiction="US" via ProxyManager
Cache:   disk-based, 6h TTL, auto-disabled in Apify production
"""

import json
import logging
import asyncio
import hashlib
import os
import re
import sys

import httpx

_src = os.path.join(os.path.dirname(__file__), "..")
if _src not in sys.path:
    sys.path.insert(0, _src)

from proxy import proxy_manager
from cache import request_cache

log = logging.getLogger(__name__)

PATENTSVIEW_SEARCH = "https://search.patentsview.org/api/v1/patent/"
GOOGLE_PATENTS_SEARCH = "https://patents.google.com/xhr/query"

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
]

MAX_PAGE_SIZE = 100
MAX_RETRIES   = 3
RETRY_DELAY   = 2.0


async def scrape_uspto(query: str, date_from: str, max_results: int) -> list[dict]:
    """
    Scrape USPTO patents. Uses PatentsView API if PATENTSVIEW_API_KEY is set,
    otherwise falls back to Google Patents scraper.
    """
    api_key = os.getenv("PATENTSVIEW_API_KEY", "").strip()

    if api_key:
        log.info("USPTO: using PatentsView API (authenticated)")
        results = await _scrape_patentsview(query, date_from, max_results, api_key)
    else:
        log.warning(
            "USPTO: PATENTSVIEW_API_KEY not set — falling back to Google Patents. "
            "Get a free key at: https://patentsview.org/apis/keyrequest"
        )
        results = await _scrape_google_patents(query, date_from, max_results)

    for r in results:
        r["_source"]     = "USPTO"
        r["_source_api"] = "PatentsView" if api_key else "GooglePatents"

    return results


# ── PatentsView (authenticated) ───────────────────────────────────────────────

async def _scrape_patentsview(
    query: str,
    date_from: str,
    max_results: int,
    api_key: str,
) -> list[dict]:
    results: list[dict] = []
    offset    = 0
    page_size = min(MAX_PAGE_SIZE, max_results)

    while len(results) < max_results:
        payload   = _build_pv_payload(query, date_from, offset, page_size)
        cache_key = f"pv:{hashlib.md5(json.dumps(payload, sort_keys=True).encode()).hexdigest()}"

        data = request_cache.get(cache_key)
        if data is None:
            async with proxy_manager.client(
                jurisdiction="US",
                headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
            ) as client:
                data = await _fetch_json(client, PATENTSVIEW_SEARCH, payload=payload)
            if data:
                request_cache.set(cache_key, data)

        if not data:
            break

        patents = data.get("patents") or []
        if not patents:
            break

        results.extend(patents)
        log.debug(f"PatentsView offset={offset}: got {len(patents)}, total={len(results)}")

        total_hits = data.get("total_hits", 0)
        if offset + page_size >= total_hits or len(results) >= max_results:
            break

        offset += page_size
        await asyncio.sleep(1.4)   # 45 req/min = 1.33s between calls

    return results[:max_results]


def _build_pv_payload(query: str, date_from: str, offset: int, size: int) -> dict:
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


# ── Google Patents fallback (no key) ──────────────────────────────────────────

async def _scrape_google_patents(
    query: str,
    date_from: str,
    max_results: int,
) -> list[dict]:
    """
    Scrape Google Patents public search (no API key required).
    Uses the internal XHR endpoint that powers patents.google.com.
    Returns results in PatentsView-compatible dict shape.
    """
    results: list[dict] = []
    page      = 0
    page_size = min(10, max_results)   # Google Patents pages by 10

    # Format date for Google Patents: after:YYYYMMDD
    date_compact = date_from.replace("-", "")
    gp_query     = f"{query} after:{date_compact}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer":        "https://patents.google.com/",
        "Accept":         "application/json, text/javascript, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    while len(results) < max_results:
        params = {
            "query":  gp_query,
            "cursor": f"page:{page}",
            "num":    page_size,
            "start":  page * page_size,
            "country": "US",
            "language": "ENGLISH",
        }

        cache_key = f"gp:{hashlib.md5(str(params).encode()).hexdigest()}"
        data = request_cache.get(cache_key)

        if data is None:
            async with proxy_manager.client(jurisdiction="US", headers=headers) as client:
                data = await _fetch_json(
                    client, GOOGLE_PATENTS_SEARCH, params=params
                )
            if data:
                request_cache.set(cache_key, data)

        if not data:
            break

        batch = _parse_google_patents(data)
        if not batch:
            break

        results.extend(batch)
        log.debug(f"Google Patents page={page}: got {len(batch)}, total={len(results)}")

        # Google Patents caps at ~100 results per query
        if len(batch) < page_size or len(results) >= max_results:
            break

        page += 1
        await asyncio.sleep(1.0)

    return results[:max_results]


def _parse_google_patents(data: dict) -> list[dict]:
    """
    Parse Google Patents XHR JSON response into PatentsView-shaped dicts.
    The XHR response has a 'results' key with patent clusters.
    """
    patents: list[dict] = []
    try:
        clusters = data.get("results", {}).get("cluster", [])
        if not clusters:
            # Some responses have results directly
            items = data.get("results", {}).get("result", [])
            clusters = [{"result": items}] if items else []

        for cluster in clusters:
            for item in cluster.get("result", []):
                patent = item.get("patent", {})
                if not patent:
                    continue

                patent_id = patent.get("publication_number", "")
                # Normalise: "US-11256551-B2" → "US11256551B2"
                patent_id = patent_id.replace("-", "")

                title = patent.get("title", "")
                abstract = patent.get("abstract", "")

                # Inventors
                inventor_list = patent.get("inventor", [])
                if isinstance(inventor_list, str):
                    inventor_list = [{"inventor_first_name": "", "inventor_last_name": inventor_list}]
                elif isinstance(inventor_list, list):
                    parsed = []
                    for inv in inventor_list:
                        if isinstance(inv, str):
                            parts = inv.split(" ", 1)
                            parsed.append({
                                "inventor_first_name": parts[0] if parts else "",
                                "inventor_last_name":  parts[1] if len(parts) > 1 else "",
                            })
                        elif isinstance(inv, dict):
                            parsed.append({
                                "inventor_first_name": inv.get("name", "").split(" ", 1)[0],
                                "inventor_last_name":  inv.get("name", "").split(" ", 1)[-1],
                            })
                    inventor_list = parsed

                # Assignee
                assignee_raw = patent.get("assignee", [])
                if isinstance(assignee_raw, list) and assignee_raw:
                    assignee_org = assignee_raw[0] if isinstance(assignee_raw[0], str) else ""
                elif isinstance(assignee_raw, str):
                    assignee_org = assignee_raw
                else:
                    assignee_org = ""

                # CPC codes
                cpc_raw = patent.get("cpc", [])
                cpcs = [{"cpc_group": c} for c in (cpc_raw if isinstance(cpc_raw, list) else [])]

                patents.append({
                    "patent_id":              patent_id,
                    "patent_title":           title,
                    "patent_abstract":        abstract,
                    "patent_date":            patent.get("publication_date", ""),
                    "application_filing_date": patent.get("filing_date", ""),
                    "assignees": [{"assignee_organization": assignee_org}] if assignee_org else [],
                    "inventors": inventor_list,
                    "cpcs":      cpcs,
                })

    except Exception as e:
        log.warning(f"Google Patents parse error: {e}")

    return patents


# ── Shared HTTP helper ────────────────────────────────────────────────────────

async def _fetch_json(
    client: httpx.AsyncClient,
    url: str,
    payload: dict | None = None,
    params: dict | None  = None,
) -> dict | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            if payload is not None:
                resp = await client.post(url, json=payload)
            else:
                resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()

        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            log.warning(f"USPTO HTTP {status} (attempt {attempt}/{MAX_RETRIES})")

            if status == 403:
                log.error(
                    "USPTO 403 Forbidden — your PATENTSVIEW_API_KEY may be invalid or missing.\n"
                    "  → Get a free key at: https://patentsview.org/apis/keyrequest\n"
                    "  → Add it to your .env file: PATENTSVIEW_API_KEY=your_key_here"
                )
                return None

            if status == 429:
                wait = RETRY_DELAY * (2 ** attempt)
                log.info(f"Rate-limited — waiting {wait:.1f}s")
                await asyncio.sleep(wait)
            elif status in (500, 502, 503, 504):
                await asyncio.sleep(RETRY_DELAY * attempt)
            elif attempt == MAX_RETRIES:
                return None
            else:
                await asyncio.sleep(RETRY_DELAY)

        except (httpx.ConnectError, httpx.TimeoutException) as e:
            log.warning(f"Connection error (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt == MAX_RETRIES:
                return None
            await asyncio.sleep(RETRY_DELAY * attempt)

        except Exception as e:
            log.warning(f"Unexpected error (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt == MAX_RETRIES:
                return None
            await asyncio.sleep(RETRY_DELAY)

    return None


# ── Manual test ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    async def _test():
        results = await scrape_uspto("blockchain", "2022-01-01", 5)
        print(f"\nGot {len(results)} results (source: {results[0].get('_source_api') if results else 'n/a'})")
        for r in results:
            print(f"  {r.get('patent_id', '?'):20} | {r.get('patent_title', '')[:55]}")

    asyncio.run(_test())