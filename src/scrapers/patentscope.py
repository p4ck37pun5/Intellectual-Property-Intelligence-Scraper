"""
WIPO PATENTSCOPE Scraper
Primary:  RSS feed  (https://patentscope.wipo.int/search/en/rss.jsf)
Fallback: REST API  (https://patentscope.wipo.int/search/en/results.jsf)
Docs:     https://www.wipo.int/patentscope/en/data/api.html

Proxy:    jurisdiction="WO" → CH node via ProxyManager
Cache:    disk-based, 6h TTL, disabled in Apify production
"""

import asyncio
import hashlib
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from typing import Any

import httpx

_src = os.path.join(os.path.dirname(__file__), "..")
if _src not in sys.path:
    sys.path.insert(0, _src)

from proxy import proxy_manager
from cache import request_cache

log = logging.getLogger(__name__)

PS_RSS    = "https://patentscope.wipo.int/search/en/rss.jsf"
PS_SEARCH = "https://patentscope.wipo.int/search/en/search.jsf"
PS_DETAIL = "https://patentscope.wipo.int/search/en/detail.jsf"

# PATENTSCOPE REST API (JSON, newer endpoint)
PS_API_SEARCH = "https://patentscope.wipo.int/search/en/results.jsf"

MAX_RETRIES   = 3
RETRY_DELAY   = 2.5
MAX_PAGE_SIZE = 50

_USER_AGENT = (
    "Mozilla/5.0 (compatible; GlobalIPScraper/1.0; "
    "+https://apify.com/store)"
)


async def scrape_patentscope(query: str, date_from: str, max_results: int) -> list[dict]:
    """Scrape WIPO PATENTSCOPE — RSS primary, HTML fallback."""
    results: list[dict] = []
    page = 1
    page_size = min(MAX_PAGE_SIZE, max_results)

    while len(results) < max_results:
        batch = await _fetch_page(query, date_from, page, page_size)

        if not batch:
            # Attempt HTML fallback on first page failure
            if page == 1:
                log.info("PATENTSCOPE: RSS failed — trying HTML fallback")
                batch = await _html_fallback(query, date_from, page_size)
            if not batch:
                break

        results.extend(batch)
        log.debug(f"PATENTSCOPE page={page}: got {len(batch)}, total={len(results)}")

        if len(batch) < page_size:
            break  # Last page

        page += 1
        await asyncio.sleep(0.5)

    for r in results:
        r["_source"]     = "WIPO"
        r["_source_api"] = "PATENTSCOPE_RSS"

    return results[:max_results]


async def _fetch_page(
    query: str,
    date_from: str,
    page: int,
    page_size: int,
) -> list[dict]:
    params = {
        "query":     f"ALLFIELD:({query})",
        "office":    "WO",
        "dateFrom":  date_from,
        "pageSize":  page_size,
        "page":      page,
        "sortField": "pd",
        "sortOrder": "desc",
    }
    cache_key = f"wipo_rss:{hashlib.md5(str(params).encode()).hexdigest()}"

    # Cache check
    cached = request_cache.get(cache_key)
    if cached is not None:
        return cached if isinstance(cached, list) else []

    headers = {
        "User-Agent": _USER_AGENT,
        "Accept":     "application/rss+xml, application/xml, text/xml, */*",
    }

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with proxy_manager.client(
                jurisdiction="WO", headers=headers
            ) as client:
                resp = await client.get(PS_RSS, params=params, timeout=25.0)
                resp.raise_for_status()

            patents = _parse_rss(resp.text)
            request_cache.set(cache_key, patents)
            return patents

        except httpx.HTTPStatusError as e:
            log.warning(
                f"PATENTSCOPE RSS HTTP {e.response.status_code} "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )
            if attempt == MAX_RETRIES:
                return []
            await asyncio.sleep(RETRY_DELAY * attempt)

        except Exception as e:
            log.warning(f"PATENTSCOPE RSS error (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt == MAX_RETRIES:
                return []
            await asyncio.sleep(RETRY_DELAY)

    return []


def _parse_rss(xml_text: str) -> list[dict]:
    """Parse RSS/Atom XML → list of raw patent dicts."""
    patents: list[dict] = []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning(f"PATENTSCOPE RSS XML parse error: {e}")
        return patents

    # Namespace map
    ns = {
        "dc":   "http://purl.org/dc/elements/1.1/",
        "ps":   "http://www.wipo.int/patentscope/elements/1.0/",
        "atom": "http://www.w3.org/2005/Atom",
    }

    # Support both RSS <item> and Atom <entry>
    items = root.findall(".//item") or root.findall(".//atom:entry", ns)

    for item in items:
        def _get(tag: str) -> str:
            el = item.find(tag, ns)
            return (el.text or "").strip() if el is not None else ""

        title    = _get("title") or _get("atom:title")
        abstract = _get("description") or _get("atom:summary") or _get("atom:content")
        abstract = re.sub(r"<[^>]+>", "", abstract).strip()

        link = _get("link") or _get("atom:link")
        # Atom <link> may be an attribute
        if not link:
            link_el = item.find("atom:link", ns)
            if link_el is not None:
                link = link_el.get("href", "")

        pub_date = (
            _get("pubDate")
            or _get("dc:date")
            or _get("atom:published")
            or _get("atom:updated")
        )

        # Inventors / applicants from ps: namespace
        inventors   = [el.text.strip() for el in item.findall("ps:inventor", ns)   if el.text]
        applicants  = [el.text.strip() for el in item.findall("ps:applicant", ns)  if el.text]
        ipc_codes   = [el.text.strip() for el in item.findall("ps:ipcCode", ns)    if el.text]
        filing_date = _get("ps:filingDate")

        # Extract patent ID from link (e.g. ?docId=WO2022056789)
        patent_id = ""
        if link:
            m = re.search(r"docId=([A-Z0-9]+)", link)
            if m:
                patent_id = m.group(1)

        # Also try title prefix (e.g. "WO2022056789 - TITLE TEXT")
        if not patent_id and title:
            m = re.match(r"^(WO\d{4}\d+)", title)
            if m:
                patent_id = m.group(1)

        if not patent_id and not title:
            continue

        patents.append({
            "patent_id":     patent_id or f"WO_{len(patents) + 1}",
            "_wo_title":     re.sub(r"^WO\d+\s*[-–]\s*", "", title).strip(),
            "_wo_abstract":  abstract,
            "_wo_pub_date":  pub_date,
            "_wo_filing_date": filing_date,
            "_wo_link":      link,
            "_wo_inventors": inventors,
            "_wo_assignee":  applicants[0] if applicants else "",
            "_wo_ipcs":      ipc_codes,
        })

    return patents


async def _html_fallback(query: str, date_from: str, page_size: int) -> list[dict]:
    """Scrape PATENTSCOPE search HTML when RSS is blocked."""
    params = {
        "query":    query,
        "office":   "WO",
        "dateFrom": date_from,
        "pageSize": page_size,
    }
    cache_key = f"wipo_html:{hashlib.md5(str(params).encode()).hexdigest()}"
    cached = request_cache.get(cache_key)
    if cached is not None:
        return cached if isinstance(cached, list) else []

    try:
        async with proxy_manager.client(
            jurisdiction="WO",
            headers={"User-Agent": _USER_AGENT},
        ) as client:
            resp = await client.get(PS_SEARCH, params=params, timeout=25.0)
            resp.raise_for_status()

        patents = _parse_html(resp.text)
        request_cache.set(cache_key, patents)
        return patents

    except Exception as e:
        log.error(f"PATENTSCOPE HTML fallback failed: {e}")
        return []


def _parse_html(html: str) -> list[dict]:
    """
    Parse PATENTSCOPE search result HTML using parsel.
    Covers both the classic table layout and the newer card layout.
    """
    try:
        from parsel import Selector
    except ImportError:
        log.error("parsel not installed — HTML fallback unavailable")
        return []

    sel = Selector(html)
    patents: list[dict] = []

    # Classic table layout
    for row in sel.css("tr.resultRow"):
        patent_id = row.css("td.pn a::text").get("").strip()
        title     = row.css("td.title::text").get("").strip()
        pub_date  = row.css("td.pd::text").get("").strip()
        link      = row.css("td.pn a::attr(href)").get("").strip()

        if not patent_id and not title:
            continue

        patents.append({
            "patent_id":    patent_id or f"WO_html_{len(patents)}",
            "_wo_title":    title,
            "_wo_pub_date": pub_date,
            "_wo_link":     f"https://patentscope.wipo.int{link}" if link.startswith("/") else link,
            "_wo_abstract": "",
            "_wo_inventors": [],
            "_wo_assignee": "",
            "_wo_ipcs":     [],
        })

    # Newer card layout
    if not patents:
        for card in sel.css("div.ps-patent-result, li.patent-result"):
            patent_id = card.css(".pn::text, .patent-number::text").get("").strip()
            title     = card.css(".title::text, .patent-title::text").get("").strip()
            pub_date  = card.css(".pd::text, .pub-date::text").get("").strip()

            if not patent_id and not title:
                continue

            patents.append({
                "patent_id":    patent_id or f"WO_card_{len(patents)}",
                "_wo_title":    title,
                "_wo_pub_date": pub_date,
                "_wo_link":     "",
                "_wo_abstract": "",
                "_wo_inventors": [],
                "_wo_assignee": "",
                "_wo_ipcs":     [],
            })

    log.info(f"PATENTSCOPE HTML: parsed {len(patents)} patents")
    return patents


# ── Manual test ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    async def _test():
        results = await scrape_patentscope("CRISPR", "2022-01-01", 5)
        print(f"Got {len(results)} results")
        for r in results:
            print(f"  {r.get('patent_id'):25} | {r.get('_wo_title', '')[:55]}")

    asyncio.run(_test())